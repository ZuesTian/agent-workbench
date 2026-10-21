import json
from pathlib import Path

import pytest

from agent_workbench import discovery
from agent_workbench.adapters import Agent, discover_agents, mcp_entries, read_document, translate
from agent_workbench.catalog import Catalog
from agent_workbench.dsh import read_static_mcps
from agent_workbench.engine import Engine
from agent_workbench.service import WorkbenchService


def write(path, content=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def catalog(tmp_path):
    return Catalog(tmp_path / "home", tmp_path / "roaming", tmp_path / "data")


def portable(catalog, root):
    catalog.settings["install_roots"] = [str(root)]


def test_leftover_is_kept_as_source_but_not_counted_as_program(tmp_path):
    c = catalog(tmp_path)
    write(c.home / ".cursor/mcp.json", '{"mcpServers":{"old":{"command":"python"}}}')
    write(c.home / ".cursor/skills/kept/SKILL.md", "A retained skill")
    s = c.scan()
    assert s["counts"]["installed_agents"] == 0
    assert s["counts"]["config_only_agents"] == 1
    assert s["agents"][0]["installation_status"] == "config_only"
    assert len(s["skills"]) == len(s["mcps"]) == 1


def test_portable_program_without_config_and_removal_refresh(tmp_path):
    c = catalog(tmp_path)
    path = write(tmp_path / "other-drive/apps/deep/DeepSeek Harness.exe")
    zcode = write(tmp_path / "other-drive/Zcode/ZCode.exe")
    portable(c, tmp_path / "other-drive")
    s = c.scan()
    assert {a["id"] for a in s["agents"]} == {"deepseek-harness", "zcode"}
    assert s["counts"]["installed_agents"] == 2
    assert all(not a["config_exists"] for a in s["agents"])
    zcode.unlink()
    write(c.home / ".zcode/cli/config.json", "{}")
    s = c.scan()
    assert s["counts"]["installed_agents"] == 1
    assert (
        next(a for a in s["agents"] if a["id"] == "zcode")["installation_status"]
        == "config_only"
    )
    path.unlink()
    assert c.scan()["counts"]["installed_agents"] == 0


def test_registry_other_drive_and_stale_uninstall_record(tmp_path, monkeypatch):
    root = tmp_path / "G"
    exe = write(root / "Zcode/ZCode.exe")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(
        discovery,
        "registry_records",
        lambda: [
            {
                "DisplayName": "ZCode 3.15.3",
                "DisplayIcon": str(exe.parent / "uninstallerIcon.ico"),
                "UninstallString": f'"{exe.parent / "Uninstall ZCode.exe"}" /allusers',
            },
            {
                "DisplayName": "Cursor (User)",
                "InstallLocation": str(root / "deleted-cursor"),
            },
        ],
    )
    monkeypatch.setattr(discovery, "process_paths", lambda: [])
    monkeypatch.setattr(discovery, "fixed_drives", lambda: [])
    monkeypatch.setattr(discovery.shutil, "which", lambda _: None)
    found, _ = discovery.probe_installations(tmp_path / "home", tmp_path / "roaming")
    assert set(found) == {"zcode"}
    assert Path(found["zcode"][0]["path"]) == exe
    exe.unlink()
    assert not discovery.probe_installations(tmp_path / "home", tmp_path / "roaming")[0]


def test_process_path_disambiguates_cli_and_desktop_and_trae_variants(tmp_path):
    assert discovery._path_agent(tmp_path / "Claude/claude.exe") == "claude-desktop"
    assert discovery._path_agent(tmp_path / ".local/bin/claude.exe") == "claude"
    assert discovery._path_agent(tmp_path / "Trae CN/Trae.exe") == "trae-cn"
    assert discovery._path_agent(tmp_path / "Trae/Trae.exe") == "trae"


def test_program_root_validation_and_directory_config_rejected(tmp_path):
    c = catalog(tmp_path)
    service = WorkbenchService(c)
    directory = tmp_path / "G/apps"
    directory.mkdir(parents=True)
    write(directory / "ZCode.exe")
    result = service.add_install_root({"path": str(directory)})
    assert result["counts"]["installed_agents"] == 1
    assert service.add_install_root({"path": str(directory)})["install_roots"] == [
        str(directory)
    ]
    with pytest.raises(ValueError, match="不能填写"):
        service.add_agent({"name": "DSH", "config": str(directory)})
    result = service.remove_install_root({"path": str(directory)})
    assert result["counts"]["installed_agents"] == 0


@pytest.mark.parametrize("mode", ["merge", "replace_mcp"])
def test_zcode_nested_mcp_apply_restore_preserves_other_settings(tmp_path, mode):
    c = catalog(tmp_path)
    source = c.home / ".cursor/mcp.json"
    write(source, '{"mcpServers":{"lab":{"command":"python","args":["tool.py"]}}}')
    target = c.home / ".zcode/cli/config.json"
    original = '{"provider":{"id":"keep"},"mcp":{"enabled":true,"servers":{"old":{"command":"old"}}}}'
    write(target, original)
    c.scan()
    engine = Engine(c)
    mcp = next(x for x in c.mcps.values() if x["name"] == "lab")
    plan = engine.preview(
        {"agents": ["zcode"], "skills": [], "mcps": [mcp["id"]], "mode": mode}
    )
    assert not plan["errors"]
    engine.apply(plan["id"])
    result = json.loads(target.read_text())
    assert "mcp.servers" not in result and "mcpServers" not in result
    assert result["provider"] == {"id": "keep"}
    assert result["mcp"]["enabled"] is True
    assert result["mcp"]["servers"]["lab"]["type"] == "stdio"
    assert ("old" in result["mcp"]["servers"]) == (mode == "merge")
    history = engine.history()
    engine.restore(history[0]["id"])
    assert target.read_text() == original


def test_zcode_invalid_nested_parent_is_not_overwritten(tmp_path):
    c = catalog(tmp_path)
    target = write(c.home / ".zcode/cli/config.json", '{"mcp":"keep-invalid-value"}')
    s = c.scan()
    assert s["agents"][0]["error"]
    agent = next(a for a in discover_agents(c.home, c.roaming, []) if a.id == "zcode")
    with pytest.raises(ValueError, match="mcp"):
        mcp_entries(read_document(target, "zcode")[0], agent)


def test_harness_literal_inventory_skips_expressions_and_cycles():
    text = """
- insert:
  - name: '@deepseek-ai/dsh-mcp-client'
    config: {serverName: literal, transport: stdio, command: python}
  - name: '@deepseek-ai/dsh-mcp-client'
    config:
      serverName: dynamic
      transport: stdio
      command: !!js "process.exit(1)"
  - name: '@deepseek-ai/dsh-mcp-client'
    config: &loop {serverName: cycle, transport: stdio, command: python, env: *loop}
"""
    doc = read_static_mcps(text)
    assert set(doc["mcpServers"]) == {"literal"}
    assert len(doc["_scan_issues"]) == 2


def test_harness_mcp_write_is_blocked_but_skill_is_supported(tmp_path):
    c = catalog(tmp_path)
    write(c.home / ".dsh/profiles/desktop/cordis.patch.yml", "[]")
    write(c.home / ".cursor/skills/lab/SKILL.md", "Lab")
    write(c.home / ".cursor/mcp.json", '{"mcpServers":{"lab":{"command":"python"}}}')
    c.scan()
    engine = Engine(c)
    body = {"agents": ["deepseek-harness"], "mcps": list(c.mcps), "skills": []}
    plan = engine.preview(body)
    assert any("只读" in error for error in plan["errors"])
    assert not plan["operations"]
    plan = engine.preview({**body, "mcps": [], "skills": list(c.skills)})
    assert not plan["errors"]
    engine.apply(plan["id"])
    assert (c.home / ".dsh/skills/lab/SKILL.md").is_file()


def test_remote_zcode_keeps_url_headers_and_transport_without_writing():
    target = Agent("zcode", "Zcode", "", Path("unused"), "zcode", "mcp.servers")
    http, warnings = translate(
        {
            "url": "https://example.test/mcp",
            "headers": {"Authorization": "Bearer fake-token"},
            "timeoutMs": 1500,
        },
        "json",
        target,
    )
    assert http["url"] == "https://example.test/mcp"
    assert http["headers"]["Authorization"] == "Bearer fake-token"
    assert http["type"] == "http" and http["timeoutMs"] == 1500
    assert not any("Authorization" in warning for warning in warnings)
    sse, _warnings = translate(
        {"type": "sse", "url": "https://example.test/sse", "headers": {"X-Test": "1"}},
        "json",
        target,
    )
    assert sse["type"] == "sse" and sse["url"].endswith("/sse") and sse["headers"] == {"X-Test": "1"}
    with pytest.raises(ValueError, match="不能自动转换"):
        translate(
            {"url": "https://example.test/mcp", "bearer_token_env_var": "TOKEN"},
            "json",
            target,
        )


def _remote_plan(c, agent_id):
    write(c.home / ".cursor/mcp.json", '{"mcpServers":{"lab":{"url":"https://example.test/mcp","headers":{"Authorization":"Bearer fake-token"}}}}')
    before = {
        path: path.read_bytes()
        for path in (
            c.home / ".claude.json",
            c.home / ".claude/settings.json",
            c.home / ".cline/data/settings/cline_mcp_settings.json",
            c.roaming / "Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
            c.home / ".config/opencode/opencode.json",
            c.home / ".config/opencode/opencode.jsonc",
        )
        if path.is_file()
    }
    c.scan()
    engine = Engine(c)
    mcp = next(item for item in c.mcps.values() if item["name"] == "lab")
    plan = engine.preview({"agents": [agent_id], "skills": [], "mcps": [mcp["id"]], "mode": "merge"})
    for path, content in before.items():
        assert path.read_bytes() == content
    return engine, plan


def test_cline_uses_the_only_existing_install_and_refuses_two(tmp_path):
    c = catalog(tmp_path)
    extra = c.roaming / "Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json"
    write(extra, '{"mcpServers":{}}')
    engine, plan = _remote_plan(c, "cline")
    assert not plan["errors"]
    assert plan["operations"][0]["target"] == str(extra)
    assert "将写入已存在的配置" in " ".join(plan["warnings"])
    engine.apply(plan["id"])
    saved = json.loads(extra.read_text(encoding="utf-8"))
    assert saved["mcpServers"]["lab"]["url"] == "https://example.test/mcp"
    assert not (c.home / ".cline/data/settings/cline_mcp_settings.json").exists()
    primary = c.home / ".cline/data/settings/cline_mcp_settings.json"
    write(primary, '{"mcpServers":{"keep":{"command":"local"}}}')
    extra_bytes = extra.read_bytes()
    primary_bytes = primary.read_bytes()
    _engine, ambiguous = _remote_plan(c, "cline")
    assert any("多份配置并存" in error for error in ambiguous["errors"])
    assert extra.read_bytes() == extra_bytes and primary.read_bytes() == primary_bytes


def test_opencode_jsonc_is_distinct_from_json(tmp_path):
    c = catalog(tmp_path)
    jsonc = c.home / ".config/opencode/opencode.jsonc"
    write(jsonc, '{"mcp":{}}')
    engine, plan = _remote_plan(c, "opencode")
    assert not plan["errors"]
    assert plan["operations"][0]["target"] == str(jsonc)
    engine.apply(plan["id"])
    assert not (c.home / ".config/opencode/opencode.json").exists()
    assert "example.test" in jsonc.read_text(encoding="utf-8")
    write(c.home / ".config/opencode/opencode.json", '{"mcp":{}}')
    json_bytes = (c.home / ".config/opencode/opencode.json").read_bytes()
    jsonc_bytes = jsonc.read_bytes()
    _engine, ambiguous = _remote_plan(c, "opencode")
    assert any("多份配置并存" in error for error in ambiguous["errors"])
    assert (c.home / ".config/opencode/opencode.json").read_bytes() == json_bytes
    assert jsonc.read_bytes() == jsonc_bytes


def test_claude_keeps_the_user_level_file_when_settings_also_exist(tmp_path):
    c = catalog(tmp_path)
    settings = c.home / ".claude/settings.json"
    write(settings, '{"permissions":{"allow":[]}}')
    settings_bytes = settings.read_bytes()
    engine, plan = _remote_plan(c, "claude")
    assert not plan["errors"]
    assert plan["operations"][0]["target"] == str(c.home / ".claude.json")
    assert "用户级配置" in " ".join(plan["warnings"])
    engine.apply(plan["id"])
    assert settings.read_bytes() == settings_bytes
    document = json.loads((c.home / ".claude.json").read_text(encoding="utf-8"))
    assert document["mcpServers"]["lab"]["headers"]["Authorization"] == "Bearer fake-token"


@pytest.mark.parametrize("agent_id", ["cline", "opencode"])
@pytest.mark.parametrize("selected_index", [0, 1])
def test_explicit_config_choice_writes_and_restores_only_the_chosen_file(tmp_path, agent_id, selected_index):
    c = catalog(tmp_path)
    if agent_id == "cline":
        paths = [c.home / ".cline/data/settings/cline_mcp_settings.json",
                 c.roaming / "Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json"]
        key = "mcpServers"
    else:
        paths = [c.home / ".config/opencode/opencode.json", c.home / ".config/opencode/opencode.jsonc"]
        key = "mcp"
    for path in paths:
        write(path, json.dumps({key: {}, "unrelated": path.name}))
    original = {path: path.read_bytes() for path in paths}
    engine, ambiguous = _remote_plan(c, agent_id)
    assert ambiguous["errors"] and ambiguous["operation_count"] == 0
    assert set(ambiguous["config_choices"][0]["paths"]) == {str(path) for path in paths}
    assert ambiguous["config_choices"][0]["selected"] == ""
    with pytest.raises(ValueError, match="过期"):
        engine.apply(ambiguous["id"])
    mcp = next(item for item in c.mcps.values() if item["name"] == "lab")
    body = {"agents": [agent_id], "mcps": [mcp["id"]], "config_targets": {agent_id: str(paths[selected_index])}}
    plan = engine.preview(body)
    assert not plan["errors"] and plan["operations"][0]["target"] == str(paths[selected_index])
    assert plan["config_choices"][0]["selected"] == str(paths[selected_index])
    tx = engine.apply(plan["id"])["transaction"]
    assert paths[1 - selected_index].read_bytes() == original[paths[1 - selected_index]]
    assert "lab" in json.loads(paths[selected_index].read_text(encoding="utf-8"))[key]
    engine.restore(tx)
    assert {path: path.read_bytes() for path in paths} == original
    invalid = engine.preview({**body, "config_targets": {agent_id: str(c.home / "unrelated.json")}})
    assert any("不属于可写位置" in error for error in invalid["errors"])
    assert not (c.home / "unrelated.json").exists()
    paths[selected_index].unlink()
    stale = engine.preview(body)
    assert any("已失效" in error for error in stale["errors"])
    assert not paths[selected_index].exists()
