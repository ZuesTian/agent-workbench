from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from time import perf_counter
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .adapters import (
    discover_agents,
    normalize,
    mcp_entries,
)
from .discovery import probe_installations
from .scan_cache import ParsedFiles, fingerprint, load_snapshot, save_snapshot
from .storage import read_json, write_json, stamp


def uid(*parts) -> str:
    return hashlib.sha256("\0".join(map(str, parts)).encode()).hexdigest()[:24]


def metadata(text: str) -> dict:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            try:
                result = yaml.safe_load(parts[1])
                if isinstance(result, dict):
                    return result
            except yaml.YAMLError:
                pass
    return {}


def skill_content(content: bytes):
    text = content.decode("utf-8-sig")
    return text, metadata(text)


def safe_config(raw: dict, kind: str) -> dict:
    """Never send credentials, command arguments, or full remote URLs to the UI."""
    n = normalize(raw, kind)
    result = {"transport": n["transport"], "enabled": bool(n["enabled"])}
    if n["command"]:
        result["launcher"] = re.split(r"[/\\]", str(n["command"]))[-1]
        result["argument_count"] = len(n["args"]) if isinstance(n["args"], list) else 0
    if n["url"]:
        try:
            result["host"] = urlsplit(n["url"]).hostname or "远程地址"
        except ValueError:
            result["host"] = "远程地址"
    result["environment_keys"] = list(n["env"]) if isinstance(n["env"], dict) else []
    result["header_keys"] = list(n["headers"]) if isinstance(n["headers"], dict) else []
    result["fields"] = list(raw)
    return result


