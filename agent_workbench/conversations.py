"""Several local Agents answering in one conversation, one reply at a time.

A conversation owns a workspace directory and a list of members. Sending a
message queues one reply per member, then runs them serially through the
existing execution manager, so the same CLI, sandbox and log limits apply.
"""

from __future__ import annotations

import copy
import threading
import uuid
from pathlib import Path

from .execution import ACTIVE
from .runners import run_mode
from .storage import read_json, record_directory, record_roots, stamp, write_json
from .validation import identifier, text

MAX_MEMBERS = 6
MAX_MESSAGES = 400
CONTEXT_LIMIT = 12_000
TITLE_LIMIT = 60


def _short(value, limit):
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


class ConversationManager:
    def __init__(self, data: Path, runs):
        self.root = data.resolve() / "conversations"
        self.record_roots = record_roots(self.root)
        self.runs = runs
        self.lock = threading.RLock()
        self.active = None
        self.worker = None
        self.cancel_requested = threading.Event()
        self.closed = False
        self._recover()

    def _recover(self):
        """A reply that was running when the service exited is not resumed."""
        for path in self.root.glob("*/conversation.json"):
            try:
                record = read_json(path, {})
                changed = False
                for message in record.get("messages", []):
                    if message.get("status") in {"queued", "starting", "running", "cancelling"}:
                        message["status"] = "interrupted"
                        message["text"] = message.get("text") or "服务在回复完成前退出，这一轮没有结果。"
                        changed = True
                if changed:
                    record.update(status="interrupted", error="服务在回复完成前退出。", updated_at=stamp())
                    write_json(path, record)
            except (OSError, ValueError):
                continue

    def _path(self, conversation_id):
        return record_directory(self.root, identifier(conversation_id, "会话编号"), self.record_roots, "会话")

    def _read(self, conversation_id):
        record = read_json(self._path(conversation_id) / "conversation.json", None)
        if not isinstance(record, dict) or record.get("id") != conversation_id:
            raise LookupError("未找到会话")
        return record

    def _save(self, record):
        write_json(self._path(record["id"]) / "conversation.json", record)

    # ---- reads -----------------------------------------------------------
    def list(self):
        with self.lock:
            rows = []
            for path in self.root.glob("*/conversation.json"):
                try:
                    record = self._read(path.parent.name)
                except (OSError, ValueError, LookupError):
                    continue
                messages = record.get("messages", [])
                rows.append(
                    {
                        "id": record["id"],
                        "name": record.get("name") or "未命名会话",
                        "workspace": record.get("workspace") or "",
                        "members": record.get("members", []),
                        "updated_at": record.get("updated_at") or record.get("created_at"),
                        "created_at": record.get("created_at"),
                        "message_count": len(messages),
                        "preview": _short(
                            next((m.get("text") for m in reversed(messages) if m.get("text")), ""),
                            80,
                        ),
                        "running": record["id"] == self.active,
                    }
                )
            rows.sort(key=lambda row: row.get("updated_at") or "", reverse=True)
            return {"items": rows, "active": self.active}

    def get(self, conversation_id):
        with self.lock:
            record = copy.deepcopy(self._read(conversation_id))
            record["running"] = record["id"] == self.active
            record["task_list"] = self._tasks(record)
            return record

    @staticmethod
    def _tasks(record):
        """Assistant replies are the conversation's task list, newest last."""
        tasks = []
        for message in record.get("messages", []):
            if message.get("role") == "assistant":
                tasks.append(
                    {
                        "id": message["id"],
                        "member": message.get("member", ""),
                        "name": message.get("name", ""),
                        "status": message.get("status", ""),
                        "run_id": message.get("run_id", ""),
                        "created_at": message.get("created_at", ""),
                        "duration_ms": message.get("duration_ms"),
                        "error": message.get("error", ""),
                    }
                )
        return tasks[-30:]

    # ---- writes ----------------------------------------------------------
    def _members(self, value):
        if not isinstance(value, list) or not value or len(value) > MAX_MEMBERS:
            raise ValueError(f"请选择 1 到 {MAX_MEMBERS} 个会话成员")
        known = {runner["id"]: runner for runner in self.runs.runners()}
        members, seen = [], set()
        for item in value:
            raw = item if isinstance(item, dict) else {"id": item}
            agent = text(raw.get("id"), "成员编号", 40, required=True)
            runner = known.get(agent)
            if runner is None:
                raise ValueError("会话成员不是已接入的客户端")
            if not runner.get("available"):
                raise ValueError(f"{runner.get('name') or agent} 没有可执行的调用入口")
            if agent in seen:
                continue
            seen.add(agent)
            sandbox = text(raw.get("sandbox", run_mode(agent, "read-only")), "权限", 40)
            if sandbox not in {mode["id"] for mode in runner["modes"]}:
                raise ValueError(f"{runner.get('name') or agent} 不支持所选权限模式")
            members.append(
                {
                    "id": agent,
                    "name": runner.get("name") or agent,
                    "model": text(raw.get("model", ""), "模型", 160),
                    "sandbox": sandbox,
                }
            )
        return members

    def _workspace(self, value):
        path = Path(text(value, "工作区", 4096, required=True)).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("工作区必须是已存在的绝对目录")
        path = path.resolve()
        if path == self.root.parent.resolve() or self.root.parent.resolve() in path.parents:
            raise ValueError("请使用项目目录，不能把工坊数据目录作为工作区")
        return str(path)

    def create(self, body):
        with self.lock:
            if self.closed:
                raise ValueError("会话服务正在退出")
            if len(list(self.root.glob("*/conversation.json"))) >= 200:
                raise ValueError("会话数量已达上限，请先删除不用的会话")
            record = {
                "id": uuid.uuid4().hex,
                "name": text(body.get("name", ""), "会话名称", 80) or "新会话",
                "workspace": self._workspace(body.get("workspace")),
                "members": self._members(body.get("members")),
                "messages": [],
                "created_at": stamp(),
                "updated_at": stamp(),
                "status": "idle",
            }
            self._save(record)
            return self.get(record["id"])

    def update(self, body):
        conversation_id = identifier(body.get("id"), "会话编号")
        with self.lock:
            record = self._read(conversation_id)
            if record["id"] == self.active:
                raise ValueError("会话正在回复，请等待完成或取消后再修改")
            if "name" in body:
                record["name"] = text(body.get("name"), "会话名称", 80) or "新会话"
            if "workspace" in body:
                record["workspace"] = self._workspace(body.get("workspace"))
            if "members" in body:
                record["members"] = self._members(body.get("members"))
            record["updated_at"] = stamp()
            self._save(record)
            return self.get(conversation_id)

    def delete(self, conversation_id):
        conversation_id = identifier(conversation_id, "会话编号")
        with self.lock:
            record = self._read(conversation_id)
            if record["id"] == self.active:
                raise ValueError("会话正在回复，请等待完成或取消后再删除")
            path = self._path(conversation_id)
            for item in sorted(path.rglob("*"), reverse=True):
                if item.is_dir():
                    item.rmdir()
                else:
                    item.unlink()
            path.rmdir()
            return {"deleted": conversation_id}

    def message(self, body):
        conversation_id = identifier(body.get("id"), "会话编号")
        prompt = text(body.get("text"), "消息", 20_000, required=True)
        targets = body.get("members")
        if targets is not None and not isinstance(targets, list):
            raise ValueError("回复成员格式不正确")
        with self.lock:
            if self.closed:
                raise ValueError("会话服务正在退出")
            if self.active is not None:
                raise ValueError("会话正在回复，请等待完成或取消后再发送")
            record = self._read(conversation_id)
            if not record.get("members"):
                raise ValueError("请先选择会话成员")
            if not record.get("workspace"):
                raise ValueError("请先设置工作区")
            members = record["members"]
            if targets is not None:
                wanted = set()
                for item in targets:
                    value = item.get("id") if isinstance(item, dict) else item
                    wanted.add(text(value, "回复成员", 40, required=True))
                if not wanted or not wanted <= {member["id"] for member in members}:
                    raise ValueError("所选成员不在当前会话中")
                members = [member for member in members if member["id"] in wanted]
            if len(record["messages"]) + 1 + len(members) > MAX_MESSAGES:
                raise ValueError("会话消息已达上限，请新建会话继续")
            trusted_cwd = text(body.get("cursor_trusted_cwd", ""), "Cursor 信任目录", 4096)
            if trusted_cwd and not any(member["id"] == "cursor" for member in members):
                raise ValueError("本轮没有 Cursor 成员，无需 Cursor 信任确认")
            original = copy.deepcopy(record)
            now = stamp()
            user_message = {
                "id": uuid.uuid4().hex,
                "role": "user",
                "text": prompt,
                "status": "done",
                "created_at": now,
            }
            replies = [
                {
                    "id": uuid.uuid4().hex,
                    "role": "assistant",
                    "member": member["id"],
                    "name": member["name"],
                    "text": "",
                    "status": "queued",
                    "created_at": now,
                }
                for member in members
            ]
            record["messages"].extend([user_message, *replies])
            record["updated_at"] = now
            record["status"] = "running"
            record["error"] = ""
            self.runs.reserve(conversation_id)
            saving = False
            try:
                # Validate all selected members before persisting a turn or starting
                # the first CLI. A later invalid member must not leave partial work.
                for member, reply in zip(members, replies):
                    self.runs.validate(self._request(record, member, user_message, reply["id"], trusted_cwd))
                saving = True
                self._save(record)
                self._begin(record["id"], user_message["id"], [reply["id"] for reply in replies], trusted_cwd)
            except Exception:
                self.active = None
                self.worker = None
                try:
                    if saving:
                        self._save(original)
                finally:
                    self.runs.release(conversation_id)
                raise
            return self.get(conversation_id)

    def cancel(self, conversation_id):
        with self.lock:
            record = self._read(conversation_id)
            if record["id"] != self.active:
                return self.get(conversation_id)
            self.cancel_requested.set()
            record["status"] = "cancelling"
            for message in record["messages"]:
                if message.get("status") == "running" and message.get("run_id"):
                    try:
                        self.runs.cancel(message["run_id"])
                    except (OSError, ValueError, LookupError) as exc:
                        record["error"] = "取消时无法读取执行记录：" + str(exc)
            self._save(record)
            return self.get(conversation_id)

    # ---- execution -------------------------------------------------------
    def _begin(self, conversation_id, user_message_id, message_ids, trusted_cwd):
        self.active = conversation_id
        self.cancel_requested.clear()
        self.worker = threading.Thread(
            target=self._execute,
            args=(conversation_id, user_message_id, message_ids, trusted_cwd),
            name="agent-conversation",
            daemon=False,
        )
        self.worker.start()

    def _prompt(self, record, member, user_message):
        history = []
        for message in record["messages"]:
            if message["id"] == user_message["id"]:
                continue
            if message.get("role") == "user":
                history.append(f"用户：{message.get('text', '')}")
            elif message.get("status") == "succeeded" and message.get("text"):
                history.append(f"{message.get('name') or 'Agent'}：{message.get('text', '')}")
        others = "、".join(
            item["name"] for item in record["members"] if item["id"] != member["id"]
        )
        header = f"{_short(record.get('name'), TITLE_LIMIT)} · {member['name']}"
        prefix = "\n".join(
            [
                header,
                "",
                f"你是「{member['name']}」，在 Agent 工坊的会话里和用户"
                + (f"以及其他成员（{others}）" if others else "")
                + "一起讨论。请直接给出你的回复，不要复述任务；需要信息时说明缺什么。",
                "",
                "## 之前的对话",
            ]
        ) + "\n"
        suffix = "\n\n## 用户的新消息\n" + user_message["text"]
        budget = min(CONTEXT_LIMIT, 30_000 - len(prefix) - len(suffix))
        context = "\n\n".join(history) or "（这是第一条消息）"
        if len(context) > budget:
            notice = "[更早的对话已截取]\n"
            context = notice + context[-(budget - len(notice)):]
        return prefix + context + suffix

    def _request(self, record, member, user_message, reply_id, trusted_cwd):
        return {
            "request_id": uuid.uuid5(uuid.UUID(record["id"]), reply_id).hex,
            "agent": member["id"],
            "prompt": self._prompt(record, member, user_message),
            "cwd": record["workspace"],
            "sandbox": member["sandbox"],
            "model": member.get("model", ""),
            "timeout_minutes": record.get("timeout_minutes", 30),
            "cursor_trusted_cwd": trusted_cwd if member["id"] == "cursor" else "",
        }

    def _execute(self, conversation_id, user_message_id, message_ids, trusted_cwd):
        outcome, error = "succeeded", ""
        try:
            with self.lock:
                record = self._read(conversation_id)
                record["status"] = "running"
                self._save(record)
            for message_id in message_ids:
                with self.lock:
                    record = self._read(conversation_id)
                    message = next(
                        (item for item in record["messages"] if item["id"] == message_id), None
                    )
                    if message is None:
                        continue
                    if self.cancel_requested.is_set():
                        outcome = "cancelled"
                        break
                    member = next(
                        (item for item in record["members"] if item["id"] == message["member"]),
                        None,
                    )
                    user_message = next(item for item in record["messages"] if item["id"] == user_message_id)
                    if member is None:
                        message.update(status="error", error="成员已不在会话中")
                        self._save(record)
                        continue
                    try:
                        run = self.runs.start(
                            self._request(record, member, user_message, message_id, trusted_cwd),
                            owner=conversation_id,
                        )
                    except ValueError as exc:
                        message.update(status="error", error=str(exc))
                        self._save(record)
                        outcome, error = "failed", str(exc)
                        break
                    message.update(status="running", run_id=run["id"], started_at=stamp())
                    self._save(record)
                while True:
                    if self.cancel_requested.wait(0.15):
                        self.runs.cancel(run["id"])
                        if self.runs.worker:
                            self.runs.worker.join(timeout=1)
                    run = self.runs.get(run["id"])
                    if run["status"] not in ACTIVE:
                        break
                with self.lock:
                    record = self._read(conversation_id)
                    message = next(
                        (item for item in record["messages"] if item["id"] == message_id), None
                    )
                    if message is not None:
                        message["status"] = run["status"]
                        message["error"] = run.get("error", "")
                        message["text"] = run.get("result", "") or run.get("error", "")
                        message["duration_ms"] = _elapsed(message.get("started_at"))
                        message["result_path"] = str(Path(run.get("directory", "")) / "result.md")
                        self._save(record)
                if run["status"] != "succeeded" or self.cancel_requested.is_set():
                    outcome = "cancelled" if self.cancel_requested.is_set() else "failed"
                    error = run.get("error") or run["status"]
                    break
        except Exception as exc:  # noqa: BLE001 - surface a readable message
            outcome = "failed"
            error = str(exc) if isinstance(exc, ValueError) else f"会话执行失败：{type(exc).__name__}"
        finally:
            try:
                with self.runs.lock:
                    active_run = self.runs.active if self.runs.owner == conversation_id else None
                if active_run:
                    try:
                        self.runs.cancel(active_run)
                    finally:
                        if self.runs.worker:
                            self.runs.worker.join(timeout=20)
            except Exception as exc:  # Cleanup must never skip the ownership release below.
                outcome, error = "failed", "清理执行记录失败：" + str(exc)
            with self.lock:
                try:
                    record = self._read(conversation_id)
                    for message in record["messages"]:
                        if message["id"] in message_ids and message.get("status") in {"queued", "running"}:
                            message["status"] = "cancelled" if outcome == "cancelled" else "error"
                            if not message.get("text"):
                                message["text"] = message.get("error") or "这一轮没有返回结果。"
                    record["status"] = outcome
                    record["error"] = error
                    record["updated_at"] = stamp()
                    self._save(record)
                finally:
                    self.active = None
                    self.runs.release(conversation_id)

    def close(self):
        with self.lock:
            self.closed = True
            if self.active:
                self.cancel(self.active)
            worker = self.worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=25)


def _elapsed(started_at):
    if not started_at:
        return None
    try:
        from datetime import datetime

        start = datetime.fromisoformat(started_at)
        return int((datetime.now().astimezone() - start).total_seconds() * 1000)
    except (ValueError, TypeError):
        return None
