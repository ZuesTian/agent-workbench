import json
import os
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from agent_workbench.adapters import Agent, load_document, translate
from agent_workbench.catalog import Catalog
from agent_workbench.engine import Engine
from agent_workbench.resources import digest, path_identity
from agent_workbench.profiles import Profiles
import agent_workbench.engine as engine_module
from agent_workbench.server import WorkbenchServer


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        value if isinstance(value, str) else json.dumps(value), encoding="utf-8"
    )


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=b""))
    home, roaming, data = tmp_path / "home", tmp_path / "roaming", tmp_path / "data"
    write(
        home / ".codex/config.toml",
        '# keep this comment\nmodel = "do-not-change"\n[mcp_servers.existing]\ncommand = "old-tool"\n',
    )
    write(
        home / ".claude.json",
        {
            "model": "preserve-me",
            "projects": {"private": {"lastCost": 0.1}},
            "mcpServers": {
                "research": {
                    "command": "python",
                    "args": ["secret-argument"],
                    "env": {"API_KEY": "VERY_SECRET_TEST_VALUE"},
                }
            },
        },
    )
    write(
        home / ".cursor/mcp.json",
        {
            "other": {"preserved": True},
            "mcpServers": {
                "existing-cursor": {"command": "node", "args": ["server.js"]}
            },
        },
    )
    write(
        home / ".claude/skills/lab/SKILL.md",
        "---\nname: lab\ndescription: Scientific helper\n---\nRead the reference.\n",
    )
    write(home / ".claude/skills/lab/references/evidence.txt", "reference asset")
    c = Catalog(home, roaming, data)
    c.scan()
    return c, Engine(c)


def choose(c, **kwargs):
    skill = next(x for x in c.skills.values() if x["name"] == "lab")
    mcp = next(x for x in c.mcps.values() if x["name"] == "research")
    return {"skills": [skill["id"]], "mcps": [mcp["id"]], "agents": ["codex"], **kwargs}


def test_catalog_is_read_only_and_hides_credentials(app):
    c, _ = app
    before = digest(c.home / ".claude.json")
    result = json.dumps(c.scan())
    assert "VERY_SECRET_TEST_VALUE" not in result and "secret-argument" not in result
    assert "API_KEY" in result
    assert digest(c.home / ".claude.json") == before


def test_apply_full_skill_assets_preserve_other_settings_and_restore_bytes(app):
    c, engine = app
    config = c.home / ".codex/config.toml"
    before = config.read_bytes()
    plan = engine.preview(choose(c))
    assert not plan["errors"] and plan["operation_count"] == 2
    result = engine.apply(plan["id"])
    doc = load_document(config, "codex")
    assert doc["model"] == "do-not-change"
    assert "# keep this comment" in config.read_text()
    assert doc["mcp_servers"]["research"]["env"]["API_KEY"] == "VERY_SECRET_TEST_VALUE"
    assert "existing" in doc["mcp_servers"]
    target = c.home / ".agents/skills/lab"
    assert (target / "references/evidence.txt").read_text() == "reference asset"
    assert digest(target) == digest(c.home / ".claude/skills/lab")
    engine.restore(result["transaction"])
    assert config.read_bytes() == before
    assert not target.exists()


def test_selected_only_mode_changes_only_mcp_section(app):
    c, engine = app
    plan = engine.preview(choose(c, skills=[], agents=["cursor"], mode="replace_mcp"))
    assert not plan["errors"]
    engine.apply(plan["id"])
    doc = load_document(c.home / ".cursor/mcp.json", "json")
    assert doc["other"] == {"preserved": True}
    assert set(doc["mcpServers"]) == {"research"}


def test_target_changed_after_preview_is_not_overwritten(app):
    c, engine = app
    plan = engine.preview(choose(c))
    p = c.home / ".codex/config.toml"
    p.write_text(p.read_text() + "\n# another app wrote this\n")
    before = p.read_bytes()
    with pytest.raises(ValueError, match="目标.*变化"):
        engine.apply(plan["id"])
    assert p.read_bytes() == before


