"""Serial, dependency-ordered collaboration runs over immutable team snapshots."""

from __future__ import annotations

import copy
import threading
import uuid
from pathlib import Path

from .execution import ACTIVE, TRUST_PROMPT, paginate, record_text
from .runners import run_mode
from .storage import read_json, write_json, stamp
from .teams import blueprint
from .validation import identifier, text


PHASES = {"assign": "分派", "deliver": "执行", "review": "审核汇总"}


def execution_steps(team):
    steps = []

    def visit(node):
        children = [item for item in team["nodes"] if item["parentId"] == node["id"]]
        if children:
            steps.append({"node_id": node["id"], "phase": "assign"})
        for child in children:
            visit(child)
        steps.append({
            "node_id": node["id"],
            "phase": "review" if children or node["role"] == "tutor" else "deliver",
        })

    for node in team["nodes"]:
        if node["parentId"] is None:
            visit(node)
    return steps


def _workflow_fields(record):
    return {key: record.get(key) for key in ("team", "cwd", "permission", "timeout_minutes", "cursor_trusted_cwd")}


def _same_workflow(existing, request):
    return _workflow_fields(existing) == _workflow_fields(request)


def _workflow_trust_retry(existing, request):
    """A failed collaboration may be resubmitted when the only change is Cursor trust."""
    trusted = request.get("cursor_trusted_cwd") or ""
    if existing.get("status") != "failed" or existing.get("cursor_trusted_cwd") or not trusted:
        return False
    if trusted != request.get("cwd"):
        return False
    unchanged = ("team", "cwd", "permission", "timeout_minutes")
    if any(existing.get(key) != request.get(key) for key in unchanged):
        return False
    return existing.get("trust_required") is True or TRUST_PROMPT in str(existing.get("error") or "")


def clipped(value, limit):
    return value if len(value) <= limit else value[:limit] + "\n[内容已截取；完整内容见对应执行记录。]"


def step_prompt(workflow, step, previous):
    team = workflow["team"]
    node = next(item for item in team["nodes"] if item["id"] == step["node_id"])
    phase = step["phase"]
    children = [item for item in team["nodes"] if item["parentId"] == node["id"]]
    instructions = {
        "assign": "请将总目标分解为下列成员的具体任务、交付物和验收条件。本步只输出分派说明；调度器会逐一执行成员。",
        "deliver": "完成本节点职责，结合已有交接结果开展工作。交付结果、产物路径、验证依据和未解决问题。",
        "review": "审核已有成果与验证依据，汇总当前节点的交付。明确列出未完成项或验收问题；不要把执行完成等同于质量通过。",
    }
    lines = [
        f"# {team['name']} / {node['name']} / {PHASES[phase]}",
        "", "## 总目标", team["goal"], "", "## 本节点职责", node["prompt"],
        "", "## 当前步骤", instructions[phase], "", "## 审核与汇报要求", node["policy"],
        "", "工作目录：" + workflow["cwd"],
        "调度器负责后续成员调用。请只完成本步，不自行启动其他 Agent 或创建定时任务。",
    ]
    if children:
        lines += ["", "## 下级分工"] + [
            f"- {child['name']}：{clipped(child['prompt'], 500)}" for child in children
        ]
    if node["skills"] or node["mcps"]:
        lines += [
            "", "## 方案中的资源引用",
            "这些引用不会自动安装或授予权限；仅使用本客户端当前实际可用的资源。缺失时请说明。",
            "Skills：" + "、".join(ref["name"] for ref in node["skills"]),
            "MCP：" + "、".join(ref["name"] for ref in node["mcps"]),
        ]
    base = clipped("\n".join(lines), 17_000)
    # Recent results first, with each result's source and full on-disk location.
    # Earlier assignments remain visible through a compact history index.
    handoffs = []
    remaining = 11_000
    for item in reversed(previous):
        entry = (
            f"\n### {item['name']} / {PHASES[item['phase']]}\n"
            f"执行记录：{item['run_id']}\n完整结果：{item['path']}\n"
            + clipped(item["result"], min(remaining, 5000))
        )
        if len(entry) > remaining:
            break
        handoffs.append(entry)
        remaining -= len(entry)
    return base + "\n\n## 已完成步骤的交接结果（按执行顺序）\n" + (
        "\n".join(reversed(handoffs)) or "暂无，当前为首个步骤。"
    )


