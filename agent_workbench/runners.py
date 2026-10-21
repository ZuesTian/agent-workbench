"""Known local CLI entrypoints, permission modes and output protocols."""

from __future__ import annotations

import json
import os
import re
import shutil
import urllib.request
from pathlib import Path

from .discovery import executable_path, registry_records
from .model_catalog import ModelCatalog, zcode_model_override


def execution_environment():
    environment = os.environ.copy()
    keys = {key.lower() for key in environment}
    for protocol, url in urllib.request.getproxies().items():
        if protocol in {"http", "https", "all"} and f"{protocol}_proxy" not in keys:
            environment[f"{protocol.upper()}_PROXY"] = url
    if "no_proxy" not in keys:
        environment["NO_PROXY"] = "127.0.0.1,localhost,::1"
    for key in ("CODEX_SESSION_ID", "CODEX_THREAD_ID", "CODEX_TOOL_CALL_ID"):
        environment.pop(key, None)
    return environment


def cursor_command():
    """Resolve Cursor's own launcher, never the unrelated generic 'agent' name."""
    binary = shutil.which("cursor-agent.exe" if os.name == "nt" else "cursor-agent")
    if binary and Path(binary).is_file():
        return [str(Path(binary).resolve())]
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    versions = local / "cursor-agent/versions"
    installed = []
    if versions.is_dir():
        for directory in versions.iterdir():
            match = re.fullmatch(r"(\d{4})\.(\d{1,2})\.(\d{1,2})(?:-(\d{2})-(\d{2})-(\d{2}))?-[a-f0-9]+", directory.name)
            if match and (directory / "node.exe").is_file() and (directory / "index.js").is_file():
                installed.append((tuple(int(part or 0) for part in match.groups()), directory))
    if installed:
        _, directory = max(installed, key=lambda item: (item[0], item[1].name))
        return [str((directory / "node.exe").resolve()), str((directory / "index.js").resolve())]
    return None


def node_command():
    candidate = shutil.which("node")
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    path = Path(candidate) if candidate else local / "Programs/nodejs/node.exe"
    return str(path.resolve()) if path.is_file() else None


def dsh_command():
    """Read the installed launcher shim; never execute its shell contents."""
    node = node_command()
    if not node:
        return None
    binary = shutil.which("dsh.exe")
    if binary:
        return [str(Path(binary).resolve())]
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    shim = Path(shutil.which("dsh.cmd") or local / "dsh/bin/dsh.cmd")
    if shim.is_file():
        for value in re.findall(r'"([^"\r\n]+[/\\]apps[/\\]cli[/\\]lib[/\\]bin\.js)"',
                                shim.read_text(encoding="utf-8", errors="replace")):
            script = Path(value)
            if script.is_absolute() and script.is_file():
                return [node, str(script.resolve())]
    roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    for package in ("@deepseek-ai/dsh", "@deepseek-ai/dsh-cli"):
        root = roaming / "npm/node_modules" / package
        if (root / "package.json").is_file():
            metadata = json.loads((root / "package.json").read_text(encoding="utf-8"))
            entry = metadata.get("bin", {})
            entry = entry.get("dsh") if isinstance(entry, dict) else entry
            if isinstance(entry, str) and (root / entry).is_file():
                return [node, str((root / entry).resolve())]
    return None


def zcode_command():
    node = node_command()
    if not node:
        return None
    candidates = []
    for row in registry_records():
        if not re.fullmatch(r"ZCode(?:\s+.*)?", row.get("DisplayName", ""), re.I):
            continue
        if row.get("InstallLocation"):
            candidates.append(Path(row["InstallLocation"]))
        for field in ("DisplayIcon", "UninstallString"):
            path = executable_path(row.get(field))
            if path:
                candidates.append(path.parent)
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    candidates += [local / "Programs/ZCode", local / "ZCode"]
    for root in candidates:
        script = root / "resources/glm/zcode.cjs"
        if script.is_file():
            return [node, str(script.resolve())]
    return None