def test_source_changed_after_scan_blocks_preview(app):
    c, engine = app
    body = choose(c)
    p = c.home / ".claude.json"
    doc = json.loads(p.read_text())
    doc["mcpServers"]["research"]["command"] = "new"
    write(p, doc)
    plan = engine.preview(body)
    assert plan["errors"] and "来源配置已变化" in plan["errors"][0]


def test_source_changed_after_preview_is_not_used(app):
    c, engine = app
    plan = engine.preview(choose(c))
    write(
        c.home / ".claude/skills/lab/references/evidence.txt", "changed after preview"
    )
    with pytest.raises(ValueError, match="来源已变化"):
        engine.apply(plan["id"])
    assert not (c.home / ".agents/skills/lab").exists()


def test_conflict_requires_explicit_overwrite(app):
    c, engine = app
    write(c.home / ".agents/skills/lab/SKILL.md", "my own skill")
    plan = engine.preview(choose(c))
    assert any("同名 Skill" in s for s in plan["errors"])
    plan = engine.preview(choose(c, overwrite=True))
    assert not plan["errors"]
    tx = engine.apply(plan["id"])["transaction"]
    engine.restore(tx)
    assert (c.home / ".agents/skills/lab/SKILL.md").read_text() == "my own skill"


def test_failed_second_write_rolls_back_first(app, monkeypatch):
    c, engine = app
    config = c.home / ".codex/config.toml"
    before = config.read_bytes()
    plan = engine.preview(choose(c))
    real_replace = engine_module.replace_resource

    def fail(source, target, expected, before, identity, **kwargs):
        if target.name == "lab":
            raise OSError("test failure")
        return real_replace(source, target, expected, before, identity, **kwargs)

    monkeypatch.setattr(engine_module, "replace_resource", fail)
    with pytest.raises(ValueError, match="已回滚"):
        engine.apply(plan["id"])
    assert config.read_bytes() == before
    assert not (c.home / ".agents/skills/lab").exists()
    assert engine.history()[0]["status"] == "rolled_back"


def test_restore_refuses_to_overwrite_new_user_edits(app):
    c, engine = app
    plan = engine.preview(choose(c))
    tx = engine.apply(plan["id"])["transaction"]
    p = c.home / ".agents/skills/lab/references/evidence.txt"
    write(p, "my new work")
    with pytest.raises(ValueError, match="又被修改"):
        engine.restore(tx)
    assert p.read_text() == "my new work"


def test_restore_checks_backup_integrity(app):
    c, engine = app
    plan = engine.preview(choose(c))
    tx = engine.apply(plan["id"])["transaction"]
    write(c.data / "backups" / tx / "0.before", "tampered")
    with pytest.raises(ValueError, match="备份校验失败"):
        engine.restore(tx)


def test_duplicate_name_variants_are_not_ambiguous(app):
    c, engine = app
    write(
        c.home / ".claude/skills/another-lab/SKILL.md",
        "---\nname: lab\n---\nAnother version",
    )
    c.scan()
    # Different slugs can legitimately share frontmatter names; same install slug cannot.
    write(c.home / ".cursor/skills/lab/SKILL.md", "another lab")
    c.scan()
    skills = [s["id"] for s in c.skills.values() if s["slug"] == "lab"]
    with pytest.raises(ValueError, match="同名"):
        engine.preview(choose(c, skills=skills))


def test_opencode_conversion_preserves_command_arguments_and_env():
    target = Agent("opencode", "OpenCode", "", Path("unused"), "opencode", "mcp")
    out, warnings = translate(
        {"command": "python", "args": ["server.py"], "env": {"TOKEN": "secret"}},
        "json",
        target,
    )
    assert out["command"] == ["python", "server.py"] and out["environment"] == {
        "TOKEN": "secret"
    }
    assert out["type"] == "local" and out["enabled"]


def test_cross_client_auth_and_tool_policy_not_silently_dropped():
    target = Agent("cursor", "Cursor", "", Path("unused"))
    for field in [
        "bearer_token_env_var",
        "oauth_resource",
        "env_vars",
        "disabled_tools",
    ]:
        with pytest.raises(ValueError, match="不能自动转换"):
            translate(
                {"url": "https://example.test/mcp", field: "important"}, "codex", target
            )


