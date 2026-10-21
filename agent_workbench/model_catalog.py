"""Read installed clients' model metadata without contacting an inference API."""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import threading
import time
import tomllib
from pathlib import Path

import yaml

from .dsh import LiteralLoader


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def walk(value, seen=None):
    seen = set() if seen is None else seen
    if not isinstance(value, (dict, list)) or id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, dict):
        yield value
        values = value.values()
    else:
        values = value
    for child in values:
        yield from walk(child, seen)


def model_row(ident, name=None, provider=""):
    if not isinstance(ident, str) or not ident.strip() or len(ident) > 160:
        return None
    return {"id": ident, "name": name if isinstance(name, str) else ident,
            "provider": provider}


def codex_models(environment):
    home = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")
    config_path = home / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.exists() else {}
    profile = config.get("profiles", {}).get(config.get("profile"), {})
    current = profile.get("model", config.get("model", ""))
    provider = profile.get("model_provider", config.get("model_provider", "openai"))
    catalog_path = profile.get("model_catalog_json", config.get("model_catalog_json"))
    # OpenAI's account cache is not the model catalog of a custom provider.
    path = Path(catalog_path).expanduser() if catalog_path else home / "models_cache.json"
    if not path.is_absolute():
        path = home / path
    data = read_json(path) if path.exists() and (catalog_path or provider == "openai") else {}
    rows = [
        model_row(item.get("slug"), item.get("display_name"), provider)
        for item in data.get("models", [])
        if isinstance(item, dict) and item.get("visibility", "list") == "list"
    ]
    if current and not any(row and row["id"] == current for row in rows):
        rows.insert(0, model_row(current, provider=provider))
    return rows, current, "来自 Codex 本地模型目录与当前配置；账号权限由客户端确认。"


