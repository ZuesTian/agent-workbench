"""Machine changes must alter discovery and actual launch/config targets together."""

import json
import shutil
import uuid
from pathlib import Path

import pytest

from agent_workbench import discovery, locations, runners
from agent_workbench.adapters import discover_agents
from agent_workbench.catalog import Catalog
from agent_workbench.data_location import control_root
from agent_workbench.engine import Engine
from agent_workbench.model_catalog import codex_models
from agent_workbench.service import WorkbenchService
from launcher import runtime_root


def write(path, value=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def machine(tmp_path, monkeypatch):
    node = shutil.which("node")
    home, roaming, local = tmp_path / "new-user", tmp_path / "roaming", tmp_path / "local"
    for directory in (home, roaming, local):
        directory.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("PATH", str(tmp_path / "path-bin"))
    for variable in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR",
                     "NPM_CONFIG_PREFIX", "NPM_CONFIG_USERCONFIG", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(discovery, "registry_records", lambda: [])
    monkeypatch.setattr(runners, "registry_records", lambda: [])
    monkeypatch.setattr(discovery, "process_paths", lambda: [])
    monkeypatch.setattr(discovery, "fixed_drives", lambda: [])
    monkeypatch.setattr(shutil, "which", lambda name: node if name == "node" else None)
    return home, roaming, local


@pytest.mark.parametrize("location", ["prefix_env", "npmrc", "path"])
def test_npm_install_location_is_shared_by_scan_and_runner(machine, tmp_path, monkeypatch, location):
    home, roaming, _ = machine
    prefix = tmp_path / "another-drive/中文 tools"
    script = write(prefix / "node_modules/@openai/codex/new-cli/main.cjs")
    write(script.parents[1] / "package.json", {"name": "@openai/codex", "bin": {"codex": "new-cli/main.cjs"}})
    if location == "prefix_env":
        monkeypatch.setenv("NPM_CONFIG_PREFIX", str(prefix))
    elif location == "npmrc":
        write(home / ".npmrc", f'prefix="{prefix}"\n//example.test/:_authToken=must-not-read-as-command\n')
    else:
        monkeypatch.setenv("PATH", str(prefix))
    found, _ = discovery.probe_installations(home, roaming)
    assert any(Path(item["path"]) == script for item in found["codex"])
    assert Path(runners.codex_command()[-1]) == script
    script.unlink()
    assert runners.codex_command() is None


def test_custom_npm_prefix_runs_a_real_fixture_process(machine, tmp_path, monkeypatch):
    prefix = tmp_path / "portable npm"
    script = write(prefix / "node_modules/@openai/codex/bin/codex.js", """
const fs = require('node:fs');
const args = process.argv.slice(2);
fs.writeFileSync(args[args.indexOf('--output-last-message')+1], 'adaptive ' + fs.readFileSync(0, 'utf8'));
console.log(JSON.stringify({type:'turn.completed'}));
""")
    write(script.parents[1] / "package.json", {"name": "@openai/codex", "type": "commonjs", "bin": {"codex": "bin/codex.js"}})
    monkeypatch.setenv("NPM_CONFIG_PREFIX", str(prefix))
    catalog = Catalog(data=tmp_path / "data")
    service = WorkbenchService(catalog)
    work = tmp_path / "project"
    work.mkdir()
    try:
        service.runs.start({"request_id": uuid.uuid4().hex, "prompt": "current machine", "cwd": str(work)})
        service.runs.worker.join(10)
        record = service.runs.list()[0]
        assert record["status"] == "succeeded"
        assert service.runs.get(record["id"])["result"] == "adaptive current machine"
        assert record["runtime"]["entrypoints"][-1]["path"] == str(script)
    finally:
        service.workflows.close()
        service.conversations.close()
        service.runs.close()
        catalog.close()


def test_portable_zcode_discovery_is_used_by_execution(machine, tmp_path):
    home, roaming, _ = machine
    install = tmp_path / "other-drive/portable/ZCode"
    write(install / "ZCode.exe")
    script = write(install / "resources/glm/zcode.cjs")
    catalog = Catalog(home, roaming, tmp_path / "data", system_scan=False)
    catalog.settings["install_roots"] = [str(install.parent)]
    service = WorkbenchService(catalog)
    try:
        assert catalog.scan()["counts"]["installed_agents"] == 1
        assert Path(service.runs.registry.resolve("zcode")[-1]) == script
        script.unlink()
        assert service.runs.registry.resolve("zcode") is None
    finally:
        service.workflows.close()
        service.conversations.close()
        service.runs.close()
        catalog.close()


def test_desktop_binary_is_not_treated_as_a_codex_cli(machine, tmp_path):
    desktop = write(tmp_path / "portable/Codex.exe")
    assert runners.codex_command([{"path": str(desktop), "kind": "executable"}]) is None


def test_native_harness_does_not_require_node(machine, tmp_path, monkeypatch):
    binary = write(tmp_path / "tools/dsh.exe")
    monkeypatch.setattr(shutil, "which", lambda name: str(binary) if name == "dsh.exe" else None)
    assert runners.dsh_command() == [str(binary)]


def test_redirected_codex_home_is_used_for_inventory_models_and_apply(machine, tmp_path, monkeypatch):
    home, _, _ = machine
    custom = tmp_path / "different-drive/codex-config"
    target = write(custom / "config.toml", 'model = "local-model"\n')
    write(custom / "skills/local/SKILL.md", "Use the current machine")
    untouched = write(home / ".codex/config.toml", 'model = "old-location"\n')
    write(home / ".cursor/mcp.json", '{"mcpServers":{"example":{"command":"python"}}}')
    monkeypatch.setenv("CODEX_HOME", str(custom))
    catalog = Catalog(data=tmp_path / "data")
    try:
        catalog.scan()
        agent = catalog.agents["codex"]
        assert agent.config == target
        assert any(Path(item["path"]) == custom / "skills/local" for item in catalog.skills.values())
        assert codex_models({"CODEX_HOME": str(custom)})[1] == "local-model"
        engine = Engine(catalog)
        plan = engine.preview({"agents": ["codex"], "mcps": list(catalog.mcps)})
        assert not plan["errors"]
        transaction = engine.apply(plan["id"])["transaction"]
        assert "mcp_servers.example" in target.read_text(encoding="utf-8")
        assert untouched.read_text(encoding="utf-8") == 'model = "old-location"\n'
        engine.restore(transaction)
        assert target.read_text(encoding="utf-8") == 'model = "local-model"\n'
    finally:
        catalog.close()


def test_config_environment_change_invalidates_the_display_cache(machine, tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    write(first / "config.toml", 'model="first"')
    write(second / "config.toml", 'model="second"')
    monkeypatch.setenv("CODEX_HOME", str(first))
    previous = Catalog(data=tmp_path / "data")
    previous.scan()
    previous.close()
    monkeypatch.setenv("CODEX_HOME", str(second))
    current = Catalog(data=tmp_path / "data")
    try:
        assert current._display_snapshot is None
        current.scan()
        assert current.agents["codex"].config == second / "config.toml"
    finally:
        current.close()


def test_explicit_catalog_roots_stay_isolated_from_host_overrides(machine, tmp_path, monkeypatch):
    home, roaming, _ = machine
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "host-private"))
    catalog = Catalog(home, roaming, tmp_path / "data")
    try:
        catalog.scan()
        assert catalog.agents["codex"].config == home / ".codex/config.toml"
    finally:
        catalog.close()