def test_http_header_and_sse_conversion():
    target = Agent("codex", "Codex", "", Path("unused"), "codex", "mcp_servers")
    out, _ = translate(
        {"url": "https://example.test/mcp", "headers": {"Authorization": "secret"}},
        "json",
        target,
    )
    assert out["http_headers"] == {"Authorization": "secret"}
    with pytest.raises(ValueError, match="SSE"):
        translate({"type": "sse", "url": "https://example.test/sse"}, "json", target)


def test_plugin_configs_are_inventoried_but_variables_are_not_migrated(app):
    c, engine = app
    write(
        c.home / ".codex/plugins/cache/vendor/tool/1/.mcp.json",
        {"mcpServers": {"plugin-tool": {"command": "${PLUGIN_ROOT}/server"}}},
    )
    c.scan()
    m = next(x for x in c.mcps.values() if x["name"] == "plugin-tool")
    assert "未核验" in m["scope"]
    plan = engine.preview(choose(c, skills=[], mcps=[m["id"]]))
    assert any("插件变量" in s for s in plan["errors"])


def test_profiles_roundtrip_no_credential_values(app):
    c, engine = app
    profiles = Profiles(c.data)
    p = profiles.save({**choose(c), "name": "科研组合"})
    assert profiles.list()[0]["name"] == "科研组合"
    assert "VERY_SECRET" not in json.dumps(profiles.list())
    profiles.delete(p["id"])
    assert profiles.list() == []


def test_invalid_config_is_reported_and_never_replaced(app):
    c, engine = app
    p = c.home / ".codex/config.toml"
    write(p, "bad = [")
    s = c.scan()
    assert next(a for a in s["agents"] if a["id"] == "codex")["error"]
    plan = engine.preview(choose(c))
    assert plan["errors"]
    assert p.read_text() == "bad = ["


@pytest.fixture
def web(app):
    c, _ = app
    server = WorkbenchServer(("127.0.0.1", 0), c, "test-session-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with httpx.Client(
        base_url=f"http://127.0.0.1:{server.server_port}", timeout=10, trust_env=False
    ) as client:
        yield client, server
    server.shutdown()
    server.server_close()
    thread.join(3)


def auth(client):
    return client.post(
        "/api/auth", json={"token": "test-session-token"}, headers={"X-Workbench": "1"}
    )


def test_http_auth_host_origin_and_csrf_boundaries(web):
    client, server = web
    assert client.get("/api/catalog").status_code == 401
    assert client.get("/api/health").status_code == 200
    assert (
        client.get("/api/health", headers={"Host": "attacker.test"}).status_code == 403
    )
    assert client.post("/api/auth", json={"token": server.token}).status_code == 403
    assert (
        client.post(
            "/api/auth",
            json={"token": server.token},
            headers={"X-Workbench": "1", "Origin": "https://attacker.test"},
        ).status_code
        == 403
    )
    assert auth(client).status_code == 200
    response = client.get("/api/catalog")
    assert response.status_code == 200 and "VERY_SECRET_TEST_VALUE" not in response.text
    assert client.post("/api/scan", json={}).status_code == 403
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_api_full_preview_apply_restore_and_static_path_boundary(web):
    client, server = web
    auth(client)
    headers = {"X-Workbench": "1"}
    plan = client.post(
        "/api/preview", json=choose(server.catalog), headers=headers
    ).json()
    assert not plan["errors"]
    result = client.post("/api/apply", json={"id": plan["id"]}, headers=headers)
    assert result.status_code == 200
    tx = result.json()["transaction"]
    assert (
        client.post("/api/restore", json={"id": tx}, headers=headers).status_code == 200
    )
    assert client.get("/launcher.py").status_code == 404
    assert client.get("/style.css").status_code == 200
    assert client.get("/app.js").status_code == 200