RUNNERS = {
    "codex": {
        "name": "Codex CLI", "modes": [
            {"id": "read-only", "name": "只读文件"},
            {"id": "workspace-write", "name": "允许修改工作目录"},
        ], "model_override": True,
        "note": "复用 Codex 登录与配置。文件操作受 Codex 沙箱约束；MCP 按客户端权限运行。",
    },
    "deepseek-harness": {
        "name": "DeepSeek Harness", "modes": [
            {"id": "read-only", "name": "只读文件"},
            {"id": "workspace-write", "name": "允许修改工作目录"},
        ], "model_override": True,
        "note": "使用 headless profile 的账号与资源。临时沙箱设置仅作用于本次运行；需要额外批准的操作自动拒绝。",
    },
    "zcode": {
        "name": "Zcode", "modes": [
            {"id": "plan", "name": "规划模式（plan）"},
            {"id": "edit", "name": "允许编辑（edit）"},
        ], "model_override": True,
        "note": "复用 Zcode 桌面端的数据目录与模型配置。plan / edit 是客户端权限模式，不提供工作目录级操作系统隔离；需批准的操作由 CLI 拒绝。",
    },
    "cursor": {
        "name": "Cursor Agent CLI", "modes": [
            {"id": "plan", "name": "只读 / 规划（plan）"},
            {"id": "edit", "name": "允许编辑（Agent 模式）"},
        ], "model_override": True,
        "note": "使用 Cursor Agent CLI 的登录、模型和权限配置。plan 为客户端只读模式；编辑模式仍遵守现有批准规则，不自动批准 MCP 或扩大权限。",
    },
}


def run_mode(agent, permission):
    """Map the workbench permission to the selected CLI's native mode."""
    if agent in {"zcode", "cursor"}:
        return "plan" if permission == "read-only" else "edit"
    return "read-only" if permission == "read-only" else "workspace-write"


class RunnerRegistry:
    def __init__(self, codex_resolver, overrides=None):
        self.model_catalog = ModelCatalog()
        self.resolvers = {
            "codex": codex_resolver, "deepseek-harness": dsh_command, "zcode": zcode_command,
            "cursor": cursor_command,
            **(overrides or {}),
        }

    def resolve(self, agent):
        if not agent:
            raise ValueError("未绑定执行客户端；请在节点设置中选择可执行客户端，负责人和审核节点也需要绑定")
        if agent not in RUNNERS:
            raise ValueError(f"客户端“{agent}”目前仅支持装配，尚未接入自动执行；请在节点设置中选择可执行客户端")
        return self.resolvers[agent]()

    def list(self):
        rows = []
        for agent, metadata in RUNNERS.items():
            command = self.resolve(agent)
            rows.append({
                **metadata, "id": agent, "available": bool(command),
                "path": command[-1] if command else "",
            })
        return rows

    def models(self, agent, refresh=False):
        command = self.resolve(agent)
        if not command:
            return {"agent": agent, "models": [], "current_model": "", "note": "尚未找到执行客户端。"}
        environment = execution_environment()
        if agent == "zcode":
            zcode_environment(environment, Path(command[-1]))
        return self.model_catalog.get(agent, command, environment, refresh)

    def validate(self, agent, mode, model):
        metadata = RUNNERS.get(agent)
        if not metadata:
            self.resolve(agent)
        if not isinstance(mode, str) or mode not in {item["id"] for item in metadata["modes"]}:
            raise ValueError(f"{metadata['name']} 不支持所选权限模式")
        if agent == "zcode" and model:
            if model not in {row["id"] for row in self.models(agent, refresh=True)["models"]}:
                raise ValueError("Zcode 模型不在当前配置中，请刷新模型列表后重新选择")
        if agent == "cursor" and model:
            if model not in {row["id"] for row in self.models(agent)["models"]}:
                raise ValueError("Cursor 模型不在当前账号的模型列表中；请先登录 Cursor CLI，再刷新模型列表")
        command = self.resolve(agent)
        if not command:
            raise ValueError(f"未找到 {metadata['name']} 调用入口，请检查本机安装")
        return command


