import copy
import json
import threading

import httpx
import pytest

from agent_workbench.catalog import Catalog
from agent_workbench.server import WorkbenchServer
from agent_workbench.teams import Teams, blueprint


def team():
    return {
        "format": "agent-workbench-team",
        "version": 1,
        "name": "研发组",
        "goal": "交付可验证的功能",
        "layout": "horizontal",
        "nodes": [
            {
                "id": "root",
                "parentId": None,
                "role": "tutor",
                "name": "负责人",
                "prompt": "审核产物",
                "x": 0,
                "y": 0,
            },
            {
                "id": "worker",
                "parentId": "root",
                "role": "employee",
                "name": "实现",
                "x": 310,
                "y": 0,
                "skills": [{"id": "missing-source", "name": "资料"}],
            },
        ],
    }


def test_save_roundtrip_keeps_missing_references_and_filters_config_data(tmp_path):
    document = team()
    document["token"] = "must-not-persist"
    document["nodes"][0].update(
        config={"env": {"SECRET": "must-not-persist"}},
        status="completed",
    )
    document["nodes"][1]["skills"][0]["source"] = "private-path"
    store = Teams(tmp_path)
    saved = store.save(document)
    assert store.list() == [saved]
    assert saved["nodes"][1]["skills"] == [{"id": "missing-source", "name": "资料"}]
    content = (store.root / (saved["id"] + ".json")).read_text(encoding="utf-8")
    assert "must-not-persist" not in content
    assert "private-path" not in content
    assert "completed" not in content


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda t: t["nodes"][0].update(parentId="worker"), "循环"),
        (lambda t: t["nodes"][1].update(parentId="absent"), "不存在"),
        (lambda t: t["nodes"][1].update(id="root"), "重复"),
        (lambda t: t["nodes"][1].update(id="../outside"), "编号"),
        (lambda t: t["nodes"][1].update(role="system"), "类型"),
        (lambda t: t["nodes"][1].update(x=float("nan")), "坐标"),
        (lambda t: t["nodes"][1].update(y=True), "坐标"),
        (lambda t: t["nodes"][1].update(collapsed="false"), "折叠"),
        (lambda t: t.update(nodes=[]), "1–100"),
        (lambda t: t.update(nodes=t["nodes"] * 51), "1–100"),
        (lambda t: t.update(name=""), "不能为空"),
        (lambda t: t.update(layout="unknown"), "布局"),
        (lambda t: t["nodes"][1]["skills"].append(t["nodes"][1]["skills"][0]), "重复"),
    ],
)
def test_invalid_graph_is_rejected_without_writing(tmp_path, change, message):
    document = team()
    change(document)
    with pytest.raises(ValueError, match=message):
        Teams(tmp_path).save(document)
    assert not (tmp_path / "teams").exists()


def test_stale_update_and_delete_preserve_newer_document(tmp_path):
    store = Teams(tmp_path)
    original = store.save(team())
    newer = copy.deepcopy(original)
    newer["goal"] = "新的目标"
    saved = store.save(newer)
    assert saved["revision"] != original["revision"]
    with pytest.raises(ValueError, match="另一窗口"):
        store.save(original)
    with pytest.raises(ValueError, match="另一窗口"):
        store.delete(original)
    assert store.list()[0]["goal"] == "新的目标"
    store.delete(saved)
    with pytest.raises(ValueError, match="已被删除"):
        store.save(saved)


def test_invalid_persisted_team_is_reported_without_hiding_valid_team(tmp_path):
    store = Teams(tmp_path)
    saved = store.save(team())
    bad = store.root / ("f" * 32 + ".json")
    bad.write_text("{broken", encoding="utf-8")
    items = store.list()
    assert items[0]["id"] == saved["id"]
    assert items[1]["error"]
    assert bad.read_text() == "{broken"


def test_team_api_uses_existing_auth_and_does_not_touch_agent_configuration(tmp_path):
    home, roaming, data = tmp_path / "home", tmp_path / "roaming", tmp_path / "data"
    config = home / ".codex/config.toml"
    config.parent.mkdir(parents=True)
    original = b'model = "keep-me"\n'
    config.write_bytes(original)
    server = WorkbenchServer(
        ("127.0.0.1", 0), Catalog(home, roaming, data), "team-test"
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with httpx.Client(base_url=base, trust_env=False) as client:
            assert client.get("/api/teams").status_code == 401
            headers = {"X-Workbench": "1", "Origin": base}
            assert client.post(
                "/api/auth", json={"token": "team-test"}, headers=headers
            ).is_success
            saved_response = client.post(
                "/api/teams/save", json=team(), headers=headers
            )
            assert saved_response.is_success, saved_response.text
            saved = saved_response.json()
            assert client.get("/api/teams").json()[0]["id"] == saved["id"]
            assert (
                client.post(
                    "/api/teams/delete",
                    json=saved,
                    headers={"X-Workbench": "1", "Origin": "https://other.example"},
                ).status_code
                == 403
            )
            invalid = team()
            invalid["nodes"][0]["parentId"] = "worker"
            assert (
                client.post(
                    "/api/teams/save", json=invalid, headers=headers
                ).status_code
                == 400
            )
            assert len(client.get("/api/teams").json()) == 1
            assert client.post(
                "/api/teams/delete", json=saved, headers=headers
            ).is_success
            assert client.get("/api/teams").json() == []
        assert config.read_bytes() == original
        assert not (data / "backups").exists()
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def test_blueprint_normalization_does_not_mutate_input():
    raw = team()
    before = json.dumps(raw)
    assert blueprint(raw)["nodes"][0]["mcps"] == []
    assert json.dumps(raw) == before