def test_api_add_custom_agent_and_library_mcp(web):
    client, server = web
    auth(client)
    headers = {"X-Workbench": "1"}
    newdir = server.catalog.home / "myagent"
    newdir.mkdir()
    result = client.post(
        "/api/agents/add",
        json={
            "name": "My Agent",
            "config": str(newdir / "mcp.json"),
            "skills": str(newdir / "skills"),
        },
        headers=headers,
    )
    assert result.status_code == 200 and any(
        x["name"] == "My Agent" for x in result.json()["agents"]
    )
    result = client.post(
        "/api/library/mcp",
        json={
            "name": "my-tool",
            "config": {"command": "python", "env": {"SECRET": "KEEP_PRIVATE"}},
        },
        headers=headers,
    )
    assert result.status_code == 200 and "KEEP_PRIVATE" not in result.text


def test_overwrite_restore_preserves_hidden_dependencies_and_empty_folders(app):
    c, engine = app
    target = c.home / ".agents/skills/lab"
    write(target / "SKILL.md", "old definition")
    write(target / ".git/refs/heads/main", "history-pointer")
    write(target / "node_modules/my-dependency/index.js", "original dependency")
    write(target / ".venv/notes.txt", "environment notes")
    (target / "empty-folder").mkdir()
    before = digest(target)
    plan = engine.preview(choose(c, mcps=[], overwrite=True))
    assert not plan["errors"]
    tx = engine.apply(plan["id"])["transaction"]
    engine.restore(tx)
    assert digest(target) == before
    assert (target / ".git/refs/heads/main").read_text() == "history-pointer"
    assert (
        target / "node_modules/my-dependency/index.js"
    ).read_text() == "original dependency"
    assert (target / "empty-folder").is_dir()


def test_imported_skill_includes_dependency_files(app):
    from agent_workbench.service import WorkbenchService

    c, _ = app
    source = c.home / ".claude/skills/lab"
    write(source / "node_modules/helper/index.js", "a dependency")
    WorkbenchService(c).import_skill({"path": str(source)})
    assert digest(c.data / "library/skills/lab") == digest(source)


def test_partial_skill_import_cleans_stage_and_can_be_retried(app, monkeypatch):
    import agent_workbench.service as service_module

    c, _ = app
    service = service_module.WorkbenchService(c)
    source = c.home / ".claude/skills/lab"

    def fail(src, dst):
        write(dst / "partial", "partial contents")
        raise OSError("fixture")

    with monkeypatch.context() as local:
        local.setattr(service_module, "copy_resource", fail)
        with pytest.raises(OSError):
            service.import_skill({"path": str(source)})
    assert not (c.data / "library/skills/lab").exists()
    assert not list((c.data / "library").glob(".import-*"))
    service.import_skill({"path": str(source)})
    assert (c.data / "library/skills/lab/SKILL.md").is_file()


def test_skill_only_install_does_not_parse_unrelated_broken_mcp(app):
    c, engine = app
    write(c.home / ".codex/config.toml", "broken = [")
    c.scan()
    plan = engine.preview(choose(c, mcps=[]))
    assert not plan["errors"] and plan["operation_count"] == 1
    engine.apply(plan["id"])
    assert (c.home / ".codex/config.toml").read_text() == "broken = ["


def test_skill_definition_changed_since_catalog_is_not_silently_used(app):
    c, engine = app
    write(c.home / ".claude/skills/lab/SKILL.md", "changed definition")
    plan = engine.preview(choose(c, mcps=[]))
    assert any("定义已变化" in error for error in plan["errors"])


def test_hidden_file_edits_block_restore(app):
    c, engine = app
    tx = engine.apply(engine.preview(choose(c, mcps=[]))["id"])["transaction"]
    target = c.home / ".agents/skills/lab/node_modules/my-work.txt"
    write(target, "new user work")
    with pytest.raises(ValueError, match="又被修改"):
        engine.restore(tx)
    assert target.read_text() == "new user work"


def test_backup_race_is_detected_before_any_mutation(app, monkeypatch):
    c, engine = app
    config = c.home / ".codex/config.toml"
    plan = engine.preview(choose(c))
    original = engine_module.copy_resource

    def racing_copy(source, target):
        original(source, target)
        if source == config and target.name.endswith(".before"):
            target.write_bytes(b"concurrent bytes differ")

    monkeypatch.setattr(engine_module, "copy_resource", racing_copy)
    before = config.read_bytes()
    with pytest.raises(ValueError, match="已回滚"):
        engine.apply(plan["id"])
    assert config.read_bytes() == before
    assert not (c.home / ".agents/skills/lab").exists()


