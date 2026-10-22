import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_workbench import adapters, discovery
from agent_workbench.catalog import Catalog
from agent_workbench.engine import Engine
from agent_workbench.service import WorkbenchService


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        value if isinstance(value, str) else json.dumps(value), encoding="utf-8"
    )
    return path


def fixture_catalog(tmp_path):
    home, roaming, data = (tmp_path / name for name in ("home", "roaming", "data"))
    write(home / ".codex/config.toml", 'model = "preserve"\n')
    write(
        home / ".claude.json",
        {
            "mcpServers": {
                "test": {
                    "command": "python",
                    "args": ["PRIVATE_ARGUMENT"],
                    "env": {"KEY": "PRIVATE_TOKEN"},
                }
            }
        },
    )
    write(
        home / ".claude/skills/lab/SKILL.md",
        "---\nname: lab\ndescription: before\n---\nPRIVATE_SKILL_BODY",
    )
    return Catalog(home, roaming, data)


def restarted(catalog):
    return Catalog(catalog.home, catalog.roaming, catalog.data)


def pause_scan(catalog, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = catalog._scan
    calls = []

    def paused():
        calls.append(1)
        entered.set()
        if not release.wait(10):
            raise TimeoutError("test scan was not released")
        return original()

    monkeypatch.setattr(catalog, "_scan", paused)
    return entered, release, calls


def test_json_fast_path_and_json5_extension_preserve_values(monkeypatch, tmp_path):
    config = write(
        tmp_path / "config.json", '{"unicode":"中文","nested":{"a":[1,true,null]}}'
    )
    expected = adapters.json5.loads(config.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        adapters.json5,
        "loads",
        lambda _: pytest.fail("Standard JSON should not invoke the JSON5 parser"),
    )
    assert adapters.read_document(config, "json")[0] == expected


def test_json5_comments_and_trailing_commas_remain_supported(tmp_path):
    path = write(
        tmp_path / "config.jsonc", "// comment\n{unquoted: 'value', number: 0x10,}"
    )
    assert adapters.read_document(path, "json")[0] == {
        "unquoted": "value",
        "number": 16,
    }
    write(path, "[1,2]")
    with pytest.raises(ValueError, match="根节点"):
        adapters.read_document(path, "json")


def test_deep_settings_preserve_original_and_keep_catalog_available(tmp_path):
    data = tmp_path / "data"
    original = '{"roots":[],"agents":[],"extra":' + "[" * 60000 + "0" + "]" * 60000 + "}"
    settings = write(data / "settings.json", original)
    catalog = Catalog(tmp_path / "home", tmp_path / "roaming", data, system_scan=False)
    try:
        assert "settings.json" in catalog.settings_error
        assert catalog.scan()["counts"]["skills"] == 0
        assert settings.read_text(encoding="utf-8") == original
    finally:
        catalog.close()


def test_bad_skill_does_not_hide_healthy_items_and_recovers_after_edit(tmp_path):
    catalog = fixture_catalog(tmp_path)
    bad = write(catalog.home / ".claude/skills/bad/SKILL.md",
                "---\nname: bad\npayload: " + "[" * 30000 + "]" * 30000 + "\n---\nbody")
    original = bad.read_bytes()
    try:
        for _ in range(2):
            view = catalog.scan()
            assert {item["name"] for item in view["skills"]} == {"lab"}
            assert any(str(bad) in issue and "RecursionError" in issue for issue in view["issues"])
            assert not catalog.scan_status()["error"]
        assert bad.read_bytes() == original
        write(bad, "---\nname: repaired\ndescription: readable\n---\nbody")
        repaired = catalog.scan()
        assert {item["name"] for item in repaired["skills"]} == {"lab", "repaired"}
        assert not any(str(bad) in issue for issue in repaired["issues"])
    finally:
        catalog.close()


def test_unreadable_role_directory_does_not_abort_skill_scan(tmp_path, monkeypatch):
    catalog = fixture_catalog(tmp_path)
    denied = catalog.home / ".claude/agents"
    denied.mkdir()
    original_iterdir = Path.iterdir

    def iterdir(path):
        if path == denied:
            raise PermissionError("fixture denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    try:
        view = catalog.scan()
        assert any(item["name"] == "lab" for item in view["skills"])
        assert any(str(denied) in issue for issue in view["issues"])
        assert not catalog.scan_status()["error"]
    finally:
        catalog.close()


def test_startup_cache_is_public_only_and_not_authoritative(tmp_path):
    c = fixture_catalog(tmp_path)
    old = c.scan()
    disk = (c.data / "cache/catalog.json").read_text(encoding="utf-8")
    for secret in ("PRIVATE_TOKEN", "PRIVATE_ARGUMENT", "PRIVATE_SKILL_BODY", '"_raw"'):
        assert secret not in disk
    write(c.home / ".claude.json", {"mcpServers": {"test": {"command": "changed"}}})
    fresh = restarted(c)
    source = old["mcps"][0]
    plan = Engine(fresh).preview(
        {"agents": ["codex"], "mcps": [source["id"]], "skills": []}
    )
    assert not plan["errors"]
    assert fresh.mcps[source["id"]]["_raw"]["command"] == "changed"
    assert fresh.snapshot["counts"] == old["counts"]


def test_catalog_and_shell_lists_remain_available_during_refresh(tmp_path, monkeypatch):
    old = fixture_catalog(tmp_path)
    expected = old.scan()
    c = restarted(old)
    service = WorkbenchService(c)
    entered, release, calls = pause_scan(c, monkeypatch)
    try:
        view = service.get("/api/catalog")
        assert view["counts"] == expected["counts"]
        assert view["scan"]["cached"] and view["scan"]["running"]
        assert entered.wait(2)
        with ThreadPoolExecutor(max_workers=5) as pool:
            requests = [
                pool.submit(service.get, path)
                for path in (
                    "/api/catalog",
                    "/api/scan-status",
                    "/api/profiles",
                    "/api/history",
                    "/api/teams",
                )
            ]
            try:
                results = [future.result(timeout=1) for future in requests]
                assert results[0]["counts"] == expected["counts"]
                assert results[1]["running"]
                assert results[2:] == [[], [], []]
                for _ in range(5):
                    service.post("/api/scan", {})
                assert len(calls) == 1
            finally:
                release.set()
        c.close()
        assert c.scan_status()["ready"] and not c.scan_status()["running"]
        assert c.view()["scan"]["cached"] is False
    finally:
        release.set()
        c.close()
        service.workflows.close()
        service.runs.close()


@pytest.mark.parametrize("cache_problem", ["corrupt", "checksum", "settings"])
def test_unusable_cache_shows_empty_shell_then_recovers(
    tmp_path, monkeypatch, cache_problem
):
    old = fixture_catalog(tmp_path)
    expected = old.scan()
    cache = old.data / "cache/catalog.json"
    if cache_problem == "corrupt":
        write(cache, "{")
    elif cache_problem == "checksum":
        value = json.loads(cache.read_text(encoding="utf-8"))
        value["snapshot"]["counts"]["skills"] = 500
        write(cache, value)
    else:
        write(old.settings_path, {"roots": [str(tmp_path / "extra")], "agents": []})
    c = restarted(old)
    entered, release, _ = pause_scan(c, monkeypatch)
    try:
        view = c.view()
        assert view["scanned_at"] is None
        assert view["counts"]["skills"] == 0
        assert view["scan"]["running"] and not view["scan"]["cached"]
        assert entered.wait(2)
    finally:
        release.set()
        c.close()
    assert c.view()["counts"] == expected["counts"]


def test_failed_background_scan_keeps_cache_and_can_retry(tmp_path, monkeypatch):
    old = fixture_catalog(tmp_path)
    expected = old.scan()
    c = restarted(old)
    original = c._scan

    def fail():
        raise OSError("PRIVATE_ERROR_DETAIL")

    monkeypatch.setattr(c, "_scan", fail)
    c.view()
    c._refresh_thread.join(2)
    result = c.view()
    assert result["counts"] == expected["counts"]
    assert "扫描未完成" in result["scan"]["error"]
    assert "PRIVATE_ERROR_DETAIL" not in json.dumps(result)
    monkeypatch.setattr(c, "_scan", original)
    c.start_scan()
    c._refresh_thread.join(2)
    assert c.view()["scan"]["ready"]
    assert c.view()["scan"]["error"] is None
    c.close()


def test_same_size_same_mtime_edits_and_deletions_are_seen(tmp_path):
    c = fixture_catalog(tmp_path)
    c.scan()
    skill = c.home / ".claude/skills/lab/SKILL.md"
    before = skill.stat()
    skill.write_text(
        skill.read_text(encoding="utf-8").replace("before", "after!"), encoding="utf-8"
    )
    os.utime(skill, ns=(before.st_atime_ns, before.st_mtime_ns))
    config = c.home / ".claude.json"
    before = config.stat()
    config.write_text(
        config.read_text(encoding="utf-8").replace("python", "nodejs"), encoding="utf-8"
    )
    os.utime(config, ns=(before.st_atime_ns, before.st_mtime_ns))
    updated = c.scan()
    assert updated["skills"][0]["description"] == "after!"
    assert updated["mcps"][0]["summary"]["launcher"] == "nodejs"
    skill.unlink()
    config.unlink()
    removed = c.scan()
    assert removed["counts"]["skills"] == removed["counts"]["mcps"] == 0
    assert not any(key[0] in (str(skill), str(config)) for key in c._files.entries)


def test_shared_skill_sources_and_windows_case_are_preserved(tmp_path):
    c = fixture_catalog(tmp_path)
    root = c.home / ".claude/skills"
    c.settings["roots"] = [str(root)]
    if os.name == "nt":
        (root / "lab/SKILL.md").rename(root / "lab/skill.md")
    snapshot = c.scan()
    assert snapshot["counts"]["skills"] == 1
    assert {s["agent"] for s in snapshot["skills"][0]["sources"]} == {
        "claude",
        "custom",
    }


def test_process_discovery_tolerates_protected_and_disappearing_processes(monkeypatch):
    class Process:
        def __init__(self, name, value):
            self.name = name
            self.value = value

        def exe(self):
            if isinstance(self.value, Exception):
                raise self.value
            return self.value

    processes = [
        Process("Cursor.exe", "G:/Cursor/Cursor.exe"),
        Process("cursor.exe", discovery.psutil.AccessDenied()),
        Process("ZCode.exe", discovery.psutil.NoSuchProcess(999999)),
        Process("", ""),
        Process("other.exe", AssertionError("Unrelated executable must not be read")),
    ]
    monkeypatch.setattr(
        discovery,
        "_process_candidates",
        lambda: [(i, p.name) for i, p in enumerate(processes)],
    )
    monkeypatch.setattr(discovery.psutil, "Process", lambda pid: processes[pid])
    if os.name == "nt":
        assert discovery.process_paths() == [Path("G:/Cursor/Cursor.exe")]