class WorkflowManager:
    def __init__(self, data: Path, runs):
        self.root = data.resolve() / "workflows"
        self.root.mkdir(parents=True, exist_ok=True)
        self.runs = runs
        self.lock = threading.RLock()
        self.active = None
        self.worker = None
        self.closed = False
        self.cancel_requested = threading.Event()
        for path in self.root.glob("*/workflow.json"):
            try:
                record = read_json(path, {})
                if record.get("status") in ACTIVE:
                    record.update(status="interrupted", finished_at=stamp(),
                                  error="服务在调度完成前退出。任务不会自动重跑，请先检查已有产物。")
                    for step in record["steps"]:
                        if step["status"] in {"waiting", "starting", "running"}:
                            step["status"] = "interrupted"
                    write_json(path, record)
            except (OSError, ValueError, KeyError):
                pass

    def _path(self, workflow_id):
        path = self.root / identifier(workflow_id, "调度编号")
        if path.is_symlink() or self.root.resolve() not in path.resolve().parents:
            raise ValueError("调度记录路径已被重定向")
        return path

    def _read(self, workflow_id):
        record = read_json(self._path(workflow_id) / "workflow.json", None)
        if not isinstance(record, dict) or record.get("id") != workflow_id:
            raise LookupError("未找到协作执行记录")
        return record

    def _save(self, record):
        write_json(self._path(record["id"]) / "workflow.json", record)

    def list(self):
        with self.lock:
            rows = []
            for path in self.root.glob("*/workflow.json"):
                try:
                    record = self._read(path.parent.name)
                    rows.append({key: record[key] for key in (
                        "id", "title", "status", "created_at", "finished_at", "steps", "error",
                    )})
                    rows[-1]["cwd"] = record.get("cwd") or ""
                except (ValueError, OSError, LookupError, KeyError):
                    continue
            return sorted(rows, key=lambda row: row["created_at"], reverse=True)

    def page(self, query="", page=1, size=20):
        with self.lock:
            rows = self.list()
        result = paginate(rows, query, page, size, record_text)
        result["active"] = sum(row.get("status") in ACTIVE for row in rows)
        return result

    def get(self, workflow_id):
        with self.lock:
            record = copy.deepcopy(self._read(workflow_id))
            directory = self._path(workflow_id)
            summary, truncated = self.runs._tail(directory / "summary.md", 1_000_000)
            return {**record, "result": summary, "result_truncated": truncated, "directory": str(directory)}

    def start(self, body):
        workflow_id = identifier(body.get("request_id"), "请求编号")
        team = blueprint(body.get("team"))
        permission = body.get("permission", "read-only")
        if not isinstance(permission, str) or permission not in {"read-only", "edit"}:
            raise ValueError("协作权限需为只读或允许编辑")
        if not team["goal"].strip():
            raise ValueError("请先填写协作总目标")
        trusted_cwd = text(body.get("cursor_trusted_cwd", ""), "Cursor 信任目录", 4096)
        if trusted_cwd and not any(node["agentId"] == "cursor" for node in team["nodes"]):
            raise ValueError("当前协作没有 Cursor 节点，无需 Cursor 信任确认")
        errors = []
        cwd = None
        for node in team["nodes"]:
            try:
                _, request, _ = self.runs.validate({
                    "request_id": workflow_id, "agent": node["agentId"],
                    "prompt": node["prompt"], "cwd": body.get("cwd"),
                    "sandbox": run_mode(node["agentId"], permission),
                    "model": node["model"], "timeout_minutes": body.get("timeout_minutes", 30),
                    "cursor_trusted_cwd": trusted_cwd if node["agentId"] == "cursor" else "",
                })
                cwd = request["cwd"]
                if node["agentId"] == "cursor":
                    trusted_cwd = request["cursor_trusted_cwd"]
            except ValueError as exc:
                errors.append(f"{node['name']}：{exc}")
        if errors:
            raise ValueError("\n".join(errors))
        request = dict(team=team, cwd=cwd, permission=permission,
                       timeout_minutes=body.get("timeout_minutes", 30), cursor_trusted_cwd=trusted_cwd)
        with self.lock:
            path = self._path(workflow_id)
            if (path / "workflow.json").is_file():
                existing = self._read(workflow_id)
                if _same_workflow(existing, request):
                    return self.get(workflow_id)
                if not _workflow_trust_retry(existing, request):
                    raise ValueError("同一个请求编号不能提交不同协作任务")
                if self.closed:
                    raise ValueError("调度服务正在退出")
                self.runs.reserve(workflow_id)
                try:
                    existing.update(
                        cursor_trusted_cwd=request["cursor_trusted_cwd"], status="starting",
                        finished_at=None, error="", trust_required=False,
                    )
                    for step in existing["steps"]:
                        if step["status"] != "succeeded":
                            step.update(status="waiting", error="", launched=False)
                    self._begin(existing)
                except Exception:
                    self.active = None
                    self.runs.release(workflow_id)
                    raise
                return self.get(workflow_id)
            if self.closed:
                raise ValueError("调度服务正在退出")
            self.runs.reserve(workflow_id)
            try:
                path.mkdir(exist_ok=False)
                steps = execution_steps(team)
                for index, step in enumerate(steps):
                    node = next(item for item in team["nodes"] if item["id"] == step["node_id"])
                    step.update(
                        name=node["name"], agent=node["agentId"], status="waiting",
                        run_id=uuid.uuid5(uuid.UUID(workflow_id), str(index)).hex,
                        error="", launched=False,
                    )
                record = {
                    **request, "id": workflow_id, "title": team["name"], "status": "starting",
                    "created_at": stamp(), "finished_at": None, "steps": steps, "error": "",
                    "trust_required": False,
                }
                self._begin(record)
            except Exception:
                self.active = None
                self.runs.release(workflow_id)
                raise
            return self.get(workflow_id)

    def _begin(self, record):
        self._save(record)
        self.active = record["id"]
        self.cancel_requested.clear()
        self.worker = threading.Thread(target=self._execute, args=(record["id"],),
                                       name="collaboration-scheduler", daemon=False)
        self.worker.start()

    def cancel(self, workflow_id):
        with self.lock:
            record = self._read(workflow_id)
            if workflow_id == self.active and record["status"] in ACTIVE:
                self.cancel_requested.set()
                record["status"] = "cancelling"
                self._save(record)
                for step in record["steps"]:
                    if step["status"] == "running":
                        self.runs.cancel(step["run_id"])
            return self.get(workflow_id)

    def _execute(self, workflow_id):
        previous = []
        outcome, error = "failed", ""
        try:
            with self.lock:
                record = self._read(workflow_id)
                record["status"] = "running"
                self._save(record)
            for index, step in enumerate(record["steps"]):
                node = next(item for item in record["team"]["nodes"] if item["id"] == step["node_id"])
                with self.lock:
                    if self.cancel_requested.is_set():
                        outcome = "cancelled"
                        break
                    run = self.runs.start({
                        "request_id": step["run_id"], "agent": node["agentId"],
                        "prompt": step_prompt(record, step, previous),
                        "cwd": record["cwd"], "sandbox": run_mode(node["agentId"], record["permission"]),
                        "model": node["model"], "timeout_minutes": record["timeout_minutes"],
                        "cursor_trusted_cwd": record["cursor_trusted_cwd"] if node["agentId"] == "cursor" else "",
                    }, owner=workflow_id)
                    step["status"] = "running"
                    step["launched"] = True
                    self._save(record)
                while run["status"] in ACTIVE:
                    if self.cancel_requested.wait(0.15):
                        self.runs.cancel(run["id"])
                        # The owned process must finish before releasing the lease.
                        if self.runs.worker:
                            self.runs.worker.join(timeout=1)
                    run = self.runs.get(run["id"])
                step["status"], step["error"] = run["status"], run["error"]
                with self.lock:
                    self._save(record)
                if run["status"] != "succeeded" or self.cancel_requested.is_set():
                    outcome = "cancelled" if self.cancel_requested.is_set() or run["status"] == "cancelled" else "failed"
                    error = f"{step['name']} / {PHASES[step['phase']]}：{run['error'] or run['status']}"
                    break
                previous.append({
                    **step, "result": run["result"], "path": str(Path(run["directory"]) / "result.md"),
                })
            else:
                outcome = "succeeded"
        except Exception as exc:
            error = str(exc) if isinstance(exc, ValueError) else f"调度失败：{type(exc).__name__}"
        finally:
            # Unexpected persistence/read failures must not orphan an owned CLI
            # or let a new manual run overlap it when the scheduler releases.
            with self.runs.lock:
                active_id = self.runs.active if self.runs.owner == workflow_id else None
            if active_id:
                self.runs.cancel(active_id)
                if self.runs.worker:
                    self.runs.worker.join(timeout=20)
            with self.lock:
                record = self._read(workflow_id)
                record.update(
                    status=outcome, finished_at=stamp(), error=error,
                    trust_required=outcome == "failed" and TRUST_PROMPT in error,
                )
                for step in record["steps"]:
                    if step["status"] in {"waiting", "running"}:
                        step["status"] = "cancelled" if outcome == "cancelled" else "blocked"
                summary = [
                    f"# {record['title']}", "", f"执行状态：{outcome}",
                    "执行状态只表示客户端是否正常返回；质量验收请以各节点的验证依据和审核结论为准。",
                    "", "## 步骤记录",
                    *[f"- {s['name']} / {PHASES[s['phase']]} / {s['agent']}：{s['status']}（{s['run_id']}）"
                      for s in record["steps"]],
                ]
                if error:
                    summary += ["", "## 中断原因", error]
                roots = {n["id"] for n in record["team"]["nodes"] if n["parentId"] is None}
                reports = [p for p in previous if p["node_id"] in roots and p["phase"] != "assign"]
                if not reports and previous:
                    reports = previous[-1:]
                for report in reports:
                    summary += ["", f"## {report['name']} / {PHASES[report['phase']]}",
                                clipped(report["result"], 100_000), "", f"完整结果：{report['path']}"]
                (self._path(workflow_id) / "summary.md").write_text("\n".join(summary), encoding="utf-8")
                self._save(record)
                self.active = None
                self.runs.release(workflow_id)

    def close(self):
        with self.lock:
            self.closed = True
            if self.active:
                self.cancel(self.active)
            worker = self.worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=25)