def test_target_edit_during_final_staging_is_not_overwritten(app, monkeypatch):
    import agent_workbench.resources as resources

    c, engine = app
    config = c.home / ".codex/config.toml"
    plan = engine.preview(choose(c, skills=[]))
    original = resources.copy_resource
    edited = config.read_text() + "\n# external work\n"

    def racing_copy(source, target):
        original(source, target)
        if target.name.startswith(".config.toml.awb-"):
            config.write_text(edited, encoding="utf-8")

    monkeypatch.setattr(resources, "copy_resource", racing_copy)
    with pytest.raises(ValueError, match="需要从备份恢复"):
        engine.apply(plan["id"])
    assert config.read_text(encoding="utf-8") == edited
    assert engine.history()[0]["status"] == "recovery_needed"


def test_same_name_mcp_policy_cannot_be_lost_without_overwrite(app):
    c, engine = app
    raw = {
        "command": "python",
        "args": ["secret-argument"],
        "env": {"API_KEY": "VERY_SECRET_TEST_VALUE"},
        "type": "stdio",
        "autoApprove": ["all"],
    }
    write(c.home / ".cursor/mcp.json", {"mcpServers": {"research": raw}})
    c.scan()
    plan = engine.preview(choose(c, skills=[], agents=["cursor"]))
    assert any("同名 MCP" in error for error in plan["errors"])


def test_invalid_profile_cannot_break_other_profiles(app):
    c, _ = app
    store = Profiles(c.data)
    good = store.save({**choose(c), "name": "valid"})
    write(c.data / "profiles" / ("b" * 32 + ".json"), "{broken")
    items = store.list()
    assert any(x.get("error") for x in items)
    assert any(x["id"] == good["id"] for x in items)


def test_profiles_persist_only_selected_agents_config_targets(app):
    c, _ = app
    profiles = Profiles(c.data)
    targets = {"codex": str(c.home / ".codex/config.toml")}
    saved = profiles.save({**choose(c), "name": "local choice", "config_targets": targets})
    assert saved["config_targets"] == profiles.list()[0]["config_targets"] == targets
    with pytest.raises(ValueError, match="不属于所选"):
        profiles.save({**choose(c), "name": "invalid", "config_targets": {"other": "outside"}})


def test_corrupt_mcp_library_does_not_hide_other_resources(app):
    c, _ = app
    write(c.data / "library/mcps.json", "{broken")
    result = c.scan()
    assert result["counts"]["skills"] and result["counts"]["mcps"]
    assert any("资源库无法读取" in issue for issue in result["issues"])


def test_failed_scan_keeps_previous_catalog_coherent(app, monkeypatch):
    c, _ = app
    before = c.get()

    def fail():
        c.skills = {}
        raise OSError("fixture")

    monkeypatch.setattr(c, "_scan", fail)
    with pytest.raises(OSError):
        c.scan()
    assert c.get() is before and len(c.skills) == before["counts"]["skills"]


@pytest.mark.parametrize(
    "body",
    [
        {"name": "bad", "skills": "abcd"},
        {"name": "bad", "mcps": [None]},
        {"name": "bad", "agents": [{"id": "codex"}]},
        {"name": "bad", "mode": "bad"},
        {"name": "bad", "overwrite": "false"},
        {"name": "bad", "config_targets": []},
        {"name": "bad", "agents": ["codex"], "config_targets": {"codex": 12}},
        {"name": "bad", "agents": ["codex"], "config_targets": {"other": "outside"}},
    ],
)
def test_malformed_profiles_rejected_at_api_boundary(web, body):
    client, server = web
    auth(client)
    response = client.post(
        "/api/profiles/save", json=body, headers={"X-Workbench": "1"}
    )
    assert response.status_code == 400
    assert not list((server.catalog.data / "profiles").glob("*.json"))