class Catalog:
    def __init__(
        self,
        home: Path | None = None,
        roaming: Path | None = None,
        data: Path | None = None,
        *,
        system_scan: bool | None = None,
    ):
        self.system_scan = (
            (home is None and roaming is None) if system_scan is None else system_scan
        )
        self.home = home or Path.home()
        self.roaming = roaming or Path(os.getenv("APPDATA", str(self.home / ".config")))
        self.data = (
            data
            or Path(os.getenv("LOCALAPPDATA", str(self.home / ".local/share")))
            / "AgentWorkbench"
        )
        self.data.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.settings_path = self.data / "settings.json"
        self.settings_error = ""
        try:
            self.settings = read_json(self.settings_path, {"roots": [], "agents": []})
            if not isinstance(self.settings, dict):
                raise ValueError()
            if not isinstance(self.settings.get("roots"), list) or not all(
                isinstance(p, str) for p in self.settings["roots"]
            ):
                raise ValueError()
            if not isinstance(self.settings.get("agents"), list):
                raise ValueError()
            self.settings.setdefault("install_roots", [])
            if not isinstance(self.settings["install_roots"], list) or not all(
                isinstance(p, str) for p in self.settings["install_roots"]
            ):
                raise ValueError()
            for agent in self.settings["agents"]:
                if not isinstance(agent, dict) or any(
                    not isinstance(agent.get(k), str) or not agent[k]
                    for k in ("id", "name", "config")
                ):
                    raise ValueError()
        except (ValueError, TypeError, OSError):
            self.settings_error = (
                "settings.json 无法读取；已保留原文件，修复后重新启动可恢复自定义目录。"
            )
            self.settings = {"roots": [], "agents": [], "install_roots": []}
        self.skills = {}
        self.mcps = {}
        self.roles = {}
        self.agents = {}
        self.snapshot = None
        self._files = ParsedFiles()
        self._status_lock = threading.Lock()
        self._refresh_thread = None
        self._needs_refresh = True
        self._closed = False
        self._scan_state = {
            "running": False,
            "revision": 0,
            "error": None,
            "cache_warning": None,
            "elapsed_seconds": None,
        }
        self._cache_path = self.data / "cache/catalog.json"
        self._display_snapshot = load_snapshot(self._cache_path, self._cache_context())

    def _cache_context(self):
        return fingerprint(
            [
                str(self.home),
                str(self.roaming),
                str(self.data),
                self.system_scan,
                self.settings,
            ]
        )

    def save_settings(self):
        if self.settings_error:
            raise ValueError(self.settings_error)
        write_json(self.settings_path, self.settings)

    def scan(self):
        with self.lock:
            previous = self.skills, self.mcps, self.roles, self.agents, self.snapshot
            started = perf_counter()
            with self._status_lock:
                self._needs_refresh = False
                self._scan_state = {**self._scan_state, "running": True, "error": None}
            self._files.begin()
            try:
                result = self._scan()
                self._display_snapshot = result
                self._files.prune()
                cache_warning = None
                try:
                    save_snapshot(self._cache_path, self._cache_context(), result)
                except (OSError, ValueError, TypeError):
                    cache_warning = "本次扫描已完成，启动缓存未能保存。"
                with self._status_lock:
                    self._scan_state = {
                        **self._scan_state,
                        "revision": self._scan_state["revision"] + 1,
                        "cache_warning": cache_warning,
                    }
                return result
            except Exception as exc:
                self.skills, self.mcps, self.roles, self.agents, self.snapshot = (
                    previous
                )
                with self._status_lock:
                    self._scan_state = {
                        **self._scan_state,
                        "error": f"扫描未完成（{type(exc).__name__}），可点击重新扫描重试。",
                    }
                raise
            finally:
                with self._status_lock:
                    self._scan_state = {
                        **self._scan_state,
                        "running": False,
                        "elapsed_seconds": round(perf_counter() - started, 3),
                    }

    def scan_status(self):
        with self._status_lock:
            return {
                **self._scan_state,
                "cached": self.snapshot is None and self._display_snapshot is not None,
                "ready": self.snapshot is not None,
            }

    def start_scan(self):
        with self._status_lock:
            if self._closed or self._scan_state["running"]:
                return
            self._needs_refresh = False
            revision = self._scan_state["revision"]
            self._scan_state = {**self._scan_state, "running": True, "error": None}
            self._refresh_thread = threading.Thread(
                target=self._background_scan,
                args=(revision,),
                daemon=True,
                name="catalog-refresh",
            )
            self._refresh_thread.start()

    def _background_scan(self, revision):
        try:
            with self.lock:
                # A foreground operation may already have refreshed the catalog
                # while this worker was waiting for the transaction lock.
                if self._scan_state["revision"] != revision and not self._needs_refresh:
                    return
                self.scan()
        except Exception:
            # scan() records a public, credential-free error and retains the old view.
            pass

    def view(self):
        if self._needs_refresh:
            self.start_scan()
        snapshot = (
            self.snapshot
            or self._display_snapshot
            or {
                "scanned_at": None,
                "agents": [],
                "skills": [],
                "mcps": [],
                "roles": [],
                "issues": [],
                "roots": self.settings["roots"],
                "install_roots": self.settings["install_roots"],
                "discovery": {"roots": [], "issues": [], "system": self.system_scan},
                "data_dir": str(self.data),
                "counts": dict.fromkeys(
                    (
                        "agents",
                        "installed_agents",
                        "config_only_agents",
                        "skills",
                        "skill_names",
                        "mcps",
                        "mcp_names",
                        "roles",
                    ),
                    0,
                ),
            }
        )
        return {**snapshot, "scan": self.scan_status()}

    def close(self):
        with self._status_lock:
            self._closed = True
            worker = self._refresh_thread
        if worker and worker is not threading.current_thread():
            worker.join()

    def invalidate(self):
        self._display_snapshot = self.snapshot or self._display_snapshot
        self.snapshot = None
        self._needs_refresh = True

    def _scan(self):
        self.skills, self.mcps, self.roles = {}, {}, {}
        self.agents = {
            a.id: a
            for a in discover_agents(self.home, self.roaming, self.settings["agents"])
        }
        issues, roots, public_agents = (
            ([self.settings_error] if self.settings_error else []),
            [],
            [],
        )
        installations, discovery = probe_installations(
            self.home,
            self.roaming,
            self.settings["install_roots"],
            system=self.system_scan,
        )
        issues.extend(discovery["issues"])
        for agent in self.agents.values():
            agent.installations.extend(installations.get(agent.id, []))
            if not agent.detected:
                continue
            status = (
                "installed"
                if agent.installations
                else (
                    "custom"
                    if agent.id.startswith("custom-") and not agent.config.is_dir()
                    else "config_only"
                )
            )
            public = {
                "id": agent.id,
                "name": agent.name,
                "color": agent.color,
                "config": str(agent.config),
                "kind": agent.kind,
                "skills_path": str(agent.skills) if agent.skills else None,
                "note": agent.note,
                "mcp_count": 0,
                "skill_count": 0,
                "running_hint": any(x["running"] for x in agent.installations),
                "installation_status": status,
                "installations": agent.installations,
                "configuration_evidence": [
                    str(p)
                    for p in dict.fromkeys(
                        [agent.config, *agent.extra_configs, *agent.evidence]
                    )
                    if p.exists()
                ],
                "mcp_writable": agent.mcp_writable,
                "config_exists": agent.config.is_file(),
                "error": None,
            }
            if agent.config.is_dir():
                public["error"] = (
                    "MCP 配置路径指向文件夹；请在设置中移除此接入后，使用具体配置文件重新接入。"
                )
                issues.append(agent.name + "：" + public["error"])
            public_agents.append(public)
            for p in ([agent.skills] if agent.skills else []) + agent.skill_roots:
                roots.append((p, agent.id, "本地 Skill"))
            for p in agent.plugin_roots:
                roots.append((p, agent.id, "插件目录 / 启用状态未核验"))
            if agent.id == "zcode":
                for installation in agent.installations:
                    packages = (
                        Path(installation["path"]).parent / "resources/glm/packages"
                    )
                    if packages.is_dir():
                        roots.append(
                            (packages, agent.id, "随程序提供的插件 / 启用状态未核验")
                        )
            for p in dict.fromkeys([agent.config, *agent.extra_configs]):
                if not p.is_file():
                    continue
                try:
                    doc, source_hash = self._files.document(p, agent.kind)
                    entries = mcp_entries(doc, agent)
                    issues.extend(
                        f"{agent.name} / {p.name}：{msg}"
                        for msg in doc.get("_scan_issues", [])
                    )
                    for name, raw in entries.items():
                        if not isinstance(raw, dict):
                            continue
                        self._mcp(
                            str(name),
                            raw,
                            agent.kind,
                            agent.id,
                            p,
                            p == agent.config and agent.kind != "dsh",
                            (
                                "Cordis 静态定义 / " + p.parent.name
                                if agent.kind == "dsh"
                                else "全局"
                            ),
                            source_hash=source_hash,
                        )
                        public["mcp_count"] += 1
                    if agent.id == "claude":
                        for project, config in doc.get("projects", {}).items():
                            if isinstance(config, dict):
                                for name, raw in config.get("mcpServers", {}).items():
                                    if isinstance(raw, dict):
                                        self._mcp(
                                            str(name),
                                            raw,
                                            agent.kind,
                                            agent.id,
                                            p,
                                            False,
                                            str(project),
                                            source_hash=source_hash,
                                        )
                                        public["mcp_count"] += 1
                except Exception as exc:
                    msg = f"{agent.name}：无法解析 {p.name}（{type(exc).__name__}）；已跳过，未覆盖原文件。"
                    issues.append(msg)
                    if p == agent.config:
                        public["error"] = msg
            for root in agent.role_roots:
                if root.is_dir():
                    for p in sorted(root.iterdir()):
                        if p.suffix not in (".md", ".toml") or not p.is_file():
                            continue
                        try:
                            (text, meta), _ = self._files.read(p, "role", skill_content)
                            if p.suffix == ".toml":
                                meta, _ = self._files.document(p, "codex")
                            i = uid("role", p)
                            self.roles[i] = {
                                "id": i,
                                "name": str(meta.get("name", p.stem)),
                                "description": str(meta.get("description", "")),
                                "agent": agent.id,
                                "path": str(p),
                                "_text": text,
                            }
                        except Exception:
                            issues.append(f"无法读取子 Agent：{p.name}")
        for root in self.settings["roots"]:
            roots.append((Path(root), "custom", "自定义目录"))
        roots.append((self.data / "library/skills", "library", "我的资源库"))
        for root, agent_id, label in roots:
            if not root.is_dir():
                continue
            for p in self._walk_skills(
                root, issues, agent_id if "插件" in label else None
            ):
                try:
                    resolved = str(p.resolve()).casefold()
                    i = uid("skill", resolved)
                    source = {"agent": agent_id, "label": label, "path": str(p.parent)}
                    if i in self.skills:
                        if source not in self.skills[i]["sources"]:
                            self.skills[i]["sources"].append(source)
                        continue
                    if p.stat().st_size > 2_000_000:
                        issues.append(f"Skill 超出读取上限：{p}")
                        continue
                    (text, meta), source_hash = self._files.read(
                        p, "skill", skill_content
                    )
                    self.skills[i] = {
                        "id": i,
                        "name": str(meta.get("name", p.parent.name)),
                        "slug": p.parent.name,
                        "description": str(meta.get("description", "暂无说明")),
                        "path": str(p.parent),
                        "sources": [source],
                        "signature": uid(text)[:8],
                        "_text": text,
                        "_path": p.parent,
                        "_definition_hash": source_hash,
                    }
                except (OSError, UnicodeError):
                    issues.append(f"无法读取 Skill：{p}")
        library_path = self.data / "library/mcps.json"
        try:
            content = library_path.read_bytes() if library_path.exists() else b"[]"
            library = json.loads(content.decode("utf-8-sig"))
            if not isinstance(library, list):
                raise ValueError()
            for item in library:
                try:
                    if (
                        not isinstance(item, dict)
                        or not isinstance(item.get("config"), dict)
                        or not isinstance(item.get("name"), str)
                    ):
                        raise ValueError()
                    self._mcp(
                        item["name"],
                        item["config"],
                        item.get("kind", "json"),
                        "library",
                        library_path,
                        False,
                        library_id=item["id"],
                        source_hash=hashlib.sha256(content).hexdigest(),
                    )
                except (ValueError, KeyError, TypeError):
                    issues.append("资源库中有一项 MCP 格式不正确，已跳过该项。")
        except (ValueError, OSError):
            issues.append("MCP 资源库无法读取；原文件已保留，其他来源可继续使用。")
        for agent in public_agents:
            agent["skill_count"] = sum(
                any(s["agent"] == agent["id"] for s in x["sources"])
                for x in self.skills.values()
            )
            agent["mcp_count"] = sum(
                x["agent"] == agent["id"] for x in self.mcps.values()
            )
        self.snapshot = {
            "scanned_at": stamp(),
            "agents": public_agents,
            "skills": [
                self.public(x)
                for x in sorted(
                    self.skills.values(), key=lambda x: (x["name"].lower(), x["path"])
                )
            ],
            "mcps": [
                self.public(x)
                for x in sorted(
                    self.mcps.values(), key=lambda x: (x["name"].lower(), x["id"])
                )
            ],
            "roles": [self.public(x) for x in self.roles.values()],
            "issues": issues,
            "roots": self.settings["roots"],
            "install_roots": self.settings["install_roots"],
            "discovery": discovery,
            "data_dir": str(self.data),
        }
        self.snapshot["counts"] = {
            "agents": len(public_agents),
            "installed_agents": sum(
                a["installation_status"] == "installed" for a in public_agents
            ),
            "config_only_agents": sum(
                a["installation_status"] == "config_only" for a in public_agents
            ),
            "skills": len(self.skills),
            "skill_names": len({x["name"] for x in self.skills.values()}),
            "mcps": len(self.mcps),
            "mcp_names": len({x["name"] for x in self.mcps.values()}),
            "roles": len(self.roles),
        }
        return self.snapshot

    def _walk_skills(self, root, issues, plugin_agent=None):
        stack, visited, count = [(root, 0)], set(), 0
        excluded = {
            "node_modules",
            ".git",
            "__pycache__",
            ".venv",
            "venv",
            "sessions",
            "log",
            "logs",
            "dist",
        }
        while stack:
            directory, depth = stack.pop()
            try:
                real = str(directory.resolve()).casefold()
                if real in visited:
                    continue
                visited.add(real)
                count += 1
                if count > 30000:
                    issues.append(f"扫描目录过大，已截断：{root}")
                    break
                with os.scandir(directory) as listing:
                    directory_entries = {
                        (
                            entry.name.casefold() if os.name == "nt" else entry.name
                        ): entry
                        for entry in listing
                    }
                if plugin_agent:
                    for name in (".mcp.json", "mcp.json"):
                        entry = directory_entries.get(name)
                        if entry and entry.is_file():
                            config = Path(entry.path)
                            try:
                                doc, source_hash = self._files.document(config, "json")
                                entries = doc.get("mcpServers", doc.get("servers", doc))
                                if isinstance(entries, dict):
                                    for server_name, raw in entries.items():
                                        if isinstance(raw, dict) and any(
                                            k in raw
                                            for k in (
                                                "command",
                                                "url",
                                                "httpUrl",
                                                "type",
                                            )
                                        ):
                                            self._mcp(
                                                str(server_name),
                                                raw,
                                                "json",
                                                plugin_agent,
                                                config,
                                                False,
                                                "插件目录 / 启用状态未核验",
                                                source_hash=source_hash,
                                            )
                            except Exception:
                                issues.append(f"插件 MCP 配置无法解析：{config}")
                skill = directory_entries.get(
                    "skill.md" if os.name == "nt" else "SKILL.md"
                )
                if skill and skill.is_file():
                    yield Path(skill.path)
                    continue
                if depth >= 10:
                    issues.append(f"已跳过超过 10 层的目录：{directory}")
                    continue
                for entry in directory_entries.values():
                    if entry.name not in excluded and entry.is_dir():
                        stack.append((Path(entry.path), depth + 1))
            except OSError:
                issues.append(f"无法访问目录：{directory}")

    def _mcp(
        self,
        name,
        raw,
        kind,
        agent,
        path,
        writable,
        scope="全局",
        library_id=None,
        source_hash=None,
    ):
        i = uid("mcp", path, scope, name, library_id or "")
        self.mcps[i] = {
            "id": i,
            "name": name,
            "agent": agent,
            "source": str(path),
            "scope": scope,
            "writable_source": writable,
            "summary": safe_config(raw, kind),
            "_raw": raw,
            "_kind": kind,
            "_source_hash": source_hash,
        }

    @staticmethod
    def public(item):
        return {k: v for k, v in item.items() if not k.startswith("_")}

    def get(self):
        return self.snapshot or self.scan()