# A fixed Node bootstrap passes prompt text on stdin to the documented CLI
# argument, avoiding Windows' command-line length limit and shell interpretation.
ZCODE_BOOTSTRAP = (
    "const fs=require('node:fs');const entry=process.argv[1];"
    "const args=process.argv.slice(2);"
    "process.argv=[process.execPath,entry,...args,'--prompt',fs.readFileSync(0,'utf8')];"
    "require(entry);"
)


def zcode_environment(environment, script):
    """Follow the desktop's configured data directory without copying credentials."""
    base = environment.get("ZCODE_DATA_BASE_DIR")
    if not base:
        settings = Path.home() / ".zcode/v2/setting.json"
        if settings.is_file():
            value = json.loads(settings.read_text(encoding="utf-8-sig")).get("dataBaseDir")
            if isinstance(value, str) and value.strip():
                base = value
        base = base or str(Path.home())
    data = Path(base).expanduser()
    if not data.is_absolute() or not data.is_dir():
        raise ValueError("Zcode 桌面端设置的数据目录不存在，请在客户端检查数据位置")
    environment["ZCODE_DATA_BASE_DIR"] = str(data.resolve())
    provider = script.parent.parent / "config/provider/zcode-builtin.json"
    if provider.is_file():
        environment.setdefault("ZCODE_BUILTIN_PROVIDER_CONFIG_FILE", str(provider))
    personal = data / ".zcode/v2/provider_config.json"
    if personal.is_file():
        environment.setdefault("ZCODE_PERSONAL_PROVIDER_CONFIG_FILE", str(personal))
    proxy = environment.get("HTTPS_PROXY") or environment.get("HTTP_PROXY")
    if proxy:
        environment.setdefault("ZCODE_HTTP_PROXY", proxy)


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def same_directory(left, right):
    """True when both paths name the same directory after normalization."""
    if not isinstance(left, str) or not isinstance(right, str) or not left or not right:
        return False
    try:
        return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def cursor_workspace_untrusted(message):
    """Match Cursor's workspace-trust refusal, including color codes and nearby wording."""
    plain = _ANSI.sub("", str(message)).casefold()
    return (
        "workspace trust required" in plain
        or "do you trust the contents of this directory" in plain
        or "if you trust this directory" in plain
    )


def prepare_command(prefix, record, directory, environment):
    agent = record["agent"]
    if agent == "codex":
        command = [
            *prefix, "-a", "never", "exec", "--json", "--color", "never",
            "--skip-git-repo-check", "--sandbox", record["sandbox"],
            "--cd", record["cwd"], "--output-last-message", str(directory / "result.md"),
        ]
        if record["model"]:
            command += ["--model", record["model"]]
        return [*command, "-"]
    if agent == "deepseek-harness":
        mode = record["sandbox"]
        patch = [
            {"id": "sandbox-policy", "config": {"mode": mode, "workspaceRoot": record["cwd"]}},
            {"id": "approval", "config": {"policy": "never"}},
            {"id": "permission", "config": {
                "defaultPreset": "workbench",
                "presets": {"workbench": {"sandbox": mode, "approval": "never"}},
            }},
        ]
        if record["model"]:
            provider, separator, model = record["model"].partition("/")
            patch.append({"id": "agent-default-model", "config": (
                {"provider": provider, "model": model} if separator else {"model": provider}
            )})
        path = directory / "headless.patch.json"
        path.write_text(json.dumps(patch, ensure_ascii=False), encoding="utf-8")
        environment["DSH_PERMISSION_MODE"] = mode
        bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if bash.is_file():
            environment.setdefault("SHELL", str(bash))
        return [*prefix, "--profile", "headless", "--patch", str(path), "--json", "-"]
    if agent == "cursor":
        command = [*prefix, "--print", "--output-format", "stream-json", "--workspace", record["cwd"]]
        if same_directory(record.get("cursor_trusted_cwd", ""), record["cwd"]):
            command += ["--trust"]
        if record["sandbox"] == "plan":
            command += ["--mode", "plan"]
        if record["model"]:
            command += ["--model", record["model"]]
        # Only forward explicit confirmation of this exact workspace. Cursor
        # persists its own trust marker; tool approval stays with the client.
        return command
    script = Path(prefix[-1])
    zcode_environment(environment, script)
    if record["model"]:
        zcode_model_override(environment, record["model"], directory)
    return [
        prefix[0], "-e", ZCODE_BOOTSTRAP, *prefix[1:],
        "--mode", record["sandbox"], "--workflow-mode", "disabled",
        "--output-format", "stream-json", "--no-color", "--cwd", record["cwd"],
    ]


