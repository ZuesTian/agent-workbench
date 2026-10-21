"""Conversation storage, validation and serial reply execution."""

import json
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from agent_workbench.conversations import ConversationManager
from agent_workbench.execution import ExecutionManager
from agent_workbench.runners import RUNNERS, RunnerRegistry


class FakeRuns:
    """Minimal stand-in for ExecutionManager used by the conversation tests."""

    def __init__(self, delay=0.0, status="succeeded", result="回复内容"):
        self.lock = threading.RLock()
        self.owner = None
        self.active = None
        self.worker = None
        self.started = []
        self.delay = delay
        self.status = status
        self.result = result
        self.last = None
        self.validated = []
        self.registry = RunnerRegistry(lambda: [sys.executable], {
            key: (lambda: [sys.executable]) for key in RUNNERS
        })

    def runners(self):
        return self.registry.list() + [{"id": "offline", "name": "Offline CLI", "available": False}]

    def validate(self, body):
        self.validated.append(dict(body))
        self.registry.validate(body["agent"], body["sandbox"], body.get("model", ""))
        return body["request_id"], dict(body), [sys.executable]

    def reserve(self, owner):
        with self.lock:
            if self.owner is not None or self.active is not None:
                raise ValueError("已有任务或协作调度运行中")
            self.owner = owner

    def release(self, owner):
        with self.lock:
            if self.owner == owner:
                self.owner = None

    def start(self, body, owner=None):
        with self.lock:
            self.started.append(dict(body))
            record = {
                "id": body["request_id"],
                "status": "running",
                "result": "",
                "error": "",
                "directory": "C:/fake/run",
            }
            self.last = record
            self.active = record["id"]
            if self.delay:
                time.sleep(self.delay)
            record["status"] = self.status
            record["result"] = self.result(body, len(self.started)) if callable(self.result) else self.result
            return dict(record)

    def get(self, run_id):
        with self.lock:
            record = dict(self.last or {"status": "succeeded", "result": "", "error": "", "directory": ""})
            if record["status"] != "running":
                self.active = None
            return record

    def cancel(self, run_id):
        self.last["status"] = "cancelled"
        self.active = None
        return dict(self.last)


def make_manager(tmp_path, runs=None):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    return ConversationManager(data, runs or FakeRuns())


def create(manager, workspace, members=None, name="联调"):
    workspace.mkdir(exist_ok=True)
    return manager.create(
        {
            "name": name,
            "workspace": str(workspace),
            "members": members or [{"id": "codex"}, {"id": "deepseek-harness"}],
        }
    )


def test_create_requires_existing_workspace(tmp_path):
    manager = make_manager(tmp_path)
    with pytest.raises(ValueError, match="工作区"):
        manager.create(
            {"name": "x", "workspace": str(tmp_path / "missing"), "members": [{"id": "codex"}]}
        )


def test_create_rejects_unavailable_member(tmp_path):
    manager = make_manager(tmp_path)
    with pytest.raises(ValueError, match="调用入口"):
        create(manager, tmp_path, [{"id": "offline"}])


def test_create_and_list_conversation(tmp_path):
    manager = make_manager(tmp_path)
    conv = create(manager, tmp_path)
    assert conv["name"] == "联调"
    assert [m["id"] for m in conv["members"]] == ["codex", "deepseek-harness"]
    rows = manager.list()["items"]
    assert len(rows) == 1
    assert rows[0]["id"] == conv["id"]
    assert rows[0]["message_count"] == 0


def test_message_queues_one_reply_per_member_and_records_results(tmp_path):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    updated = manager.message({"id": conv["id"], "text": "你好"})
    assert [m["role"] for m in updated["messages"][:3]] == ["user", "assistant", "assistant"]
    manager.worker.join(timeout=5)
    final = manager.get(conv["id"])
    replies = [m for m in final["messages"] if m["role"] == "assistant"]
    assert [m["status"] for m in replies] == ["succeeded", "succeeded"]
    assert all(m["text"] == "回复内容" for m in replies)
    assert all(m["run_id"] for m in replies)
    assert final["status"] == "succeeded"
    assert [prompt["agent"] for prompt in runs.started] == ["codex", "deepseek-harness"]
    assert "回复内容" in runs.started[1]["prompt"]
    assert runs.started[1]["prompt"].count("你好") == 1


