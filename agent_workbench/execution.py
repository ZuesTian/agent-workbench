"""Owned CLI execution with durable records, cancellation and scheduling leases."""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .execution_process import ProcessTree
from .runners import (
    RunnerRegistry, RUNNERS, prepare_command, decode_event, execution_environment,
    cursor_workspace_untrusted, run_mode,
)
from .storage import read_json, record_directory, record_roots, stamp, write_json
from .validation import identifier, text

ACTIVE = {"starting", "running", "cancelling"}
TRUST_PROMPT = "Cursor 要求确认工作目录。请核对目录后勾选“信任此工作目录（Cursor 将记住）”，再重新启动。"


def paginate(rows, query, page, size, text_of):
    """Return one searchable page without dropping older records on disk."""
    words = [part.casefold() for part in (query or "").split() if part]
    matched = [row for row in rows if all(word in text_of(row).casefold() for word in words)]
    size = 20 if type(size) is not int else min(max(size, 1), 50)
    pages = max(1, (len(matched) + size - 1) // size)
    page = 1 if type(page) is not int else min(max(page, 1), pages)
    start = (page - 1) * size
    return {
        "items": matched[start:start + size],
        "total": len(matched),
        "page": page,
        "pages": pages,
        "page_size": size,
        "query": query or "",
    }


def record_text(row):
    created = str(row.get("created_at") or "")
    return " ".join(
        str(row.get(key) or "")
        for key in ("title", "cwd", "agent", "id", "status")
    ) + " " + created + " " + created.replace("-", "/").replace("T", " ")
LOG_LIMIT = 16 * 1024 * 1024
VIEW_LIMIT = 160_000
EVENT_LIMIT = 8 * 1024 * 1024


class OutputTooLarge(ValueError):
    """One stdout or stderr record exceeded the explicit parse limit."""


def read_stream_records(stream, limit=EVENT_LIMIT):
    """Yield complete records, including a final chunk that has no newline."""
    pending = bytearray()
    # BufferedReader.read(n) waits for n bytes or EOF on a live pipe. read1
    # returns available bytes after one raw read so flushed events stay live.
    read_chunk = getattr(stream, "read1", stream.read)
    while True:
        chunk = read_chunk(64 * 1024)
        if not chunk:
            if pending:
                if len(pending) > limit:
                    raise OutputTooLarge("单条输出超过 8 MB，已停止解析")
                yield bytes(pending)
            return
        pending.extend(chunk)
        while True:
            newline = pending.find(b"\n")
            if newline < 0:
                break
            if newline + 1 > limit:
                raise OutputTooLarge("单条输出超过 8 MB，已停止解析")
            record = bytes(pending[: newline + 1])
            del pending[: newline + 1]
            yield record
        if len(pending) > limit:
            raise OutputTooLarge("单条输出超过 8 MB，已停止解析")


def codex_command() -> list[str] | None:
    """Resolve an executable, never run a user-supplied command or shell text."""
    candidate = shutil.which("codex.exe" if os.name == "nt" else "codex")
    if candidate and Path(candidate).is_file():
        return [str(Path(candidate).resolve())]
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    candidates = list((local / "OpenAI/Codex/bin").glob("*/codex.exe"))
    if candidates:
        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        return [str(latest.resolve())]
    # npm installations can expose a .cmd shim; invoke its packaged JS through
    # Node directly instead of handing user text to cmd.exe.
    roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    script = roaming / "npm/node_modules/@openai/codex/bin/codex.js"
    node = shutil.which("node")
    if node and script.is_file():
        return [str(Path(node).resolve()), str(script)]
    return None


def event_text(event):
    kind = str(event.get("type") or "")
    item = event.get("item") or {}
    if not isinstance(item, dict):
        item = {}
    if kind.startswith("item."):
        item_kind = item.get("type", "")
        if item_kind == "agent_message":
            return item.get("text", "")
        if item_kind == "command_execution":
            return f"{item.get('command', '')}\n{item.get('aggregated_output', '')}"
        if item_kind == "file_change":
            return "文件变更：" + json.dumps(item.get("changes", []), ensure_ascii=False)
        if item_kind == "mcp_tool_call":
            return f"MCP：{item.get('server', '')} / {item.get('tool', '')} · {item.get('status', kind)}"
        if item_kind == "error":
            return str(item.get("message") or item.get("text") or item.get("error") or "客户端报告错误")
        if item_kind == "reasoning":
            return ""  # Keep the execution log focused on user-facing work.
        return f"{item_kind or '事件'} · {kind}"
    if kind == "thread.started":
        return "会话已建立"
    if kind == "turn.started":
        return "Agent 开始处理任务"
    if kind == "turn.completed":
        return "Agent 已返回完成事件"
    if kind in ("turn.failed", "error"):
        error = event.get("error", event.get("message", "Agent 返回错误"))
        return error.get("message", str(error)) if isinstance(error, dict) else str(error)
    return kind


def _same_request(existing, request):
    return all(existing.get(key) == value for key, value in request.items())


def _only_added_trust(existing, request):
    """The retry confirms this Cursor workspace and leaves every other field unchanged."""
    trusted = request.get("cursor_trusted_cwd") or ""
    if existing.get("agent") != "cursor" or not trusted or trusted != request.get("cwd"):
        return False
    if existing.get("cursor_trusted_cwd"):
        return False
    return all(
        existing.get(key) == value for key, value in request.items() if key != "cursor_trusted_cwd"
    )


def _failed_for_workspace_trust(existing):
    return existing.get("status") == "failed" and (
        existing.get("trust_required") is True or TRUST_PROMPT in str(existing.get("error") or "")
    )


class ExecutionManager:
    def __init__(self, data: Path, resolver=codex_command, *, registry=None):
        self.root = data.resolve() / "runs"
        self.record_roots = record_roots(self.root)
        self.registry = registry or RunnerRegistry(resolver)
        self.lock = threading.RLock()
        self.active = None
        self.worker = None
        self.cancel_requested = threading.Event()
        self.closed = False
        self.owner = None
        # Never trust a stale PID or re-run a user's task after a restart.
        for path in self.root.glob("*/run.json"):
            try:
                record = read_json(path, {})
                if record.get("status") in ACTIVE:
                    record.update(
                        status="interrupted", finished_at=stamp(),
                        error="管理服务在任务完成前退出；请检查工作目录后再决定是否重试。",
                    )
                    write_json(path, record)
            except (OSError, ValueError):
                pass
        self._clear_zcode_overrides()

    def _clear_zcode_overrides(self):
        """Remove model-selection copies left behind when a previous process died."""
        for path in self.root.glob("*/zcode-model.json"):
            try:
                directory = self._path(path.parent.name)
                if path.is_symlink() or path.resolve().parent != directory.resolve():
                    continue
                path.unlink()
            except (OSError, ValueError):
                continue

    def runners(self):
        return self.registry.list()

    def reserve(self, owner):
        with self.lock:
            if self.closed:
                raise ValueError("执行服务正在退出")
            if self.active is not None or self.owner is not None:
                raise ValueError("已有任务或协作调度运行中")
            self.owner = owner

    def release(self, owner):
        with self.lock:
            if self.owner == owner:
                self.owner = None

    def _path(self, run_id):
        run_id = identifier(run_id, "任务编号")
        return record_directory(self.root, run_id, self.record_roots, "任务")

    def _record(self, run_id):
        record = read_json(self._path(run_id) / "run.json", None)
        if not isinstance(record, dict) or record.get("id") != run_id:
            raise LookupError("未找到任务")
        if record.get("status") in ACTIVE and run_id != self.active:
            record.update(status="interrupted", error="执行线程已结束，但结束状态未能保存；请检查执行日志。")
        return record

    def list(self):
        with self.lock:
            records = []
            for path in self.root.glob("*/run.json"):
                try:
                    record = self._record(path.parent.name)
                    records.append({
                        k: v for k, v in record.items()
                        if k not in {"prompt", "command"}
                    })
                except (OSError, ValueError, LookupError):
                    continue
            return sorted(records, key=lambda x: x["created_at"], reverse=True)

    def page(self, query="", page=1, size=20, kind="all"):
        with self.lock:
            rows = self.list()
        if kind not in {"all", "task", "step"}:
            kind = "all"
        selected = [
            row for row in rows
            if kind == "all"
            or (kind == "step" and row.get("workflow_id"))
            or (kind == "task" and not row.get("workflow_id"))
        ]
        result = paginate(selected, query, page, size, record_text)
        result["active"] = sum(row.get("status") in ACTIVE for row in rows)
        result["kind"] = kind
        return result

    @staticmethod
    def _tail(path, limit):
        if not path.is_file():
            return "", False
        with path.open("rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            stream.seek(max(0, size - limit))
            data = stream.read(limit)
        return data.decode("utf-8", errors="replace"), size > limit

    def get(self, run_id):
        with self.lock:
            record = copy.deepcopy(self._record(run_id))
            directory = self._path(run_id)
            log, truncated = self._tail(directory / "execution.log", VIEW_LIMIT)
            result, result_truncated = self._tail(directory / "result.md", 1_000_000)
            record.pop("command", None)
            return {
                **record, "log": log, "log_truncated": truncated,
                "result": result, "result_truncated": result_truncated,
                "directory": str(directory),
            }

    def validate(self, body):
        run_id = identifier(body.get("request_id"), "请求编号")
        agent = body.get("agent", "codex")
        if not isinstance(agent, str):
            raise ValueError("客户端编号格式不正确")
        prompt = text(body.get("prompt"), "任务", 30_000, required=True)
        cwd = Path(text(body.get("cwd"), "工作目录", 4096, required=True)).expanduser()
        if not cwd.is_absolute() or not cwd.is_dir():
            raise ValueError("工作目录必须是已存在的绝对目录")
        cwd = cwd.resolve()
        if any(cwd == root.parent or root.parent in cwd.parents for root in self.record_roots):
            raise ValueError("请使用项目目录，不能把执行记录目录作为工作目录")
        trusted_cwd = text(body.get("cursor_trusted_cwd", ""), "Cursor 信任目录", 4096)
        if trusted_cwd:
            trusted_path = Path(trusted_cwd).expanduser()
            if agent != "cursor" or not trusted_path.is_absolute() or trusted_path.resolve() != cwd:
                raise ValueError("Cursor 信任确认必须对应本次工作目录；请重新确认目录")
            trusted_cwd = str(cwd)
        sandbox = body.get("sandbox", run_mode(agent, "read-only"))
        model = text(body.get("model", ""), "模型", 160)
        timeout = body.get("timeout_minutes", 30)
        if type(timeout) is not int or not 1 <= timeout <= 120:
            raise ValueError("超时时间需为 1 至 120 分钟的整数")
        request = dict(
            agent=agent, prompt=prompt, cwd=str(cwd), sandbox=sandbox,
            model=model, timeout_minutes=timeout, cursor_trusted_cwd=trusted_cwd,
        )
        prefix = self.registry.validate(agent, sandbox, model)
        return run_id, request, prefix

    def _ensure_available(self, owner):
        if self.closed:
            raise ValueError("执行服务正在退出")
        if self.active is not None:
            raise ValueError("已有任务运行中，请等待完成或取消后再启动")
        if self.owner != owner:
            raise ValueError("协作调度占用执行入口，请等待完成或取消调度")

    def _spawn(self, record, prefix):
        self.active = record["id"]
        try:
            # Validate/read before launching a worker; roll back ownership on failure.
            snapshot = self.get(record["id"])
            self.cancel_requested.clear()
            self.worker = threading.Thread(
                target=self._execute, args=(record, prefix),
                name="agent-execution", daemon=False,
            )
            self.worker.start()
        except Exception:
            self.active = None
            self.worker = None
            self._update(record["id"], status="failed", finished_at=stamp(), error="无法启动执行线程。")
            raise
        return snapshot

    def start(self, body, *, owner=None):
        run_id, request, prefix = self.validate(body)
        with self.lock:
            directory = self._path(run_id)
            if (directory / "run.json").is_file():
                existing = self._record(run_id)
                same_owner = existing.get("workflow_id") == owner
                if same_owner and _same_request(existing, request):
                    return self.get(run_id)
                # A finished step can keep its result when a later retry only adds trust.
                if same_owner and existing.get("status") == "succeeded" and _only_added_trust(existing, request):
                    return self.get(run_id)
                if not (same_owner and _failed_for_workspace_trust(existing) and _only_added_trust(existing, request)):
                    raise ValueError("同一个请求编号不能提交不同任务")
                self._ensure_available(owner)
                existing.update(
                    cursor_trusted_cwd=request["cursor_trusted_cwd"],
                    status="starting", started_at=None, finished_at=None,
                    exit_code=None, error="", trust_required=False,
                )
                write_json(directory / "run.json", existing)
                (directory / "execution.log").write_bytes(b"")
                (directory / "result.md").unlink(missing_ok=True)
                return self._spawn(existing, prefix)
            self._ensure_available(owner)
            directory.mkdir(exist_ok=False)
            record = {
                **request, "id": run_id, "title": request["prompt"].splitlines()[0][:80],
                "created_at": stamp(), "started_at": None, "finished_at": None,
                "status": "starting", "exit_code": None, "error": "", "trust_required": False,
                "thread_id": None, "usage": None, "workflow_id": owner,
            }
            write_json(directory / "run.json", record)
            (directory / "prompt.txt").write_text(request["prompt"], encoding="utf-8")
            return self._spawn(record, prefix)

    def cancel(self, run_id):
        with self.lock:
            # A damaged/unreadable record must not prevent stopping its process.
            if run_id == self.active:
                self.cancel_requested.set()
            record = self._record(run_id)
            if run_id == self.active and record["status"] in ACTIVE:
                self.cancel_requested.set()
                record["status"] = "cancelling"
                write_json(self._path(run_id) / "run.json", record)
            return self.get(run_id)

    def _update(self, run_id, **changes):
        with self.lock:
            record = self._record(run_id)
            record.update(changes)
            write_json(self._path(run_id) / "run.json", record)

    def _execute(self, record, prefix):
        try:
            self._run(record, prefix)
        except Exception as exc:  # Includes setup, logging and final persistence.
            logging.exception("Execution worker failed for %s", record["id"])
            try:
                self._update(record["id"], status="failed", finished_at=stamp(),
                             error="执行记录处理失败：" + type(exc).__name__)
            except (OSError, ValueError, LookupError):
                logging.exception("Could not persist failed execution %s", record["id"])
        finally:
            with self.lock:
                if self.active == record["id"]:
                    self.active = None

    def _run(self, record, prefix):
        run_id = record["id"]
        directory = self._path(run_id)
        tree = None
        readers = []
        completed, failed = threading.Event(), threading.Event()
        trust_required = threading.Event()
        oversized = threading.Event()
        outcome, error, returncode = "failed", "", None
        needs_trust = False
        log_lock = threading.Lock()
        bytes_written = 0
        log_full = False
        result_path = directory / "result.md"
        agent_name = RUNNERS[record["agent"]]["name"]

        def log(message, source=""):
            nonlocal bytes_written, log_full
            if not message:
                return
            payload = (f"[{stamp()}] {source} {str(message)[:24_000]}\n").encode("utf-8")
            with log_lock:
                if bytes_written >= LOG_LIMIT:
                    if log_full:
                        return
                    payload = "\n日志达到 16 MB，后续日志省略；任务状态仍持续更新。\n".encode("utf-8")
                    log_full = True
                with (directory / "execution.log").open("ab") as output:
                    output.write(payload)
                bytes_written += len(payload)

        def consume(stream, source):
            try:
                for raw in read_stream_records(stream):
                    line = raw.decode("utf-8", errors="replace").strip()
                    if record["agent"] == "cursor" and cursor_workspace_untrusted(line):
                        trust_required.set()
                    if source == "stderr":
                        log(line, source)
                        continue
                    try:
                        event = json.loads(line) if line else None
                    except ValueError:
                        log(line, source)
                        continue
                    if not isinstance(event, dict):
                        log(line, source)
                        continue
                    change = decode_event(record["agent"], event)
                    if change.get("completed"):
                        completed.set()
                    if change.get("failed"):
                        failed.set()
                    if "result" in change:
                        if not isinstance(change["result"], str):
                            raise ValueError("客户端结果格式不正确")
                        result_path.write_text(change["result"], encoding="utf-8")
                    metadata = {k: change[k] for k in ("thread_id", "usage") if k in change}
                    if metadata:
                        self._update(run_id, **metadata)
                    log(event_text(event) if record["agent"] == "codex" else change.get("log"), agent_name)
            except OutputTooLarge as exc:
                oversized.set()
                failed.set()
                log(str(exc), "工坊")
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                failed.set()
                log("读取执行输出失败：" + type(exc).__name__, "工坊")
            finally:
                stream.close()

        try:
            environment = execution_environment()
            command = prepare_command(prefix, record, directory, environment)
            # Record launch settings without environment values or credentials.
            runtime = {
                "client": record["agent"], "entrypoints": [],
                "requested_model": record["model"] or None,
                "model_source": "explicit" if record["model"] else "client_default",
                "cwd": record["cwd"], "permission": record["sandbox"],
            }
            for value in prefix:
                entry = Path(value)
                if entry.is_file():
                    info = entry.stat()
                    runtime["entrypoints"].append({"path": str(entry.resolve()), "bytes": info.st_size,
                                                    "modified_ns": str(info.st_mtime_ns)})
            self._update(run_id, runtime=runtime)
            log(f"启动 {agent_name}；工作目录：{record['cwd']}；权限：{record['sandbox']}", "工坊")
            if record.get("cursor_trusted_cwd"):
                log("已收到此工作目录的信任确认；Cursor 将保存目录信任，工具操作仍按客户端权限处理。", "工坊")
            with (directory / "prompt.txt").open("rb") as prompt:
                with self.lock:
                    if self.cancel_requested.is_set() or self.closed:
                        outcome = "cancelled"
                        return
                    tree = ProcessTree(
                        command, record["cwd"], prompt, subprocess.PIPE, subprocess.PIPE,
                        env=environment,
                    )
                    self._update(run_id, status="running", started_at=stamp())
                for source in ("stdout", "stderr"):
                    reader = threading.Thread(
                        target=consume, args=(getattr(tree.process, source), source),
                        daemon=True,
                    )
                    reader.start()
                    readers.append(reader)
                deadline = time.monotonic() + record["timeout_minutes"] * 60
                while tree.process.poll() is None:
                    if self.cancel_requested.wait(0.15):
                        # A cancellation can arrive after the process exited while
                        # this wait was sleeping. Preserve that completed result.
                        if tree.process.poll() is None:
                            outcome = "cancelled"
                            tree.stop()
                        break
                    if time.monotonic() >= deadline:
                        if tree.process.poll() is None:
                            outcome, error = "timed_out", "任务超过设定时间，已终止执行。"
                            tree.stop()
                        break
                returncode = tree.process.wait(timeout=10)
                tree.close()
                tree = None
                for reader in readers:
                    reader.join(timeout=5)
                if outcome not in {"cancelled", "timed_out"}:
                    if oversized.is_set():
                        error = "单条输出超过 8 MB，已停止解析。"
                    elif returncode == 0 and completed.is_set() and not failed.is_set():
                        outcome = "succeeded"
                    elif trust_required.is_set():
                        needs_trust = True
                        error = TRUST_PROMPT
                    else:
                        error = f"{agent_name} 未正常完成（退出码 {returncode}）；请查看执行日志与客户端模型/登录配置。"
        except Exception as exc:
            error = "无法完成执行：" + type(exc).__name__ + f"。请查看 {agent_name} 安装、登录与目录权限。"
            log(error, "工坊")
        finally:
            if tree is not None:
                try:
                    tree.stop()
                    tree.process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    outcome = "failed"
                    error = "未能确认执行进程已结束，请检查当前任务日志。"
                finally:
                    tree.close()
            if record["agent"] == "zcode" and record["model"]:
                try:
                    (directory / "zcode-model.json").unlink(missing_ok=True)
                except OSError:
                    log("本次模型配置临时文件未能移除。", "工坊")
            log(f"执行结束：{outcome}。取消或失败不会回滚已发生的文件修改。", "工坊")
            with self.lock:
                try:
                    self._update(
                        run_id, status=outcome, exit_code=returncode,
                        finished_at=stamp(), error=error, trust_required=needs_trust,
                    )
                finally:
                    if self.active == run_id:
                        self.active = None

    def close(self):
        with self.lock:
            self.closed = True
            self.cancel_requested.set()
            worker = self.worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=20)
