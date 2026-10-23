import uuid

import pytest

from agent_workbench.scheduling import WorkflowManager
from agent_workbench.scheduling import step_prompt
from agent_workbench.storage import write_json
from test_scheduling import body, node, wait, runtime as shared_runtime

runtime = shared_runtime


def configured(work, maximum=2, prompt="repair"):
    request = body(work, [node("codex", "worker", prompt=prompt)])
    request.update(permission="edit", max_reworks=maximum,
                   rework_node_id=request["team"]["nodes"][0]["id"])
    request["team"]["acceptance"] = [{"path": "out.json", "kind": "json", "changed": True}]
    return request


def patch_fixture(work, script):
    fixture = work.parent / "fixture.py"
    fixture.write_text(fixture.read_text(encoding="utf-8").replace('failed="FAIL_NODE" in duty', script + '\nfailed="FAIL_NODE" in duty'), encoding="utf-8")


def test_limited_rework_carries_reasons_and_keeps_original_baseline(runtime):
    flows, runs, work = runtime
    patch_fixture(work, 'if "/ 验收返工" in p: Path("out.json").write_text("{}")')
    result = wait(flows, flows.start(configured(work))["id"])
    assert result["status"] == "succeeded" and result["acceptance"]["status"] == "passed"
    assert result["rework_calls"] == 1
    assert [s["phase"] for s in result["steps"]] == ["review", "rework"]
    assert [e["status"] for e in result["acceptance_history"]] == ["failed", "passed"]
    assert result["acceptance"]["checks"][0]["before"] == {"exists": False}
    repair = runs.get(result["steps"][-1]["run_id"])
    assert "out.json：文件不存在" in repair["prompt"]
    assert "HANDOFF_TOKEN" in repair["prompt"]


def test_limit_persists_across_restart_and_recheck_never_calls_agents(runtime):
    flows, runs, work = runtime
    result = wait(flows, flows.start(configured(work))["id"])
    assert result["rework_calls"] == 2 and len(result["steps"]) == 3
    assert result["acceptance"]["status"] == "failed"
    flows.close()
    restored = WorkflowManager(flows.root.parent, runs)
    try:
        before = len(runs.list())
        result = wait(restored, restored.resume({"id": result["id"], "request_id": uuid.uuid4().hex})["id"])
        assert result["acceptance"]["status"] == "failed" and result["rework_calls"] == 2
        assert len(runs.list()) == before
        (work / "out.json").write_text("{}")
        result = wait(restored, restored.resume({"id": result["id"], "request_id": uuid.uuid4().hex})["id"])
        assert result["acceptance"]["status"] == "passed" and len(runs.list()) == before
    finally:
        restored.close()


def test_resume_reconciles_completed_run_before_checkpoint_and_is_idempotent(runtime):
    flows, runs, work = runtime
    request = body(work, [node("codex", "worker")])
    result = wait(flows, flows.start(request)["id"])
    flows.worker.join(5)
    record = flows._read(result["id"])
    record.update(status="interrupted")
    record["steps"][0].update(status="waiting", launched=False)
    flows._save(record)
    request_id = uuid.uuid4().hex
    result = wait(flows, flows.resume({"id": result["id"], "request_id": request_id})["id"])
    assert result["status"] == "succeeded" and len(runs.list()) == 1
    assert result["resumptions"][0]["pending_steps"] == 0
    flows.resume({"id": result["id"], "request_id": request_id})
    assert len(runs.list()) == 1


def test_resume_only_failed_step_preserves_prior_attempt_and_handoff(runtime):
    flows, runs, work = runtime
    request = body(work, [node("codex", "root"), node("codex", "broken", "root", "FAIL_NODE")])
    result = wait(flows, flows.start(request)["id"])
    first_id, failed_id = [s["run_id"] for s in result["steps"][:2]]
    fixture = work.parent / "fixture.py"
    fixture.write_text(fixture.read_text(encoding="utf-8").replace('failed="FAIL_NODE" in duty', 'failed=False'), encoding="utf-8")
    result = wait(flows, flows.resume({"id": result["id"], "request_id": uuid.uuid4().hex})["id"])
    assert result["status"] == "succeeded" and len(runs.list()) == 4
    assert result["steps"][0]["run_id"] == first_id
    assert result["steps"][1]["attempts"][0]["run_id"] == failed_id
    assert runs.get(failed_id)["status"] == "failed"
    assert "HANDOFF_TOKEN" in runs.get(result["steps"][1]["run_id"])["prompt"]