def decode_event(agent, event):
    """Normalize only explicit CLI completion/failure signals, never text guesses."""
    kind = event.get("type", "")
    change = {}
    if agent == "codex":
        if kind == "thread.started":
            change["thread_id"] = event.get("thread_id")
        if kind == "turn.completed":
            change.update(completed=True, usage=event.get("usage"))
        if kind == "turn.failed" or (kind == "error" and not isinstance(event.get("item"), dict)):
            change["failed"] = True
        return change
    if agent == "deepseek-harness":
        if kind == "session":
            change["thread_id"] = event.get("sessionId")
        elif kind == "final":
            change.update(completed=True, result=event.get("text", ""))
        elif kind == "error":
            change["failed"] = True
        elif kind == "status":
            if event.get("phase") == "turn_end":
                change["failed"] = event.get("reason", {}).get("kind") != "completed"
            if event.get("usage"):
                change["usage"] = event["usage"]
        change["log"] = (
            event.get("text", "") if kind in {"text", "final"} else
            event.get("message", "") if kind == "error" else
            f"工具：{event.get('tool', '')}" if kind == "tool_call" else
            f"工具结果：{event.get('result', '')}" if kind == "tool_result" else
            event.get("phase", kind) if kind != "thinking" else ""
        )
        return change
    if agent == "cursor":
        if event.get("session_id"):
            change["thread_id"] = event["session_id"]
        if kind == "result":
            success = event.get("subtype") == "success" and event.get("is_error") is False
            change.update(completed=success, failed=not success,
                          result=event.get("result", ""), usage=event.get("usage"))
            change["log"] = event.get("result", "")
        elif kind == "assistant":
            content = (event.get("message") or {}).get("content") or []
            change["log"] = "\n".join(item.get("text", "") for item in content
                                     if isinstance(item, dict) and item.get("type") == "text")
        elif kind == "tool_call":
            change["log"] = f"工具调用 · {event.get('subtype', '')}"
        elif kind == "error":
            change.update(failed=True, log=event.get("message", "Cursor 返回错误"))
        return change
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    if event.get("sessionId"):
        change["thread_id"] = event["sessionId"]
    if kind == "result":
        change.update(completed=True, result=event.get("response", ""), usage=event.get("usage"))
        if event.get("projection", {}).get("status") in {"error", "failed", "cancelled"}:
            change["failed"] = True
    if kind in {"turn.failed", "turn.error"} or (
        kind == "error" and not isinstance(event.get("item"), dict)
    ):
        change["failed"] = True
    issue = payload.get("error") or {}
    if not isinstance(issue, dict):
        issue = {}
    change["log"] = "" if any(word in kind for word in ("thinking", "reasoning")) else (
        issue.get("underlyingErrorMessage") or issue.get("message")
        or event.get("response") or payload.get("response") or payload.get("text")
        or payload.get("delta") or payload.get("message") or kind
    )
    return change
