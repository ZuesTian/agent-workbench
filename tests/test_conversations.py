"""Conversation storage, validation and serial reply execution."""

import json
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from agent_workbench.conversations import (
    ERROR_TEXT_LIMIT,
    REPLY_TEXT_LIMIT,
    ConversationManager,
)
from agent_workbench.execution import ExecutionManager
from agent_workbench.runners import RUNNERS, RunnerRegistry
from agent_workbench.service import WorkbenchService


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


def fail_latest_round(manager, runs, conversation_id, text="会失败"):
    """Run one round whose first member fails and later members stay unfinished."""
    runs.status = "failed"
    manager.message({"id": conversation_id, "text": text})
    manager.worker.join(timeout=5)
    return manager.get(conversation_id)


def wait_for_started(runs, count, timeout=3):
    deadline = time.monotonic() + timeout
    while len(runs.started) < count and time.monotonic() < deadline:
        time.sleep(0.01)
    return len(runs.started)


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


@pytest.mark.parametrize("cleanup_error", [ValueError, RuntimeError])
def test_cleanup_record_error_releases_conversation_and_allows_resend(tmp_path, monkeypatch, cleanup_error):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    original_start = runs.start

    def fail_start(body, owner=None):
        runs.active = body["request_id"]
        raise ValueError("injected redirected record")

    def fail_cancel(_run_id):
        runs.active = None
        raise cleanup_error("injected unreadable cleanup record")

    with monkeypatch.context() as patch:
        patch.setattr(runs, "start", fail_start)
        patch.setattr(runs, "cancel", fail_cancel)
        manager.message({"id": conv["id"], "text": "first turn fails before CLI"})
        manager.worker.join(timeout=5)
    failed = manager.get(conv["id"])
    assert failed["status"] == "failed" and not failed["running"]
    assert all(m["status"] == "error" for m in failed["messages"] if m["role"] == "assistant")
    assert runs.owner is None and manager.active is None
    assert runs.start == original_start
    manager.message({"id": conv["id"], "text": "resend after failed cleanup"})
    manager.worker.join(timeout=5)
    assert manager.get(conv["id"])["status"] == "succeeded"

# ---- retry --------------------------------------------------------------
def test_retry_failed_reply_appends_result_and_keeps_original_message(tmp_path):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    failed = fail_latest_round(manager, runs, conv["id"])
    user_message, original, unfinished = failed["messages"]
    assert original["status"] == "failed" and unfinished["status"] == "error"
    runs.status = "succeeded"
    runs.result = "重试后的回复"
    updated = manager.retry({"id": conv["id"], "message_ids": [original["id"]]})
    assert [m["id"] for m in updated["messages"][:3]] == [
        m["id"] for m in failed["messages"]
    ]
    retried = updated["messages"][-1]
    assert retried["id"] != original["id"]
    assert retried["role"] == "assistant"
    assert retried["retry_of"] == original["id"]
    assert retried["round_id"] == user_message["id"]
    assert retried["member"] == "codex" and retried["name"] == original["name"]
    assert retried["status"] == "queued" and retried["text"] == ""
    manager.worker.join(timeout=5)
    final = manager.get(conv["id"])
    assert final["status"] == "succeeded"
    assert len(runs.started) == 2
    assert runs.started[-1]["agent"] == "codex"
    assert runs.started[-1]["prompt"].count("会失败") == 1
    kept = next(m for m in final["messages"] if m["id"] == original["id"])
    assert kept["status"] == "failed" and kept["text"] == original["text"]
    assert final["messages"][-1]["status"] == "succeeded"
    assert final["messages"][-1]["text"] == "重试后的回复"


def test_retry_without_ids_retries_all_unfinished_replies_in_message_order(tmp_path):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    failed = fail_latest_round(manager, runs, conv["id"])
    user_message, failed_reply, unfinished_reply = failed["messages"]
    runs.status = "succeeded"
    runs.result = lambda _body, index: f"RETRY_REPLY_{index}"
    updated = manager.retry({"id": conv["id"]})
    new_replies = updated["messages"][3:]
    assert [m["retry_of"] for m in new_replies] == [
        failed_reply["id"],
        unfinished_reply["id"],
    ]
    assert [m["member"] for m in new_replies] == ["codex", "deepseek-harness"]
    assert all(m["round_id"] == user_message["id"] for m in new_replies)
    manager.worker.join(timeout=5)
    final = manager.get(conv["id"])
    assert final["status"] == "succeeded"
    assert [m["status"] for m in final["messages"][3:]] == ["succeeded", "succeeded"]
    assert [m["text"] for m in final["messages"][3:]] == ["RETRY_REPLY_2", "RETRY_REPLY_3"]
    assert [call["agent"] for call in runs.started] == [
        "codex",
        "codex",
        "deepseek-harness",
    ]