def test_interrupted_rework_budget_cannot_be_reset_by_resume(runtime):
    flows, runs, work = runtime
    patch_fixture(work, 'if "/ 验收返工" in p: duty="FAIL_NODE"')
    result = wait(flows, flows.start(configured(work, 1))["id"])
    assert result["status"] == "failed" and result["rework_calls"] == 1
    assert result["acceptance"]["status"] == "not_run"
    with pytest.raises(ValueError, match="用尽"):
        flows.resume({"id": result["id"], "request_id": uuid.uuid4().hex})
    assert len(runs.list()) == 2


@pytest.mark.parametrize("change", [{"max_reworks": 4}, {"max_reworks": True}, {"permission": "read-only"}, {"rework_node_id": "missing"}])
def test_rework_rejects_invalid_policy_before_writes(runtime, change):
    flows, runs, work = runtime
    request = configured(work)
    request.update(change)
    with pytest.raises(ValueError):
        flows.start(request)
    assert not flows.list() and not runs.list()


def test_restart_marks_pending_steps_and_resumes_from_failed_checkpoint(runtime):
    flows, runs, work = runtime
    result = wait(flows, flows.start(body(work, [node("codex", "worker")]))["id"])
    flows.close()
    record = flows._read(result["id"])
    record.update(status="running")
    record["steps"][0]["status"] = "running"
    write_json(flows._path(record["id"]) / "workflow.json", record)
    restored = WorkflowManager(flows.root.parent, runs)
    try:
        assert restored.get(record["id"])["status"] == "interrupted"
        result = wait(restored, restored.resume({"id": record["id"], "request_id": uuid.uuid4().hex})["id"])
        assert result["status"] == "succeeded" and len(runs.list()) == 1
    finally:
        restored.close()


def test_cancel_during_rework_stops_further_calls(runtime):
    flows, runs, work = runtime
    patch_fixture(work, 'if "/ 验收返工" in p: time.sleep(120)')
    result = flows.start(configured(work, 3))
    wait(flows, result["id"], lambda r: len(r["steps"]) == 2 and r["steps"][1]["status"] == "running")
    flows.cancel(result["id"])
    result = wait(flows, result["id"])
    assert result["status"] == "cancelled"
    assert result["acceptance"]["status"] == "not_run"
    assert result["rework_calls"] == 1 and len(runs.list()) == 2


def test_missing_successful_handoff_refuses_resume(runtime):
    flows, runs, work = runtime
    result = wait(flows, flows.start(body(work, [node("codex", "worker")]))["id"])
    record = flows._read(result["id"])
    record["status"] = "interrupted"
    flows._save(record)
    (runs._path(record["steps"][0]["run_id"]) / "result.md").unlink()
    with pytest.raises(ValueError, match="交接文件缺失"):
        flows.resume({"id": record["id"], "request_id": uuid.uuid4().hex})
    assert len(runs.list()) == 1


def test_resume_can_add_explicit_cursor_trust_for_original_directory(runtime):
    flows, runs, work = runtime
    result = wait(flows, flows.start(body(work, [node("cursor", "worker", prompt="TRUST_NODE")]))["id"])
    assert result['trust_required']
    with pytest.raises(ValueError, match="原任务"):
        flows.resume({'id': result['id'], 'request_id': uuid.uuid4().hex, 'cursor_trusted_cwd': str(work.parent)})
    result = wait(flows, flows.resume({'id': result['id'], 'request_id': uuid.uuid4().hex, 'cursor_trusted_cwd': str(work.resolve())})['id'])
    assert result['status'] == 'succeeded' and result['cursor_trusted_cwd'] == str(work.resolve())


def test_long_context_cannot_clip_rework_failure_instructions(tmp_path):
    request = configured(tmp_path, prompt="d" * 10000)
    request["team"]["goal"] = "g" * 5000
    request["team"]["acceptance"] = [dict(path=f"{i}.json", kind="contains", changed=True, expected="e" * 900) for i in range(4)]
    prompt = step_prompt(request, {"node_id": request["rework_node_id"], "phase": "rework", "feedback": "REPAIR_FAILURE_MUST_SURVIVE"}, [])
    assert "REPAIR_FAILURE_MUST_SURVIVE" in prompt
    assert len(prompt) < 30000
