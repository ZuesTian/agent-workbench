"""Explicit adapters for local agent configuration, with loss-aware translation."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import json5
import tomlkit


@dataclass
class Agent:
    id: str
    name: str
    color: str
    config: Path
    kind: str = "json"
    key: str = "mcpServers"
    skills: Path | None = None
    evidence: list[Path] = field(default_factory=list)
    extra_configs: list[Path] = field(default_factory=list)
    skill_roots: list[Path] = field(default_factory=list)
    plugin_roots: list[Path] = field(default_factory=list)
    role_roots: list[Path] = field(default_factory=list)
    note: str = ""
    installations: list[dict] = field(default_factory=list)
    mcp_writable: bool = True

    @property
    def config_detected(self):
        return (
            self.config.is_file()
            or any(p.is_file() for p in self.extra_configs)
            or any(p.exists() for p in self.evidence)
        )

    @property
    def detected(self):
        return (
            self.config_detected
            or any(Path(x["path"]).is_file() for x in self.installations)
            or self.id.startswith("custom-")
        )


def discover_agents(home: Path, roaming: Path, custom: list[dict]) -> list[Agent]:
    h, a = home, roaming
    agents = [
        Agent(
            "codex",
            "Codex",
            "#476b56",
            h / ".codex/config.toml",
            "codex",
            "mcp_servers",
            h / ".agents/skills",
            [h / ".codex"],
            skill_roots=[h / ".codex/skills", h / ".agents/skills"],
            plugin_roots=[h / ".codex/plugins/cache"],
            role_roots=[h / ".codex/agents"],
            note="Skill 默认安装至共享的 .agents/skills；Codex 与 Cursor 等可能共同发现它。",
        ),
        Agent(
            "claude",
            "Claude Code",
            "#b57d57",
            h / ".claude.json",
            skills=h / ".claude/skills",
            evidence=[h / ".claude"],
            extra_configs=[h / ".claude/settings.json"],
            plugin_roots=[h / ".claude/plugins/cache"],
            role_roots=[h / ".claude/agents"],
        ),
        Agent(
            "cursor",
            "Cursor",
            "#53555e",
            h / ".cursor/mcp.json",
            skills=h / ".cursor/skills",
            evidence=[h / ".cursor"],
            skill_roots=[h / ".cursor/skills-cursor", h / ".agents/skills"],
            plugin_roots=[h / ".cursor/plugins/cache", h / ".cursor/plugins/local"],
            role_roots=[h / ".cursor/agents"],
        ),
        Agent(
            "trae",
            "Trae",
            "#399785",
            a / "Trae/User/mcp.json",
            skills=h / ".trae/skills",
            evidence=[h / ".trae", a / "Trae"],
            skill_roots=[h / ".trae/builtin_skills"],
            note="使用本机已发现的 mcp.json 格式；应用后请在 Trae 中重新加载验证。",
        ),
        Agent(
            "trae-cn",
            "Trae CN",
            "#43947d",
            a / "Trae CN/User/mcp.json",
            skills=h / ".trae-cn/skills",
            evidence=[h / ".trae-cn", a / "Trae CN"],
        ),
        Agent(
            "workbuddy",
            "WorkBuddy",
            "#a078bf",
            h / ".workbuddy/mcp.json",
            skills=h / ".workbuddy/skills",
            evidence=[h / ".workbuddy"],
            plugin_roots=[h / ".workbuddy/plugins/cache"],
            note="仅管理本地配置；云连接器的授权仍由 WorkBuddy 管理。",
        ),
        Agent(
            "opencode",
            "OpenCode",
            "#6576a4",
            h / ".config/opencode/opencode.json",
            "opencode",
            "mcp",
            h / ".config/opencode/skills",
            [h / ".config/opencode"],
            extra_configs=[h / ".config/opencode/opencode.jsonc"],
            skill_roots=[h / ".agents/skills", h / ".claude/skills"],
        ),
        Agent(
            "cline",
            "Cline",
            "#c18d45",
            h / ".cline/data/settings/cline_mcp_settings.json",
            skills=h / ".cline/skills",
            evidence=[h / ".cline"],
            extra_configs=[
                a
                / "Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json"
            ],
        ),
        Agent(
            "roo",
            "Roo Code",
            "#8e7967",
            a
            / "Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json",
            skills=h / ".roo/skills",
            evidence=[h / ".roo"],
        ),
        Agent(
            "windsurf",
            "Windsurf",
            "#4b9a9b",
            h / ".codeium/windsurf/mcp_config.json",
            skills=h / ".codeium/windsurf/skills",
            evidence=[h / ".codeium/windsurf"],
        ),
        Agent(
            "antigravity",
            "Antigravity",
            "#6287c7",
            h / ".gemini/antigravity/mcp_config.json",
            skills=h / ".gemini/antigravity/skills",
            evidence=[h / ".gemini/antigravity"],
        ),
        Agent(
            "claude-desktop",
            "Claude Desktop",
            "#b3856b",
            a / "Claude/claude_desktop_config.json",
            evidence=[a / "Claude"],
            note="此适配器仅装配本地 stdio MCP；桌面连接器和 Cowork 不在此配置中。",
        ),
        Agent(
            "gemini",
            "Gemini CLI",
            "#5e8ac7",
            h / ".gemini/settings.json",
            "gemini",
            skills=h / ".gemini/skills",
            evidence=[h / ".gemini/settings.json"],
        ),
        Agent(
            "deepseek-harness",
            "DeepSeek Harness",
            "#536fbc",
            h / ".dsh/profiles/desktop/cordis.patch.yml",
            "dsh",
            skills=h / ".dsh/skills",
            evidence=[h / ".dsh"],
            extra_configs=sorted((h / ".dsh/profiles").glob("*/cordis*.yml")),
            skill_roots=[h / ".agents/skills"],
            note="支持 Skill 装配；MCP 仅盘点 Cordis 文件中的静态定义，不求值脚本、不推断最终启用状态。MCP 修改请在 Harness 中完成。",
            mcp_writable=False,
        ),
        Agent(
            "zcode",
            "Zcode",
            "#467dc5",
            h / ".zcode/cli/config.json",
            "zcode",
            "mcp.servers",
            h / ".zcode/skills",
            [h / ".zcode", a / "ZCode"],
            skill_roots=[h / ".agents/skills"],
            plugin_roots=[h / ".zcode/cli/plugins"],
            note="写入用户级 mcp.servers；桌面端若使用自身管理的连接列表，请在设置 → MCP 中核对。",
        ),
        Agent(
            "vscode",
            "VS Code",
            "#438dbc",
            a / "Code/User/mcp.json",
            "vscode",
            "servers",
            evidence=[a / "Code/User"],
        ),
    ]
    for x in custom:
        agents.append(
            Agent(
                x["id"],
                x["name"],
                "#76816f",
                Path(x["config"]),
                x.get("kind", "json"),
                x.get("key", "mcpServers"),
                Path(x["skills"]) if x.get("skills") else None,
                [Path(x["config"]).parent],
                note="自定义适配器；请确认目标软件支持这些目录与字段。",
            )
        )
    return agents


def config_targets(agent):
    """Return only known writable global configuration locations for this client."""
    candidates = (
        [agent.config, *agent.extra_configs]
        if agent.id in {"cline", "opencode"}
        else [agent.config]
    )
    existing = []
    for path in candidates:
        if path.is_file() and path not in existing:
            existing.append(path)
    return existing or [agent.config]


def resolve_config_target(agent, selected=None):
    """Resolve an explicit known target, or the sole unambiguous location."""
    candidates = config_targets(agent)
    if selected:
        chosen = next((path for path in candidates if Path(selected) == path), None)
        if chosen is None:
            raise ValueError(
                agent.name + " 所选配置已失效或不属于可写位置，请重新选择配置文件"
            )
    elif len(candidates) > 1:
        raise ValueError(
            agent.name + " 有多份配置并存，请在下方选择本次要修改的配置文件"
        )
    else:
        chosen = candidates[0]
    if agent.id == "claude":
        # User-level MCP lives in ~/.claude.json. settings.json is not that file.
        return chosen, "将写入 Claude Code 用户级配置：" + str(chosen)
    if chosen.is_file():
        note = "将写入已存在的配置：" + str(chosen)
        if chosen.suffix.lower() == ".jsonc":
            note += "。JSONC 注释在写回时不会保留。"
        return chosen, note
    return chosen, "主配置不存在，将新建：" + str(chosen)


def load_document(path: Path, kind: str):
    return read_document(path, kind)[0]


def read_document(path: Path, kind: str):
    """Parse and fingerprint the same bytes, so concurrent edits cannot be missed."""
    if not path.exists():
        return (tomlkit.document() if kind == "codex" else {}), None
    content = path.read_bytes()
    return parse_document(content, kind), hashlib.sha256(content).hexdigest()


def parse_document(content: bytes, kind: str):
    text = content.decode("utf-8-sig")
    if kind == "dsh":
        from .dsh import read_static_mcps

        doc = read_static_mcps(text)
    elif kind == "codex":
        doc = tomlkit.parse(text)
    else:
        try:
            doc = json.loads(text)
        except json.JSONDecodeError:
            doc = json5.loads(text)
    if not isinstance(doc, dict):
        raise ValueError("配置根节点必须是对象")
    return doc


def dump_document(doc, kind: str) -> bytes:
    if kind == "dsh":
        raise ValueError("Harness Cordis 配置仅支持只读盘点")
    return (
        tomlkit.dumps(doc)
        if kind == "codex"
        else json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def mcp_entries(doc, agent):
    if agent.kind == "zcode":
        parent = doc.get("mcp", {})
        if not isinstance(parent, dict):
            raise ValueError("mcp 必须为对象")
        entries = parent.get("servers", {})
    else:
        entries = doc.get(agent.key, {})
    if not isinstance(entries, dict):
        raise ValueError("MCP 节点必须为对象")
    return entries


def set_mcp_entries(doc, agent, entries):
    if agent.kind == "zcode":
        mcp_entries(doc, agent)  # Reject malformed parents, preserve other mcp fields.
        doc.setdefault("mcp", {})["servers"] = entries
    else:
        doc[agent.key] = entries


def normalize(raw: dict, kind: str) -> dict:
    command = raw.get("command")
    args = raw.get("args", [])
    if isinstance(command, list):
        args, command = command[1:], command[0] if command else ""
    url = raw.get("httpUrl") or raw.get("url")
    transport = (
        "stdio"
        if command
        else (
            "sse"
            if raw.get("type") == "sse" or (kind == "gemini" and raw.get("url"))
            else "http"
        )
    )
    return {
        "transport": transport,
        "command": command or "",
        "args": args or [],
        "url": url or "",
        "env": raw.get("environment", raw.get("env", {})) or {},
        "headers": raw.get("http_headers", raw.get("headers", {})) or {},
        "enabled": raw.get("enabled", not raw.get("disabled", False)),
        "cwd": raw.get("cwd"),
    }


def translate(raw: dict, source_kind: str, target: Agent) -> tuple[dict, list[str]]:
    n = validate_mcp(raw, source_kind)
    warnings = []
    if not target.mcp_writable:
        raise ValueError("此客户端的 MCP 配置仅支持只读盘点")
    if target.id == "claude-desktop" and n["transport"] != "stdio":
        raise ValueError("Claude Desktop 的此本地配置仅支持 stdio 装配")
    if source_kind == target.kind and source_kind not in ("json",):
        out = copy.deepcopy(raw)
        out.pop("disabled", None)
        out["enabled"] = True
        return out, warnings
    serialized = json.dumps(raw, ensure_ascii=False)
    if any(marker in serialized for marker in ("${", "{{", "{env:")):
        raise ValueError(
            "包含客户端专用变量表达式，不能直接跨客户端转换；请新增目标专用 MCP"
        )
    if n["transport"] != "stdio" and n["env"]:
        raise ValueError(
            "远程 MCP 带有客户端专用 env，请新增目标专用定义以保留认证语义"
        )
    # Do not silently discard auth or process-context semantics across clients.
    special = [
        k
        for k in (
            "env_vars",
            "env_http_headers",
            "bearer_token_env_var",
            "oauth",
            "oauth_resource",
            "http_headers_helper",
            "includeTools",
            "excludeTools",
            "enabled_tools",
            "disabled_tools",
        )
        if raw.get(k)
    ]
    if special:
        raise ValueError(
            "含目标软件不能自动转换的字段："
            + ", ".join(special)
            + "；请新增目标专用 MCP"
        )
    if n["transport"] == "sse" and target.kind in ("codex", "opencode"):
        raise ValueError("SSE 不能自动改成 Streamable HTTP；请提供目标兼容的 MCP URL")
    # Build the complete connection first. Target formatting may rename fields,
    # but it must not drop the URL, headers, or transport of a remote server.
    if n["transport"] == "stdio":
        out = {"command": n["command"], "args": list(n["args"])}
        if n["env"]:
            out["env"] = dict(n["env"])
        if n["cwd"]:
            out["cwd"] = n["cwd"]
    else:
        out = {"url": n["url"], "type": n["transport"]}
        if n["headers"]:
            out["headers"] = dict(n["headers"])
    if target.kind == "zcode":
        out["type"] = n["transport"]
        out["enabled"] = True
        if isinstance(raw.get("timeoutMs"), int) and not isinstance(
            raw.get("timeoutMs"), bool
        ):
            out["timeoutMs"] = raw["timeoutMs"]
    elif target.kind == "codex":
        if "headers" in out:
            out["http_headers"] = out.pop("headers")
        out["enabled"] = True
        for key in ("startup_timeout_sec", "tool_timeout_sec"):
            if key in raw:
                out[key] = raw[key]
    elif target.kind == "opencode":
        out["type"] = "local" if n["transport"] == "stdio" else "remote"
        out["enabled"] = True
        if "command" in out:
            out["command"] = [out["command"], *out.pop("args")]
        if "env" in out:
            out["environment"] = out.pop("env")
    elif target.kind == "gemini":
        if n["transport"] == "http":
            out["httpUrl"] = out.pop("url")
    else:
        out["type"] = n["transport"]
        if target.id in ("workbuddy", "cline", "roo", "windsurf", "antigravity"):
            out["disabled"] = False
        # Preserve client-specific settings only within the same agent.
    known = {
        "type",
        "command",
        "args",
        "url",
        "httpUrl",
        "env",
        "environment",
        "headers",
        "http_headers",
        "enabled",
        "disabled",
        "cwd",
    }
    dropped = [k for k in raw if k not in known and k not in out]
    if dropped:
        warnings.append("仅转换连接字段，未迁移来源特有字段：" + ", ".join(dropped))
    return out, warnings


def validate_mcp(raw, kind):
    if not isinstance(raw, dict):
        raise ValueError("需要单个 MCP 配置对象，不包含 mcpServers 外层")
    n = normalize(raw, kind)
    if not n["command"] and not n["url"]:
        raise ValueError("MCP 缺少 command 或 URL")
    if n["command"] and n["url"]:
        raise ValueError("command 与 URL 只能选择一个")
    if (
        not isinstance(n["command"], str)
        or not isinstance(n["args"], list)
        or not all(isinstance(x, str) for x in n["args"])
    ):
        raise ValueError("command 必须为字符串，args 必须为字符串数组")
    if not isinstance(n["url"], str):
        raise ValueError("URL 必须是文本")
    for key in ("env", "headers"):
        if not isinstance(n[key], dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in n[key].items()
        ):
            raise ValueError(f"{key} 必须为字符串键值对象")
    for flag in ("enabled", "disabled"):
        if flag in raw and not isinstance(raw[flag], bool):
            raise ValueError("MCP 启用选项必须是布尔值")
    return n