@pytest.mark.parametrize("busy", ["owner", "active"])
def test_retry_rejected_while_execution_busy_preserves_record(tmp_path, busy):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    failed = fail_latest_round(manager, runs, conv["id"])
    path = manager._path(conv["id"]) / "conversation.json"
    before = path.read_bytes()
    started = len(runs.started)
    setattr(runs, busy, "occupied")
    with pytest.raises(ValueError, match="已有任务"):
        manager.retry({"id": conv["id"], "message_ids": [failed["messages"][1]["id"]]})
    assert path.read_bytes() == before
    assert len(runs.started) == started
    assert manager.active is None
    setattr(runs, busy, None)
    runs.status = "succeeded"
    manager.retry({"id": conv["id"], "message_ids": [failed["messages"][1]["id"]]})
    manager.worker.join(timeout=5)
    assert manager.get(conv["id"])["status"] == "succeeded"


@pytest.mark.parametrize("status", ["succeeded", "queued", "running"])
def test_retry_rejects_reply_that_is_not_retryable(tmp_path, status):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    manager.message({"id": conv["id"], "text": "正常一轮"})
    manager.worker.join(timeout=5)
    record = manager._read(conv["id"])
    target = record["messages"][1]
    target["status"] = status
    manager._save(record)
    with pytest.raises(ValueError, match="只能重试"):
        manager.retry({"id": conv["id"], "message_ids": [target["id"]]})
    assert manager.active is None and runs.owner is None


def test_retry_rejects_reply_from_earlier_round(tmp_path):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    first = fail_latest_round(manager, runs, conv["id"], text="第一轮")
    first_reply_id = first["messages"][1]["id"]
    second = fail_latest_round(manager, runs, conv["id"], text="第二轮")
    current_user_id = second["messages"][-3]["id"]
    assert second["messages"][-3]["role"] == "user"
    with pytest.raises(ValueError, match="最近一轮"):
        manager.retry({"id": conv["id"], "message_ids": [first_reply_id]})
    with pytest.raises(ValueError, match="最近一轮"):
        manager.retry({"id": conv["id"], "message_ids": [current_user_id]})
    assert manager.active is None and runs.owner is None


def test_retry_rejects_reply_whose_member_was_removed(tmp_path):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    failed = fail_latest_round(manager, runs, conv["id"])
    record = manager._read(conv["id"])
    record["members"] = [m for m in record["members"] if m["id"] != "codex"]
    manager._save(record)
    path = manager._path(conv["id"]) / "conversation.json"
    before = path.read_bytes()
    started = len(runs.started)
    with pytest.raises(ValueError, match="重新绑定成员"):
        manager.retry({"id": conv["id"], "message_ids": [failed["messages"][1]["id"]]})
    assert path.read_bytes() == before
    assert len(runs.started) == started
    assert runs.owner is None and manager.active is None


def test_retry_invalid_later_member_fails_preflight_without_calls_or_messages(tmp_path):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    fail_latest_round(manager, runs, conv["id"])
    record = manager._read(conv["id"])
    record["members"][1]["sandbox"] = "invalid"
    manager._save(record)
    path = manager._path(conv["id"]) / "conversation.json"
    before = path.read_bytes()
    started = len(runs.started)
    with pytest.raises(ValueError, match="权限模式"):
        manager.retry({"id": conv["id"]})
    assert len(runs.started) == started
    assert runs.owner is None and manager.active is None
    assert path.read_bytes() == before
    assert len(manager.get(conv["id"])["messages"]) == 3


def test_retry_can_be_cancelled_and_releases_execution_slot(tmp_path):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    failed = fail_latest_round(manager, runs, conv["id"])
    original = failed["messages"][1]
    runs.status = "running"
    manager.retry({"id": conv["id"], "message_ids": [original["id"]]})
    assert wait_for_started(runs, 2) == 2
    manager.cancel(conv["id"])
    manager.worker.join(timeout=5)
    final = manager.get(conv["id"])
    assert final["status"] == "cancelled"
    retried = final["messages"][-1]
    assert retried["retry_of"] == original["id"]
    assert retried["status"] == "cancelled"
    kept = next(m for m in final["messages"] if m["id"] == original["id"])
    assert kept["status"] == "failed"
    assert runs.owner is None and manager.active is None


def test_retry_without_ids_rejects_when_latest_round_succeeded(tmp_path):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    manager.message({"id": conv["id"], "text": "成功一轮"})
    manager.worker.join(timeout=5)
    path = manager._path(conv["id"]) / "conversation.json"
    before = path.read_bytes()
    started = len(runs.started)
    with pytest.raises(ValueError, match="没有未完成"):
        manager.retry({"id": conv["id"]})
    assert path.read_bytes() == before and len(runs.started) == started
    assert manager.active is None


def test_retry_rejects_empty_or_foreign_message_ids(tmp_path):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    fail_latest_round(manager, runs, conv["id"])
    with pytest.raises(ValueError, match="请选择"):
        manager.retry({"id": conv["id"], "message_ids": []})
    with pytest.raises(ValueError, match="编号列表"):
        manager.retry({"id": conv["id"], "message_ids": "abc"})
    with pytest.raises(ValueError, match="无效回复编号"):
        manager.retry({"id": conv["id"], "message_ids": ["not-an-id"]})
    with pytest.raises(ValueError, match="最近一轮"):
        manager.retry({"id": conv["id"], "message_ids": [uuid.uuid4().hex]})
    assert manager.active is None and runs.owner is None


