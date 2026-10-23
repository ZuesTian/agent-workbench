"""Application use cases. HTTP handles transport, never resource mutations."""

from __future__ import annotations

import copy
import os
import uuid
import threading
from pathlib import Path
from urllib.parse import parse_qs

from .adapters import discover_agents, validate_mcp
from .conversations import ConversationManager
from .execution import ACTIVE, paginate, record_text
from .folders import browse_directory
from .engine import Engine
from .execution import ExecutionManager
from .scheduling import WorkflowManager
from .profiles import Profiles
from .teams import Teams
from .resources import copy_resource, digest, remove_owned
from .storage import read_json, write_json
from .validation import text
from .task_library import TaskLibrary
from .data_location import relocate

KINDS = {"json", "codex", "opencode", "vscode", "gemini", "zcode"}


def listing(query, kind=False):
    params = parse_qs(query or "", keep_blank_values=False)

    def text_param(name, limit):
        value = (params.get(name) or [""])[0]
        return value[:limit] if isinstance(value, str) else ""

    def number(name, default, upper):
        raw = text_param(name, 12)
        if raw.isdigit():
            return min(int(raw), upper) or default
        return default

    found = (text_param("q", 200), number("page", 1, 100000), number("page_size", 20, 50))
    if not kind:
        return found
    scope = text_param("kind", 16)
    return (*found, scope if scope in {"all", "task", "step"} else "all")