def cli_output(command, *args, environment=None):
    result = subprocess.run(
        [*command, *args], capture_output=True, encoding="utf-8",
        timeout=25, env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        # Dumps can contain credentials; never include stdout/stderr in API errors.
        raise ValueError("无法读取客户端的模型配置")
    return result.stdout


def cursor_models(command, environment):
    status = json.loads(cli_output(command, "status", "--format", "json", environment=environment))
    if status.get("isAuthenticated") is not True:
        return [], "", "Cursor Agent CLI 尚未登录。请运行 cursor-agent login 完成官方登录，再刷新模型列表。"
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", cli_output(command, "models", environment=environment))
    rows, current, default, listing = [], "", "", False
    for line in output.splitlines():
        line = line.strip()
        if line == "Available models":
            listing = True
            continue
        if line.startswith("Tip:"):
            break
        if not listing or not line:
            continue
        match = re.fullmatch(
            r"([A-Za-z0-9][A-Za-z0-9._:/+\-\[\],=]*)(?: - (.+?))?"
            r"(?: \((current(?:, default)?|default)\))?", line,
        )
        if not match:
            continue
        ident, name, flags = match.groups()
        rows.append(model_row(ident, name, "Cursor"))
        if flags and "current" in flags:
            current = ident
        if flags and "default" in flags:
            default = ident
    return rows, current or default, "来自 Cursor CLI 当前账号的可用模型；未指定时沿用 CLI 设置。"


def dsh_models(command):
    document = yaml.load(cli_output(command, "--profile", "headless", "--dump-config"),
                         Loader=LiteralLoader)
    entries = [item for item in walk(document) if isinstance(item.get("name"), str)
               and item.get("disabled", False) is False]
    default = next((item.get("config", {}) for item in entries
                    if item.get("id") == "agent-default-model"), {})
    provider, selected = default.get("provider"), default.get("model")
    current = f"{provider}/{selected}" if isinstance(provider, str) and isinstance(selected, str) else ""
    rows = []
    for entry in entries:
        config = entry.get("config") or {}
        if entry["name"] == "@deepseek-ai/dsh-llm-deepseek":
            models = config.get("models")
            if models is None:
                schema = json.loads(cli_output(command, "--profile", "headless", "--dump-config-schema"))
                # The installed schema supplies the built-in catalog; no model names
                # are frozen into Workbench or guessed from a provider's website.
                models = next((item["properties"]["models"]["default"] for item in walk(schema)
                               if isinstance(item.get("properties"), dict)
                               and isinstance(item["properties"].get("models"), dict)
                               and isinstance(item["properties"]["models"].get("default"), list)
                               and item["properties"]["models"]["default"]
                               and all(isinstance(m, dict) and "id" in m
                                       for m in item["properties"]["models"]["default"])), [])
            if isinstance(models, list):
                rows.extend(model_row(f"deepseek-official/{m['id']}", m.get("name", m["id"]), "DeepSeek")
                            for m in models if isinstance(m, dict) and isinstance(m.get("id"), str))
        elif entry["name"] == "@deepseek-ai/dsh-llm-pi-ai":
            providers = config.get("providers", {})
            if isinstance(providers, dict):
                for key, value in providers.items():
                    if not isinstance(value, dict):
                        continue
                    for model in value.get("models", []):
                        if isinstance(model, dict) and isinstance(model.get("id"), str):
                            rows.append(model_row(f"{key}/{model['id']}", model.get("name", model["id"]),
                                                  value.get("displayName", key)))
    if current and not any(row and row["id"] == current for row in rows):
        rows.insert(0, model_row(current, selected, provider))
    return rows, current, "来自实际执行使用的 Harness headless 配置与已安装模型目录。"


def zcode_models(environment):
    path = environment.get("ZCODE_PERSONAL_PROVIDER_CONFIG_FILE")
    if not path:
        return [], "", "未找到 Zcode 模型配置；可沿用客户端当前设置。"
    document = read_json(path)
    config = document["config"]
    providers = config.get("providerConfigRules", {}).get("providerRules", [])
    rules = config.get("modelConfigRules", {})
    models = [*rules.get("providerModelRules", []), *rules.get("manualProviderModelRules", [])]
    by_model = {(m.get("providerId"), m.get("modelId")): m.get("config", {}) for m in models}
    rows = []
    for provider in providers:
        ident, settings = provider.get("providerId"), provider.get("config", {})
        if not isinstance(ident, str) or settings.get("enabled") is False:
            continue
        ids = list(settings.get("personalModelIds", []))
        ids += [m.get("modelId") for m in models if m.get("providerId") == ident]
        for model in dict.fromkeys(ids):
            if not isinstance(model, str) or by_model.get((ident, model), {}).get("enabled") is False:
                continue
            rows.append(model_row(f"{ident}/{model}", model, provider.get("providerName", ident)))
    selected = config.get("defaultModelSelection") or {}
    current = (f"{selected['providerId']}/{selected['modelId']}"
               if selected.get("providerId") and selected.get("modelId") else "")
    return rows, current, "来自 Zcode 当前数据目录中已配置的模型；未指定时沿用客户端选择。订阅内置模型可在 Zcode 中选定后沿用。"


def zcode_model_override(environment, model, directory):
    """Use the client's supported per-process provider configuration path."""
    rows, _, _ = zcode_models(environment)
    if model not in {row["id"] for row in rows if row}:
        raise ValueError("Zcode 模型不在当前配置中，请刷新模型列表后重新选择")
    source = Path(environment["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"])
    document = read_json(source)
    provider, ident = model.split("/", 1)
    document["config"]["defaultModelSelection"] = {"providerId": provider, "modelId": ident}
    path = directory / "zcode-model.json"
    write_private_text(path, json.dumps(document, ensure_ascii=False))
    environment["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"] = str(path)


def write_private_text(path, text):
    """Write a temporary credential file that other users cannot read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    restrict_private_file(path)


def restrict_private_file(path):
    if os.name != "nt":
        os.chmod(path, 0o600)
        return
    user = os.environ.get("USERNAME")
    if not user:
        raise OSError("无法确定当前用户，不能收紧临时凭据文件的权限")

    def icacls(*args):
        completed = subprocess.run(["icacls", *args], capture_output=True, check=False)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).decode("gbk", errors="replace").strip()
            raise OSError(detail or "无法限制临时凭据文件的访问")

    icacls(str(path), "/inheritance:r")
    icacls(str(path), "/grant:r", f"{user}:(R,W,D)")
    for sid in ("*S-1-1-0", "*S-1-5-32-545"):
        subprocess.run(["icacls", str(path), "/remove", sid], capture_output=True, check=False)


class ModelCatalog:
    def __init__(self):
        self.cache = {}
        self.lock = threading.Lock()

    def get(self, agent, command, environment, refresh=False):
        with self.lock:
            cached = self.cache.get(agent)
            if cached and not refresh and time.monotonic() - cached[0] < 300:
                return copy.deepcopy(cached[1])
        try:
            if agent == "codex":
                rows, current, note = codex_models(environment)
            elif agent == "deepseek-harness":
                rows, current, note = dsh_models(command)
            elif agent == "cursor":
                rows, current, note = cursor_models(command, environment)
            else:
                rows, current, note = zcode_models(environment)
            unique = {row["id"]: row for row in rows if row}
            result = {"agent": agent, "models": list(unique.values()),
                      "current_model": current, "note": note}
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, yaml.YAMLError):
            return {"agent": agent, "models": [], "current_model": "",
                    "note": "暂时无法读取本机模型目录。可刷新重试，或沿用客户端当前模型。"}
        with self.lock:
            self.cache[agent] = (time.monotonic(), result)
        return copy.deepcopy(result)