def test_invalid_mcp_definition_is_not_saved(web):
    client, server = web
    auth(client)
    response = client.post(
        "/api/library/mcp",
        json={"name": "bad", "config": {"command": 123}},
        headers={"X-Workbench": "1"},
    )
    assert response.status_code == 400
    assert not (server.catalog.data / "library/mcps.json").exists()


def test_static_modules_have_correct_type_and_are_isolated(web):
    client, _ = web
    for module in ("state", "views", "dialogs", "ui", "api"):
        response = client.get(f"/{module}.js")
        assert (
            response.status_code == 200
            and "javascript" in response.headers["content-type"]
        )
    for path in ("/../server.py", "/%2e%2e/server.py", "/service.py"):
        assert client.get(path).status_code == 404


def test_launcher_recognizes_service_while_catalog_is_busy(web):
    from launcher import find_running
    from agent_workbench.storage import write_json

    _, server = web
    write_json(
        server.catalog.data / "runtime.json",
        {
            "port": server.server_port,
            "token": server.token,
            "pid": 1,
        },
    )
    # A second request must not need the catalog lock to authenticate a launcher.
    with server.catalog.lock:
        running = find_running(server.catalog.data)
    assert running and running["port"] == server.server_port
    from agent_workbench import __version__

    assert running["version"] == __version__


def test_unsupported_backup_format_is_reported_without_modifying_targets(app):
    c, engine = app
    transaction = "a" * 32
    path = c.data / "backups" / transaction / "manifest.json"
    write(
        path, {"id": transaction, "status": "applied", "entries": [], "created_at": ""}
    )
    assert engine.history()[0]["restorable"] is False
    before = (c.home / ".codex/config.toml").read_bytes()
    with pytest.raises(ValueError, match="原版本恢复"):
        engine.restore(transaction)
    assert (c.home / ".codex/config.toml").read_bytes() == before


def test_shutdown_finishes_an_accepted_write_before_closing(web):
    client, server = web
    auth(client)
    started, release, closed = threading.Event(), threading.Event(), threading.Event()
    marker = server.catalog.data / "completed.txt"
    outcome = []

    def slow_write(_):
        started.set()
        assert release.wait(5)
        marker.write_text("complete", encoding="utf-8")
        return {"ok": True}

    server.service.operations["/api/slow-write"] = slow_write
    url = f"http://127.0.0.1:{server.server_port}"
    headers = {
        "X-Workbench": "1",
        "Cookie": f"awb_session_{server.server_port}=" + server.token,
    }

    def request():
        with httpx.Client(trust_env=False, timeout=10) as worker:
            outcome.append(
                worker.post(
                    url + "/api/slow-write", json={}, headers=headers
                ).status_code
            )

    worker = threading.Thread(target=request)
    worker.start()
    try:
        assert started.wait(3)
        assert (
            client.post(
                "/api/shutdown", json={}, headers={"X-Workbench": "1"}
            ).status_code
            == 200
        )
        closer = threading.Thread(target=lambda: (server.server_close(), closed.set()))
        closer.start()
        assert not closed.wait(0.1)
    finally:
        release.set()
    worker.join(5)
    closer.join(5)
    assert closed.is_set() and outcome == [200]
    assert marker.read_text() == "complete"


def test_interrupted_rename_can_be_restored_repeatedly_until_the_user_edits(app):
    c, engine = app
    target = c.home / ".codex/config.toml"
    original = target.read_bytes()
    identity = path_identity(target)
    before = digest(target)
    tx = "b" * 32
    aside = target.with_name(f".config.toml.awb-old-{tx}-0")
    os.replace(target, aside)
    folder = c.data / "backups" / tx
    folder.mkdir(parents=True)
    (folder / "0.before").write_bytes(original)
    stage = folder / "0.after"
    stage.write_bytes(b'model = "changed"\n')
    from agent_workbench.storage import write_json

    write_json(folder / "manifest.json", {
        "format_version": 2, "id": tx, "name": "interrupted",
        "created_at": "2026-10-21T00:00:00+08:00", "status": "applying",
        "entries": [{
            "target": str(target), "kind": "config", "before": before,
            "after": digest(stage), "index": 0, "applied": True,
            "phase": "renaming-aside", "stage": str(stage),
            "previous_path": str(aside), "identity": identity,
        }],
    })
    record = next(item for item in engine.history() if item["id"] == tx)
    assert record["entries"][0]["previous_path"] == str(aside)
    assert engine.restore(tx)["ok"] is True
    assert target.read_bytes() == original and not aside.exists()
    assert "已经是恢复后的内容" in engine.restore(tx)["message"]
    target.write_bytes(b'model = "user edit"\n')
    with pytest.raises(ValueError, match="又被修改"):
        engine.restore(tx)
    assert target.read_bytes() == b'model = "user edit"\n'