def test_message_can_target_subset_of_members(tmp_path):
    manager = make_manager(tmp_path)
    conv = create(manager, tmp_path)
    manager.message({"id": conv["id"], "text": "只回答一次", "members": ["deepseek-harness"]})
    manager.worker.join(timeout=5)
    final = manager.get(conv["id"])
    replies = [m for m in final["messages"] if m["role"] == "assistant"]
    assert len(replies) == 1
    assert replies[0]["member"] == "deepseek-harness"


def test_failed_reply_stops_the_turn(tmp_path):
    runs = FakeRuns(status="failed", result="")
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    manager.message({"id": conv["id"], "text": "会失败"})
    manager.worker.join(timeout=5)
    final = manager.get(conv["id"])
    assert final["status"] == "failed"
    replies = [m for m in final["messages"] if m["role"] == "assistant"]
    assert replies[0]["status"] == "failed"
    assert replies[1]["status"] in ("queued", "cancelled", "error")


def test_update_members_and_name(tmp_path):
    manager = make_manager(tmp_path)
    conv = create(manager, tmp_path)
    updated = manager.update(
        {"id": conv["id"], "name": "改名", "members": [{"id": "deepseek-harness"}]}
    )
    assert updated["name"] == "改名"
    assert [m["id"] for m in updated["members"]] == ["deepseek-harness"]


def test_delete_removes_conversation(tmp_path):
    manager = make_manager(tmp_path)
    conv = create(manager, tmp_path)
    manager.delete(conv["id"])
    assert manager.list()["items"] == []
    with pytest.raises(LookupError):
        manager.get(conv["id"])


def test_interrupted_reply_is_marked_on_recovery(tmp_path):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    path = tmp_path / "data" / "conversations" / conv["id"] / "conversation.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["messages"].append(
        {
            "id": "a" * 32,
            "role": "assistant",
            "member": "codex",
            "name": "Codex CLI",
            "text": "",
            "status": "running",
            "created_at": record["created_at"],
        }
    )
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    recovered = ConversationManager(tmp_path / "data", runs)
    message = recovered.get(conv["id"])["messages"][-1]
    assert message["status"] == "interrupted"
    assert "没有结果" in message["text"]
    assert recovered.get(conv["id"])["status"] == "interrupted"
    assert recovered.get(conv["id"])["running"] is False


def test_message_rejected_while_replying(tmp_path):
    runs = FakeRuns(delay=0.6)
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    manager.message({"id": conv["id"], "text": "第一条"})
    with pytest.raises(ValueError, match="正在回复"):
        manager.message({"id": conv["id"], "text": "第二条"})
    manager.worker.join(timeout=5)


def test_run_records_use_conversation_workspace_and_sandbox(tmp_path):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path / "workspace")
    manager.message({"id": conv["id"], "text": "检查目录"})
    manager.worker.join(timeout=5)
    assert runs.started[0]["cwd"] == str((tmp_path / "workspace").resolve())
    assert runs.started[0]["sandbox"] == "read-only"


def test_two_rounds_include_each_completed_reply_without_duplicating_current_user(tmp_path):
    runs = FakeRuns(result=lambda _body, index: f"UNIQUE_REPLY_{index}")
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    for user_text in ("ROUND_ONE", "ROUND_TWO"):
        manager.message({"id": conv["id"], "text": user_text})
        manager.worker.join(timeout=5)
    assert len(runs.started) == 4
    assert "UNIQUE_REPLY_1" in runs.started[1]["prompt"]
    assert "UNIQUE_REPLY_1" in runs.started[2]["prompt"]
    assert "UNIQUE_REPLY_2" in runs.started[2]["prompt"]
    assert all(f"UNIQUE_REPLY_{i}" in runs.started[3]["prompt"] for i in (1, 2, 3))
    assert runs.started[1]["prompt"].count("ROUND_ONE") == 1
    assert runs.started[3]["prompt"].count("ROUND_TWO") == 1


@pytest.mark.parametrize("agent,mode", [
    ("codex", "read-only"), ("deepseek-harness", "read-only"),
    ("cursor", "plan"), ("zcode", "plan"),
])
def test_defaults_pass_real_execution_validation_without_starting_cli(tmp_path, agent, mode):
    runs = ExecutionManager(tmp_path / "data", registry=FakeRuns().registry)
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path, [{"id": agent}])
    assert conv["members"][0]["sandbox"] == mode
    _, request, _ = runs.validate({
        "request_id": uuid.uuid4().hex, "agent": agent, "prompt": "fixture", "cwd": str(tmp_path),
        "sandbox": conv["members"][0]["sandbox"],
    })
    assert request["sandbox"] == mode
    manager.close()
    runs.close()