def test_retry_cursor_trust_is_only_passed_to_cursor_member(tmp_path):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path, [{"id": "codex"}, {"id": "cursor"}])
    failed = fail_latest_round(manager, runs, conv["id"])
    codex_reply = next(m for m in failed["messages"] if m.get("member") == "codex")
    cursor_reply = next(m for m in failed["messages"] if m.get("member") == "cursor")
    assert cursor_reply["status"] in {"failed", "error", "cancelled", "interrupted"}
    with pytest.raises(ValueError, match="信任确认"):
        manager.retry(
            {
                "id": conv["id"],
                "message_ids": [codex_reply["id"]],
                "cursor_trusted_cwd": str(tmp_path),
            }
        )
    runs.status = "succeeded"
    manager.retry(
        {
            "id": conv["id"],
            "message_ids": [cursor_reply["id"]],
            "cursor_trusted_cwd": str(tmp_path),
        }
    )
    manager.worker.join(timeout=5)
    assert runs.started[-1]["agent"] == "cursor"
    assert Path(runs.started[-1]["cursor_trusted_cwd"]) == tmp_path
    assert manager.get(conv["id"])["messages"][-1]["status"] == "succeeded"


def test_service_routes_retry_to_conversation_manager():
    calls = []

    class Recorder:
        def retry(self, body):
            calls.append(dict(body))
            return {"id": body["id"], "retried": True}

    service = WorkbenchService.__new__(WorkbenchService)
    service.conversations = Recorder()
    body = {"id": uuid.uuid4().hex, "message_ids": [uuid.uuid4().hex]}
    assert service._post("/api/conversations/retry", body) == {
        "id": body["id"],
        "retried": True,
    }
    assert calls == [body]


def test_retry_rejects_more_replies_than_members_limit(tmp_path):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    record = manager._read(conv["id"])
    now = record["created_at"]
    record["messages"].append(
        {
            "id": uuid.uuid4().hex,
            "role": "user",
            "text": "手工轮次",
            "status": "done",
            "created_at": now,
        }
    )
    message_ids = []
    for _ in range(7):
        message_id = uuid.uuid4().hex
        record["messages"].append(
            {
                "id": message_id,
                "role": "assistant",
                "member": "codex",
                "name": "Codex CLI",
                "text": "",
                "status": "failed",
                "created_at": now,
            }
        )
        message_ids.append(message_id)
    manager._save(record)
    with pytest.raises(ValueError, match="最多重试"):
        manager.retry({"id": conv["id"], "message_ids": message_ids})
    assert manager.active is None and runs.owner is None


def test_retry_rejected_after_manager_closed(tmp_path):
    runs = FakeRuns()
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    fail_latest_round(manager, runs, conv["id"])
    manager.close()
    with pytest.raises(ValueError, match="正在退出"):
        manager.retry({"id": conv["id"]})


def test_long_reply_is_bounded_in_conversation_and_keeps_result_reference(tmp_path):
    runs = FakeRuns(result="R" * 25_000 + "TAIL_MARKER")
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    manager.message({"id": conv["id"], "text": "长回复"})
    manager.worker.join(timeout=5)
    reply = next(m for m in manager.get(conv["id"])["messages"] if m["role"] == "assistant")
    assert len(reply["text"]) == REPLY_TEXT_LIMIT
    assert reply["text"].endswith("[回复过长已截取，完整结果见执行记录]")
    assert "TAIL_MARKER" not in reply["text"]
    assert reply["result_path"].endswith("result.md")


def test_long_error_text_is_bounded_in_conversation(tmp_path):
    runs = FakeRuns(status="failed", result="")
    manager = make_manager(tmp_path, runs)
    conv = create(manager, tmp_path)
    long_error = "E" * 5_000
    original_get = runs.get

    def get_with_long_error(run_id):
        record = original_get(run_id)
        record["error"] = long_error
        return record

    runs.get = get_with_long_error
    manager.message({"id": conv["id"], "text": "会失败"})
    manager.worker.join(timeout=5)
    reply = next(m for m in manager.get(conv["id"])["messages"] if m["role"] == "assistant")
    assert reply["status"] == "failed"
    assert len(reply["error"]) == ERROR_TEXT_LIMIT
    assert reply["text"] == long_error


def test_list_preview_prefers_a_member_reply_over_a_user_message(tmp_path):
    manager = make_manager(tmp_path)
    conv = create(manager, tmp_path)
    record = manager._read(conv["id"])
    now = record["created_at"]
    record["messages"] = [
        {
            "id": uuid.uuid4().hex,
            "role": "user",
            "text": "用户的问题",
            "status": "done",
            "created_at": now,
        },
        {
            "id": uuid.uuid4().hex,
            "role": "assistant",
            "member": "codex",
            "name": "Codex CLI",
            "text": "成员的回复",
            "status": "succeeded",
            "created_at": now,
        },
        {
            "id": uuid.uuid4().hex,
            "role": "user",
            "text": "新的问题",
            "status": "done",
            "created_at": now,
        },
    ]
    manager._save(record)
    assert manager.list()["items"][0]["preview"] == "成员的回复"