class SimulatedCrash(BaseException):
    """Bypass normal exception rollback to model process interruption."""


def interrupt_rename(monkeypatch, target, *, commit=False):
    real_replace = os.replace
    def replace(source, destination):
        real_replace(source, destination)
        hit = Path(destination) == target if commit else Path(source) == target
        if hit:
            raise SimulatedCrash()
    monkeypatch.setattr(os, "replace", replace)


@pytest.mark.parametrize("kind", ["config", "skill"])
@pytest.mark.parametrize("commit", [False, True])
def test_real_apply_rename_interruption_recovers(app, monkeypatch, kind, commit):
    c, engine = app
    target = c.home / (".codex/config.toml" if kind == "config" else ".agents/skills/lab")
    if kind == "skill":
        write(target / "SKILL.md", "original skill")
    original = digest(target)
    plan = engine.preview(choose(c, overwrite=True,
                                 **({"skills": []} if kind == "config" else {"mcps": []})))
    assert not plan["errors"]
    with monkeypatch.context() as fault:
        interrupt_rename(fault, target, commit=commit)
        with pytest.raises(SimulatedCrash):
            engine.apply(plan["id"])
    tx = engine.history()[0]["id"]
    assert Engine(c).restore(tx)["ok"]
    assert digest(target) == original
    assert Engine(c).restore(tx)["ok"]


@pytest.mark.parametrize("kind,commit", [("config", False), ("config", True), ("skill", False), ("skill", True), ("install", False)])
def test_restore_rename_interruption_is_journalled_and_retryable(app, monkeypatch, kind, commit):
    c, engine = app
    target = c.home / (".codex/config.toml" if kind == "config" else ".agents/skills/lab")
    if kind == "skill":
        write(target / "SKILL.md", "original skill")
        write(target / "nested/original.txt", "original attachment")
    original = digest(target)
    body = choose(c, overwrite=True, **({"skills": []} if kind == "config" else {"mcps": []}))
    tx = engine.apply(engine.preview(body)["id"])["transaction"]
    journal = c.data / "backups" / tx / "manifest.json"
    with monkeypatch.context() as fault:
        interrupt_rename(fault, target, commit=commit)
        with pytest.raises(SimulatedCrash):
            engine.restore(tx)
    entry = json.loads(journal.read_text(encoding="utf-8"))["entries"][0]
    assert entry["restore_phase"] == "prepared"
    assert Path(entry["restore_path"]).exists()
    # A fresh engine reconstructs recovery from disk, not process-local state.
    assert Engine(c).restore(tx)["ok"]
    assert digest(target) == original
    assert not Path(entry["restore_path"]).exists()
    assert Engine(c).restore(tx)["ok"]
    write(target if kind == "config" else target / "user.txt", "later user content")
    with pytest.raises(ValueError, match="又被修改"):
        Engine(c).restore(tx)


def test_interrupted_restore_keeps_later_edits_and_all_recovery_evidence(app, monkeypatch):
    c, engine = app
    target = c.home / ".codex/config.toml"
    tx = engine.apply(engine.preview(choose(c, skills=[]))["id"])["transaction"]
    with monkeypatch.context() as fault:
        interrupt_rename(fault, target)
        with pytest.raises(SimulatedCrash):
            engine.restore(tx)
    journal = c.data / "backups" / tx / "manifest.json"
    entry = json.loads(journal.read_text(encoding="utf-8"))["entries"][0]
    write(target, "user edited while the restore was interrupted")
    snapshots = {path: digest(path) for path in (target, Path(entry["restore_path"]), journal, journal.parent / "0.before")}
    with pytest.raises(ValueError, match="又被修改"):
        Engine(c).restore(tx)
    assert {path: digest(path) for path in snapshots} == snapshots