def test_claude_and_opencode_config_overrides(machine, tmp_path):
    home, roaming, _ = machine
    claude, xdg, custom = (tmp_path / name for name in ("claude-profile", "xdg", "opencode-custom"))
    extra = tmp_path / "config/opencode.json"
    agents = {agent.id: agent for agent in discover_agents(home, roaming, [], environment={
        "CLAUDE_CONFIG_DIR": str(claude), "XDG_CONFIG_HOME": str(xdg),
        "OPENCODE_CONFIG_DIR": str(custom), "OPENCODE_CONFIG": str(extra),
    })}
    assert agents["claude"].config == claude / ".claude.json"
    assert agents["claude"].skills == claude / "skills"
    assert agents["opencode"].config == xdg / "opencode/opencode.json"
    assert agents["opencode"].skills == custom / "skills"
    assert extra in agents["opencode"].extra_configs


def test_program_files_and_user_data_follow_this_machine(machine, tmp_path, monkeypatch):
    home, _, _ = machine
    programs = tmp_path / "new-drive/Applications"
    node = write(programs / "nodejs/node.exe")
    bash = write(programs / "Git/bin/bash.exe")
    edge = write(programs / "Microsoft/Edge/Application/msedge.exe")
    monkeypatch.setenv("ProgramFiles", str(programs))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(locations, "registered_app", lambda name: None)
    assert locations.node_executable() == str(node)
    assert locations.git_bash_executable() == str(bash)
    assert locations.edge_executable() == edge
    monkeypatch.delenv("LOCALAPPDATA")
    assert control_root() == runtime_root() == home / "AppData/Local/AgentWorkbench"


def test_invalid_npm_metadata_does_not_hide_another_installation(machine, tmp_path, monkeypatch):
    first, second = tmp_path / "broken", tmp_path / "healthy"
    write(first / "node_modules/@deepseek-ai/dsh/package.json", "{invalid")
    healthy = write(second / "node_modules/@deepseek-ai/dsh/cli.cjs")
    write(healthy.parent / "package.json", {"name": "@deepseek-ai/dsh", "bin": {"dsh": "cli.cjs"}})
    monkeypatch.setenv("NPM_CONFIG_PREFIX", str(first))
    monkeypatch.setenv("PATH", str(second))
    assert runners.dsh_command()[-1] == str(healthy)


def test_npm_bin_cannot_escape_its_package(machine, tmp_path):
    prefix = tmp_path / "npm"
    write(prefix / "node_modules/@openai/outside.js")
    write(prefix / "node_modules/@openai/codex/package.json", {"bin": {"codex": "../outside.js"}})
    assert list(locations.npm_entrypoints("@openai/codex", "codex", "bin/codex.js", prefixes=[prefix])) == []