@pytest.mark.parametrize("busy", ["owner", "active"])
def test_busy_rejection_preserves_exact_record_and_allows_later_send(tmp_path, busy):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    path = manager._path(conv["id"]) / "conversation.json"
    before = path.read_bytes()
    setattr(runs, busy, "occupied")
    with pytest.raises(ValueError, match="已有任务"):
        manager.message({"id": conv["id"], "text": "must not persist"})
    assert path.read_bytes() == before
    assert manager.active is None and manager.worker is None and runs.started == []
    setattr(runs, busy, None)
    manager.message({"id": conv["id"], "text": "retry after release"})
    manager.worker.join(timeout=5)
    assert manager.get(conv["id"])["status"] == "succeeded"


def test_invalid_later_member_fails_preflight_without_first_call_or_messages(tmp_path):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    record = manager._read(conv["id"])
    record["members"][1]["sandbox"] = "invalid"
    manager._save(record)
    path = manager._path(conv["id"]) / "conversation.json"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="权限模式"):
        manager.message({"id": conv["id"], "text": "reject whole turn"})
    assert runs.started == [] and runs.owner is None and manager.active is None
    assert path.read_bytes() == before


def test_thread_start_failure_restores_original_and_releases_execution_slot(tmp_path, monkeypatch):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    path = manager._path(conv["id"]) / "conversation.json"
    before = path.read_bytes()
    def fail(_thread):
        raise RuntimeError("injected thread failure")
    monkeypatch.setattr(threading.Thread, "start", fail)
    with pytest.raises(RuntimeError, match="injected"):
        manager.message({"id": conv["id"], "text": "not accepted"})
    assert path.read_bytes() == before
    assert manager.active is None and manager.worker is None and runs.owner is None


def test_save_failure_restores_record_and_releases_slot(tmp_path, monkeypatch):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    original = manager._read(conv["id"])
    save = manager._save
    def fail_once(record):
        if record["status"] == "running":
            raise OSError("injected disk error")
        save(record)
    monkeypatch.setattr(manager, "_save", fail_once)
    with pytest.raises(OSError, match="injected"):
        manager.message({"id": conv["id"], "text": "not accepted"})
    assert manager._read(conv["id"]) == original and runs.owner is None


def test_cursor_trust_is_validated_against_workspace_before_any_mutation(tmp_path):
    runs = ExecutionManager(tmp_path / "data", registry=FakeRuns().registry)
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path, [{"id": "cursor"}])
    with pytest.raises(ValueError, match="信任确认"):
        manager.message({"id": conv["id"], "text": "fixture", "cursor_trusted_cwd": str(tmp_path.parent)})
    assert manager.get(conv["id"])["messages"] == [] and runs.owner is None
    manager.close()
    runs.close()


def test_trust_only_reaches_selected_cursor_member_and_is_not_reused(tmp_path):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path, [{"id": "codex"}, {"id": "cursor"}])
    manager.message({"id": conv["id"], "text": "one", "cursor_trusted_cwd": str(tmp_path)})
    manager.worker.join(timeout=5)
    assert runs.started[0]["cursor_trusted_cwd"] == ""
    assert Path(runs.started[1]["cursor_trusted_cwd"]) == tmp_path
    manager.message({"id": conv["id"], "text": "two"})
    manager.worker.join(timeout=5)
    assert all(not run["cursor_trusted_cwd"] for run in runs.started[2:])


def test_long_user_message_keeps_recent_handoff_within_execution_limit(tmp_path):
    runs = FakeRuns(result="x" * 12_000 + "RECENT_HANDOFF_MARKER")
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    manager.message({"id": conv["id"], "text": "u" * 20_000})
    manager.worker.join(timeout=5)
    assert all(len(call["prompt"]) <= 30_000 for call in runs.started)
    assert "RECENT_HANDOFF_MARKER" in runs.started[1]["prompt"]
    assert "更早的对话已截取" in runs.started[1]["prompt"]


def test_cancel_stops_remaining_members_and_releases_slot(tmp_path):
    runs = FakeRuns(status="running")
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    manager.message({"id": conv["id"], "text": "wait for cancel"})
    deadline = time.monotonic() + 3
    while not runs.started and time.monotonic() < deadline:
        time.sleep(0.01)
    manager.cancel(conv["id"])
    manager.worker.join(timeout=5)
    final = manager.get(conv["id"])
    assert final["status"] == "cancelled"
    assert all(m["status"] == "cancelled" for m in final["messages"] if m["role"] == "assistant")
    assert len(runs.started) == 1 and runs.owner is None and manager.active is None