def test_restore_preflight_does_not_materialize_earlier_entry_when_later_backup_is_bad(app):
    c, engine = app
    write(c.home / ".agents/skills/lab/SKILL.md", "original skill")
    tx = engine.apply(engine.preview(choose(c, overwrite=True))["id"])["transaction"]
    journal = c.data / "backups" / tx / "manifest.json"
    manifest = json.loads(journal.read_text(encoding="utf-8"))
    entry = manifest["entries"][0]
    target, aside = Path(entry["target"]), Path(entry["previous_path"])
    aside.write_bytes((journal.parent / "0.before").read_bytes())
    target.unlink()
    entry["phase"] = "renaming-aside"
    write(journal, manifest)
    write(journal.parent / "1.before/SKILL.md", "corrupt backup")
    snapshots = {path: digest(path) for path in (journal, aside, journal.parent / "1.before")}
    with pytest.raises(ValueError, match="备份校验失败"):
        engine.restore(tx)
    assert not target.exists()
    assert {path: digest(path) for path in snapshots} == snapshots


def test_restore_rejects_sidecar_outside_transaction_sibling(app, monkeypatch):
    c, engine = app
    target = c.home / ".codex/config.toml"
    with monkeypatch.context() as fault:
        interrupt_rename(fault, target)
        with pytest.raises(SimulatedCrash):
            engine.apply(engine.preview(choose(c, skills=[]))["id"])
    tx = engine.history()[0]["id"]
    journal = c.data / "backups" / tx / "manifest.json"
    manifest = json.loads(journal.read_text(encoding="utf-8"))
    protected = c.home / "outside/untouched.toml"
    write(protected, "protected")
    manifest["entries"][0]["previous_path"] = str(protected)
    write(journal, manifest)
    snapshots = {path: digest(path) for path in (journal, protected, target)}
    with pytest.raises(ValueError, match="暂存路径不匹配"):
        engine.restore(tx)
    assert {path: digest(path) for path in snapshots} == snapshots


@pytest.mark.parametrize("already_restored", [False, True])
def test_redirected_parent_is_rejected_without_moving_sidecar(app, monkeypatch, already_restored):
    import shutil
    c, engine = app
    target = c.home / (".agents/skills/lab" if already_restored else ".codex/config.toml")
    if already_restored:
        tx = engine.apply(engine.preview(choose(c, mcps=[]))["id"])["transaction"]
        engine.restore(tx)
    else:
        with monkeypatch.context() as fault:
            interrupt_rename(fault, target)
            with pytest.raises(SimulatedCrash):
                engine.apply(engine.preview(choose(c, skills=[]))["id"])
        tx = engine.history()[0]["id"]
    parent = target.parent
    original = parent.with_name(parent.name + "-original")
    redirected = c.home / "redirected"
    assert c.home.resolve() in parent.resolve().parents
    assert c.home.resolve() in original.resolve().parents
    os.replace(parent, original)
    redirected.mkdir()
    if not already_restored:
        aside_name = f".{target.name}.awb-old-{tx}-0"
        shutil.copy2(original / aside_name, redirected / aside_name)
    if os.name == "nt":
        process = subprocess.Popen(["cmd", "/d", "/c", "mklink", "/J", str(parent), str(redirected)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output, error = process.communicate(timeout=10)
        assert process.returncode == 0, (output, error)
    else:
        parent.symlink_to(redirected, target_is_directory=True)
    journal = c.data / "backups" / tx / "manifest.json"
    snapshots = {path: digest(path) for path in (original, redirected, journal)}
    try:
        with pytest.raises(ValueError, match="位置已变化"):
            Engine(c).restore(tx)
        assert {path: digest(path) for path in snapshots} == snapshots
        assert not (redirected / target.name).exists()
    finally:
        # Remove only this fixture link; never recurse through the junction.
        assert parent.resolve() == redirected.resolve()
        parent.rmdir() if os.name == "nt" else parent.unlink()