class WorkbenchService:
    def __init__(self, catalog):
        self.catalog = catalog
        self.mutation_lock = threading.RLock()
        self.migration = None
        self.library = TaskLibrary(catalog.data)
        self.engine = Engine(catalog)
        self.profiles = Profiles(catalog.data)
        self.teams = Teams(catalog.data)
        self.runs = ExecutionManager(catalog.data)
        self.workflows = WorkflowManager(catalog.data, self.runs)
        self.conversations = ConversationManager(catalog.data, self.runs)
        self.operations = {
            "/api/profiles/save": self.profiles.save,
            "/api/profiles/delete": lambda b: self.profiles.delete(b.get("id")),
            "/api/teams/save": self.teams.save,
            "/api/teams/delete": self.teams.delete,
            "/api/preview": self.engine.preview,
            "/api/apply": lambda b: self.engine.apply(b.get("id")),
            "/api/restore": lambda b: self.engine.restore(b.get("id")),
            "/api/roots/add": self.add_root,
            "/api/roots/remove": self.remove_root,
            "/api/install-roots/add": self.add_install_root,
            "/api/install-roots/remove": self.remove_install_root,
            "/api/agents/add": self.add_agent,
            "/api/agents/remove": self.remove_agent,
            "/api/library/mcp": self.add_mcp,
            "/api/library/skill": self.import_skill,
        }

    def get(self, path, query=""):
        if path == "/api/data/status":
            return {"path": str(self.catalog.data), "migration": self.migration}
        if path == "/api/tasks":
            return self.task_page(query)
        if path == "/api/catalog":
            return self.catalog.view()
        if path == "/api/scan-status":
            return self.catalog.scan_status()
        # These lists use atomic file replacement and do not access the in-flight
        # scan's private state. Loading the shell must not wait for a scan lock.
        routes = {
            "/api/profiles": self.profiles.list,
            "/api/history": self.engine.history,
            "/api/teams": self.teams.list,
        }
        if path in routes:
            return routes[path]()
        if path == "/api/activity":
            return self.activity()
        if path == "/api/conversations":
            result = self.conversations.list()
            result["items"] = self.library.decorate("conversation", result["items"])
            return result
        if path.startswith("/api/conversations/"):
            return self.conversations.get(path.rsplit("/", 1)[-1])
        if path == "/api/workflows":
            query_text, page, size = listing(query)
            rows = self.library.decorate("workflow", self.workflows.list())
            return {**paginate(rows, query_text, page, size, record_text), "active": sum(r["status"] in ACTIVE for r in rows)}
        if path.startswith("/api/workflows/"):
            return {**self.workflows.get(path.rsplit("/", 1)[-1]), **self.library.metadata("workflow", path.rsplit("/", 1)[-1])}
        if path == "/api/runners":
            return self.runs.runners()
        if path.startswith("/api/runners/"):
            parts = path.split("/")
            if len(parts) in (5, 6) and parts[4] == "models":
                return self.runs.registry.models(parts[3], refresh=len(parts) == 6 and parts[5] == "refresh")
            raise LookupError()
        if path == "/api/runs":
            query_text, page, size, kind = listing(query, kind=True)
            rows = self.library.decorate("run", self.runs.list())
            selected = [r for r in rows if kind == "all" or (kind == "step") == bool(r.get("workflow_id"))]
            return {**paginate(selected, query_text, page, size, record_text), "active": sum(r["status"] in ACTIVE for r in rows), "kind": kind}
        if path.startswith("/api/runs/"):
            return self.runs.get(path.rsplit("/", 1)[-1])
        with self.catalog.lock:
            for kind in ("skills", "roles"):
                if path.startswith(f"/api/{kind}/"):
                    self.catalog.get()
                    item = getattr(self.catalog, kind).get(path.rsplit("/", 1)[-1])
                    if item:
                        return {**self.catalog.public(item), "content": item["_text"]}
            raise LookupError()

    def post(self, path, body):
        # A native picker can stay open indefinitely. It does not modify app
        # data and must not hold the lock needed to cancel a running task.
        if path == "/api/browse-directory":
            return {"path": browse_directory()}
        with self.mutation_lock:
            if self.migration:
                raise ValueError("数据迁移已完成，请完全退出工坊后重新打开，以使用新目录")
            if path == "/api/tasks/update":
                return self.update_task(body)
            if path == "/api/task-groups/save":
                return self.library.group(body)
            if path == "/api/data/migrate":
                with self.catalog.lock:
                    if (any(self.activity().values()) or self.runs.owner is not None
                            or any(m.active is not None for m in (self.runs, self.workflows, self.conversations))):
                        raise ValueError("请等待运行中的任务完成或取消后再迁移")
                    if self.catalog.scan_status().get("running"):
                        raise ValueError("资源扫描正在进行，请稍后再迁移")
                    backup_only = body.get("backup_only", False)
                    if not isinstance(backup_only, bool):
                        raise ValueError("备份选项格式不正确")
                    try:
                        result = relocate(self.catalog.data, body.get("path"), control=self.catalog.control, switch=not backup_only)
                    except OSError as exc:
                        raise ValueError(f"复制失败（{type(exc).__name__}）；未切换数据目录，原数据保留。请检查空间、权限及路径长度，并选择新的空目录重试。") from exc
                    if not backup_only:
                        self.migration = result
                    return result
            if path in {"/api/runs/start", "/api/workflows/start", "/api/workflows/resume", "/api/conversations/update", "/api/conversations/message", "/api/conversations/retry"}:
                kind = "workflow" if "/workflows/" in path else "run" if "/runs/" in path else "conversation"
                meta = self.library.metadata(kind, body.get("request_id") if path.endswith("/start") else body.get("id"))
                if meta.get("deleted_at") or meta.get("archived"):
                    raise ValueError("请先在任务库中恢复此记录，再继续执行")
            return self._post(path, body)

    def task_page(self, query):
        params = parse_qs(query)
        scope = params.get("scope", ["active"])[0]
        group = params.get("group", [""])[0]
        q, page, size = listing(query)
        rows = []
        sources = [("run", [r for r in self.runs.list() if not r.get("workflow_id")]),
                   ("workflow", self.workflows.list()), ("conversation", self.conversations.list()["items"])]
        for kind, records in sources:
            for record in self.library.decorate(kind, records, scope, group):
                rows.append({**record, "kind": kind, "title": record.get("title", record.get("name", "")),
                             "cwd": record.get("cwd", record.get("workspace", "")),
                             "status": record.get("status", "running" if record.get("running") else "idle")})
        rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return {**paginate(rows, q, page, size, record_text), "groups": self.library.read()["groups"]}

    def update_task(self, body):
        kind, ident = body.get("kind"), body.get("id")
        manager = {"run": self.runs, "workflow": self.workflows, "conversation": self.conversations}.get(kind)
        if manager is None:
            raise ValueError("任务类型不正确")
        record = manager.get(ident)
        if record.get("status") in ACTIVE or record.get("running") or manager.active == ident:
            raise ValueError("运行中的任务不能归档、删除或移动，请先等待或取消")
        if kind == "run" and record.get("workflow_id"):
            raise ValueError("协作步骤由所属任务统一管理，请操作父任务")
        return self.library.update(kind, ident, body)

    def _post(self, path, body):
        if path == "/api/scan":
            self.catalog.start_scan()
            return self.catalog.view()
        if path == "/api/workflows/start":
            return self.workflows.start(body)
        if path == "/api/workflows/resume":
            return self.workflows.resume(body)
        if path == "/api/workflows/cancel":
            return self.workflows.cancel(body.get("id"))
        if path == "/api/conversations/create":
            return self.conversations.create(body)
        if path == "/api/conversations/update":
            return self.conversations.update(body)
        if path == "/api/conversations/delete":
            self.update_task({"kind": "conversation", "id": body.get("id"), "action": "delete"})
            return {"deleted": body.get("id")}
        if path == "/api/conversations/message":
            return self.conversations.message(body)
        if path == "/api/conversations/cancel":
            return self.conversations.cancel(body.get("id"))
        if path == "/api/conversations/retry":
            return self.conversations.retry(body)
        if path == "/api/runs/start":
            return self.runs.start(body)
        if path == "/api/runs/cancel":
            return self.runs.cancel(body.get("id"))
        operation = self.operations.get(path)
        if operation is None:
            raise LookupError()
        with self.catalog.lock:
            return operation(body)

    def activity(self):
        runs = self.runs.list()
        workflows = self.workflows.list()
        conversations = self.conversations.list()["items"]
        return {
            "runs": sum(item.get("status") in ACTIVE for item in runs),
            "workflows": sum(item.get("status") in ACTIVE for item in workflows),
            "conversations": sum(1 for item in conversations if item.get("running")),
        }

    def update_settings(self, settings):
        if self.catalog.settings_error:
            raise ValueError(self.catalog.settings_error)
        write_json(self.catalog.settings_path, settings)
        self.catalog.settings = settings
        self.catalog.invalidate()
        return self.catalog.scan()

    def add_root(self, body):
        path = Path(text(body.get("path"), "目录", 4096, required=True)).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("请输入存在的绝对目录路径")
        settings = copy.deepcopy(self.catalog.settings)
        if not any(Path(p).resolve() == path.resolve() for p in settings["roots"]):
            settings["roots"].append(str(path))
        return self.update_settings(settings)

    def remove_root(self, body):
        path = text(body.get("path"), "目录", 4096, required=True)
        settings = copy.deepcopy(self.catalog.settings)
        settings["roots"] = [p for p in settings["roots"] if p != path]
        return self.update_settings(settings)

    def add_install_root(self, body):
        path = Path(
            text(body.get("path"), "程序目录", 4096, required=True)
        ).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("请输入存在的绝对目录路径")
        settings = copy.deepcopy(self.catalog.settings)
        if not any(
            Path(p).resolve() == path.resolve() for p in settings["install_roots"]
        ):
            settings["install_roots"].append(str(path))
        return self.update_settings(settings)

    def remove_install_root(self, body):
        path = text(body.get("path"), "程序目录", 4096, required=True)
        settings = copy.deepcopy(self.catalog.settings)
        settings["install_roots"] = [p for p in settings["install_roots"] if p != path]
        return self.update_settings(settings)

    def add_agent(self, body):
        name = text(body.get("name"), "Agent 名称", 60, required=True)
        config = Path(
            text(body.get("config"), "配置路径", 4096, required=True)
        ).expanduser()
        skills = text(body.get("skills", ""), "Skill 目录", 4096)
        kind = body.get("kind", "json")
        key = text(body.get("key", "mcpServers"), "MCP 根字段", 100, required=True)
        if kind not in KINDS or not config.is_absolute() or not config.parent.is_dir():
            raise ValueError("需要有效格式和绝对配置路径，且父目录已存在")
        if config.is_dir():
            raise ValueError("MCP 配置必须是文件路径，不能填写程序文件夹或项目目录")
        if skills and not Path(skills).is_absolute():
            raise ValueError("Skill 目录必须是绝对路径")
        agents = discover_agents(
            self.catalog.home, self.catalog.roaming, self.catalog.settings["agents"]
        )
        if any(a.config.resolve() == config.resolve() for a in agents):
            raise ValueError("此配置已由现有 Agent 接入")
        settings = copy.deepcopy(self.catalog.settings)
        settings["agents"].append(
            {
                "id": "custom-" + uuid.uuid4().hex[:10],
                "name": name,
                "config": str(config),
                "skills": skills,
                "kind": kind,
                "key": key,
            }
        )
        return self.update_settings(settings)

    def remove_agent(self, body):
        agent_id = text(body.get("id"), "Agent 编号", 200, required=True)
        settings = copy.deepcopy(self.catalog.settings)
        if not any(a["id"] == agent_id for a in settings["agents"]):
            raise ValueError("只能移除已接入的自定义 Agent")
        settings["agents"] = [a for a in settings["agents"] if a["id"] != agent_id]
        return self.update_settings(settings)

    def add_mcp(self, body):
        name = text(body.get("name"), "MCP 名称", 150, required=True)
        raw, kind = body.get("config"), body.get("kind", "json")
        if kind not in KINDS:
            raise ValueError("配置格式不正确")
        validate_mcp(raw, kind)
        path = self.catalog.data / "library/mcps.json"
        try:
            items = read_json(path, [])
            if not isinstance(items, list):
                raise ValueError()
        except (ValueError, OSError):
            raise ValueError(
                "原 MCP 资源库无法读取，请先修复该文件；本次未写入"
            ) from None
        if any(isinstance(x, dict) and x.get("name") == name for x in items):
            raise ValueError("资源库中已有同名 MCP，请使用不同名称")
        items.append(
            {"id": uuid.uuid4().hex, "name": name, "config": raw, "kind": kind}
        )
        write_json(path, items)
        self.catalog.invalidate()
        return self.catalog.scan()

    def import_skill(self, body):
        source = Path(
            text(body.get("path"), "Skill 目录", 4096, required=True)
        ).expanduser()
        if source.name.upper() == "SKILL.MD":
            source = source.parent
        if not source.is_absolute() or not (source / "SKILL.md").is_file():
            raise ValueError("请输入包含 SKILL.md 的绝对目录路径")
        before = digest(source)
        target = self.catalog.data / "library/skills" / source.name
        if target.exists():
            raise ValueError("资源库内已存在同名 Skill，请使用它或修改来源目录名")
        stage = self.catalog.data / "library" / (".import-" + uuid.uuid4().hex)
        stage.parent.mkdir(parents=True, exist_ok=True)
        try:
            copy_resource(source, stage)
            if digest(stage) != before or digest(source) != before:
                raise ValueError("导入期间来源发生变化，请重新导入")
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage, target)
        finally:
            if stage.exists():
                remove_owned(stage)
        self.catalog.invalidate()
        return self.catalog.scan()
