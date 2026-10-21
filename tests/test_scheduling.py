import json
import shutil
import sys
import time
import uuid
from pathlib import Path

import pytest

from agent_workbench.execution import ExecutionManager, ACTIVE
from agent_workbench.runners import RunnerRegistry, prepare_command, zcode_environment
from agent_workbench.scheduling import WorkflowManager
from agent_workbench.storage import write_json


FAKE = r'''
import json,sys,time
from pathlib import Path
p=sys.stdin.read()
duty=p.split("## 本节点职责\n")[-1].split("\n\n## 当前步骤")[0]
dsh="--profile" in sys.argv
cursor="--workspace" in sys.argv
if cursor and "--trust" not in sys.argv:
 if "TRUST_NODE" in duty:
  print("Workspace Trust Required",file=sys.stderr); sys.exit(1)
 if "ALT_TRUST" in duty:
  print("\x1b[33mDo you trust the contents of this directory?\x1b[0m",file=sys.stderr); sys.exit(1)
 if "JSON_TRUST" in duty:
  print(json.dumps({"type":"result","subtype":"error","is_error":True,"result":"Workspace Trust Required"})); sys.exit(1)
kind="cursor" if cursor else "dsh" if dsh else "codex"
def emit(x):print(json.dumps(x),flush=True)
emit({"type":"session","sessionId":"fixture"} if dsh else {"type":"thread.started","thread_id":"fixture"})
if "SLOW_NODE" in duty:time.sleep(120)
failed="FAIL_NODE" in duty
if p=="missing-final":sys.exit(0)
answer="HANDOFF_TOKEN " + kind + (" received" if "HANDOFF_TOKEN" in p else " initial")
if cursor:
 emit({"type":"result","subtype":"error" if failed else "success","is_error":failed,"result":answer,"session_id":"cursor-fixture"})
elif dsh:
 emit({"type":"status","phase":"turn_end","reason":{"kind":"error" if failed else "completed"}})
 emit({"type":"final","text":answer})
else:
 Path(sys.argv[sys.argv.index("--output-last-message")+1]).write_text(answer,encoding="utf-8")
 emit({"type":"turn.failed","error":{"message":"fixture failure"}} if failed else {"type":"turn.completed"})
sys.exit(0)
'''

FAKE_ZCODE = r'''
const prompt=process.argv[process.argv.indexOf('--prompt')+1];
if(prompt==='missing-final') process.exit(0);
console.log(JSON.stringify({type:'result',sessionId:'zcode-fixture',
 response:'HANDOFF_TOKEN zcode '+(prompt.includes('HANDOFF_TOKEN')?'received':'initial'),
 projection:{status:'idle'}}));
'''


def node(agent, ident, parent=None, prompt="完成指定工作"):
    return dict(
        id=uuid.uuid5(uuid.NAMESPACE_DNS, ident).hex,
        parentId=uuid.uuid5(uuid.NAMESPACE_DNS, parent).hex if parent else None,
        role="employee" if parent else "tutor", name=ident,
        agentId=agent, model="", prompt=prompt, policy="给出验证依据", skills=[], mcps=[],
        x=0, y=0, collapsed=False,
    )


def team(nodes):
    return dict(format="agent-workbench-team", version=1, name="自动协作测试", goal="交付并审核结果",
                layout="horizontal", nodes=nodes)


def body(work, nodes=None):
    return dict(
        request_id=uuid.uuid4().hex, team=team(nodes or [
            node("codex", "负责人"), node("deepseek-harness", "实现", "负责人"),
            node("zcode", "核验", "负责人"),
        ]), cwd=str(work), permission="read-only", timeout_minutes=1,
    )


def wait(manager, ident, predicate=lambda r: r["status"] not in ACTIVE):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        record = manager.get(ident)
        if predicate(record):
            return record
        time.sleep(0.03)
    raise AssertionError(manager.get(ident))


@pytest.fixture
def runtime(tmp_path):
    python = tmp_path / "fixture.py"
    python.write_text(FAKE, encoding="utf-8")
    javascript = tmp_path / "fixture.cjs"
    javascript.write_text(FAKE_ZCODE, encoding="utf-8")
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js required for bundled-CLI protocol fixture")
    def codex():
        return [sys.executable, str(python)]
    registry = RunnerRegistry(codex, {
        "deepseek-harness": codex, "zcode": lambda: [node_exe, str(javascript)], "cursor": codex,
    })
    runs = ExecutionManager(tmp_path / "data", registry=registry)
    flows = WorkflowManager(tmp_path / "data", runs)
    work = tmp_path / "中文 project"
    work.mkdir()
    yield flows, runs, work
    flows.close()
    runs.close()


def test_three_protocols_order_and_real_result_handoff(runtime):
    flows, runs, work = runtime
    request = body(work)
    flow = flows.start(request)
    request["team"]["goal"] = "编辑中的草稿不应影响运行"
    result = wait(flows, flow["id"])
    assert result["status"] == "succeeded"
    assert [(s["name"], s["phase"]) for s in result["steps"]] == [
        ("负责人", "assign"), ("实现", "deliver"), ("核验", "deliver"), ("负责人", "review"),
    ]
    assert all(s["status"] == "succeeded" for s in result["steps"])
    assert result["team"]["goal"] == "交付并审核结果"
    children = [runs.get(s["run_id"]) for s in result["steps"]]
    assert children[0]["result"] == "HANDOFF_TOKEN codex initial"
    assert all("received" in run["result"] for run in children[1:])
    assert all(run["workflow_id"] == result["id"] for run in children)
    assert children[2]["sandbox"] == "plan"
    assert "codex received" in result["result"]
    assert flows.runs.owner is None
    original = {**request, "team": result["team"]}
    assert flows.start(original)["id"] == flow["id"]
    assert len(flows.list()) == 1 and len(runs.list()) == 4
    with pytest.raises(ValueError, match="不同协作"):
        flows.start(request)


def test_failure_stops_all_following_nodes_even_exit_zero(runtime):
    flows, runs, work = runtime
    flow = flows.start(body(work, [
        node("codex", "负责人"),
        node("deepseek-harness", "失败节点", "负责人", "FAIL_NODE"),
        node("zcode", "后续节点", "负责人"),
    ]))
    result = wait(flows, flow["id"])
    assert result["status"] == "failed"
    assert [s["status"] for s in result["steps"]] == ["succeeded", "failed", "blocked", "blocked"]
    assert len(runs.list()) == 2
    assert "失败节点" in result["error"]


def test_cursor_coordinates_harness_and_receives_review_handoff(runtime):
    flows, runs, work = runtime
    result = wait(flows, flows.start(body(work, [
        node("cursor", "设计与实现"),
        node("deepseek-harness", "实现助手", "设计与实现"),
    ]))["id"])
    assert result["status"] == "succeeded"
    assert [step["agent"] for step in result["steps"]] == ["cursor", "deepseek-harness", "cursor"]
    children = [runs.get(step["run_id"]) for step in result["steps"]]
    assert children[0]["result"] == "HANDOFF_TOKEN cursor initial"
    assert children[2]["result"] == "HANDOFF_TOKEN cursor received"
    assert children[0]["sandbox"] == "plan"
    assert children[2]["thread_id"] == "cursor-fixture"


def test_cursor_trust_confirmation_reaches_only_cursor_steps(runtime):
    flows, runs, work = runtime
    request = body(work, [
        node("cursor", "负责人", prompt="TRUST_NODE"),
        node("deepseek-harness", "助手", "负责人"),
    ])
    request["cursor_trusted_cwd"] = str(work)
    result = wait(flows, flows.start(request)["id"])
    assert result["status"] == "succeeded"
    records = [runs.get(s["run_id"]) for s in result["steps"]]
    assert [r["cursor_trusted_cwd"] for r in records] == [str(work.resolve()), "", str(work.resolve())]
    with pytest.raises(ValueError, match="不同协作"):
        flows.start({**request, "cursor_trusted_cwd": ""})


def test_cursor_trust_error_gives_actionable_directory_confirmation(runtime):
    flows, runs, work = runtime
    result = wait(flows, flows.start(body(work, [node("cursor", "审核", prompt="TRUST_NODE")]))["id"])
    assert result["status"] == "failed"
    assert "信任此工作目录（Cursor 将记住）" in result["error"]
    assert runs.get(result["steps"][0]["run_id"])["cursor_trusted_cwd"] == ""


def test_same_request_can_add_cursor_trust_after_a_trust_failure(runtime):
    _flows, runs, work = runtime
    task = dict(request_id=uuid.uuid4().hex, agent="cursor", prompt="TRUST_NODE",
                cwd=str(work), sandbox="plan", timeout_minutes=1)
    failed = wait(runs, runs.start(task)["id"])
    assert failed["status"] == "failed" and failed["trust_required"] is True
    with pytest.raises(ValueError, match="不同任务"):
        runs.start({**task, "prompt": "其他任务", "cursor_trusted_cwd": str(work)})
    retried = wait(runs, runs.start({**task, "cursor_trusted_cwd": str(work)})["id"])
    assert retried["status"] == "succeeded"
    assert retried["cursor_trusted_cwd"] == str(work.resolve())
    assert "信任确认" in retried["log"]
    assert runs.start({**task, "cursor_trusted_cwd": str(work)})["status"] == "succeeded"


def test_non_trust_failure_rejects_a_trust_only_retry(runtime):
    _flows, runs, work = runtime
    task = dict(request_id=uuid.uuid4().hex, agent="cursor", prompt="FAIL_NODE",
                cwd=str(work), sandbox="plan", timeout_minutes=1)
    failed = wait(runs, runs.start(task)["id"])
    assert failed["status"] == "failed" and failed["trust_required"] is False
    with pytest.raises(ValueError, match="不同任务"):
        runs.start({**task, "cursor_trusted_cwd": str(work)})


def test_reworded_and_json_trust_errors_stay_actionable(runtime):
    _flows, runs, work = runtime
    for prompt in ("ALT_TRUST", "JSON_TRUST"):
        failed = wait(runs, runs.start(dict(
            request_id=uuid.uuid4().hex, agent="cursor", prompt=prompt,
            cwd=str(work), sandbox="plan", timeout_minutes=1,
        ))["id"])
        assert failed["trust_required"] is True
        assert "信任此工作目录（Cursor 将记住）" in failed["error"]


def test_failed_workflow_retries_when_only_cursor_trust_is_added(runtime):
    flows, runs, work = runtime
    request = body(work, [
        node("codex", "负责人"),
        node("cursor", "实现", "负责人", "TRUST_NODE"),
    ])
    failed = wait(flows, flows.start(request)["id"])
    assert failed["status"] == "failed" and failed["trust_required"] is True
    assert [step["status"] for step in failed["steps"]] == ["succeeded", "failed", "blocked"]
    with pytest.raises(ValueError, match="不同协作"):
        flows.start({**request, "team": {**request["team"], "goal": "另一个目标"},
                     "cursor_trusted_cwd": str(work)})
    request["cursor_trusted_cwd"] = str(work)
    result = wait(flows, flows.start(request)["id"])
    assert result["id"] == failed["id"] and result["status"] == "succeeded"
    assert [step["status"] for step in result["steps"]] == ["succeeded", "succeeded", "succeeded"]
    children = [runs.get(step["run_id"]) for step in result["steps"]]
    assert children[0]["result"] == "HANDOFF_TOKEN codex initial"
    assert children[1]["cursor_trusted_cwd"] == str(work.resolve())
    assert "received" in children[1]["result"]
    assert len(runs.list()) == 3


@pytest.mark.parametrize("invalid", ["different", "relative", True, 1, None])
def test_invalid_cursor_trust_never_launches_a_step(runtime, invalid):
    flows, runs, work = runtime
    request = body(work, [node("cursor", "审核")])
    request["cursor_trusted_cwd"] = str(work.parent) if invalid == "different" else invalid
    with pytest.raises(ValueError, match="信任"):
        flows.start(request)
    assert not flows.list() and not runs.list()


def test_cancel_reservation_and_no_descendant_launch(runtime):
    flows, runs, work = runtime
    request = body(work, [node("codex", "负责人", prompt="SLOW_NODE"), node("zcode", "下级", "负责人")])
    flow = flows.start(request)
    wait(flows, flow["id"], lambda r: r["steps"][0]["status"] == "running")
    with pytest.raises(ValueError, match="已有任务"):
        flows.start({**request, "request_id": uuid.uuid4().hex})
    with pytest.raises(ValueError, match="已有任务|协作调度"):
        runs.start(dict(request_id=uuid.uuid4().hex, agent="codex", prompt="other", cwd=str(work)))
    flows.cancel(flow["id"])
    result = wait(flows, flow["id"])
    assert result["status"] == "cancelled"
    assert all(step["status"] == "cancelled" for step in result["steps"])
    assert len(runs.list()) == 1 and runs.active is None and runs.owner is None


def test_preflight_all_nodes_before_creating_any_run(runtime):
    flows, runs, work = runtime
    request = body(work)
    request["team"]["nodes"][2]["model"] = "unsupported-override"
    with pytest.raises(ValueError, match="Zcode"):
        flows.start(request)
    assert runs.list() == [] and flows.list() == [] and runs.owner is None


def test_preflight_distinguishes_unbound_and_assembly_only_nodes(runtime):
    flows, runs, work = runtime
    request = body(work, [
        node("codex", "负责人"),
        node("claude", "设计与实现", "负责人"),
        node("", "质量审核", "负责人"),
    ])
    with pytest.raises(ValueError) as caught:
        flows.start(request)
    errors = str(caught.value).splitlines()
    assert len(errors) == 2
    assert "设计与实现" in errors[0] and "claude" in errors[0] and "仅支持装配" in errors[0]
    assert "质量审核" in errors[1] and "未绑定执行客户端" in errors[1]
    assert runs.list() == [] and flows.list() == [] and runs.owner is None


@pytest.mark.parametrize("agent", ["deepseek-harness", "zcode", "cursor"])
def test_missing_completion_is_failure(runtime, agent):
    _, runs, work = runtime
    run = runs.start(dict(request_id=uuid.uuid4().hex, agent=agent, prompt="missing-final", cwd=str(work)))
    assert wait(runs, run["id"])["status"] == "failed"


def test_zcode_long_prompt_stays_on_stdin(runtime):
    _, runs, work = runtime
    prompt = '中文 "quoted" & $() \n' * 1200
    run = runs.start(dict(request_id=uuid.uuid4().hex, agent="zcode", prompt=prompt, cwd=str(work)))
    assert wait(runs, run["id"])["status"] == "succeeded"


def test_zcode_selected_model_reaches_collaboration_child_and_is_cleaned(runtime, tmp_path, monkeypatch):
    flows, runs, work = runtime
    personal = tmp_path / "provider.json"
    write_json(personal, {"schemaVersion": 1, "config": {
        "providerConfigRules": {"providerRules": [
            {"providerId": "fixture", "providerName": "Fixture", "config": {"personalModelIds": ["chosen"]}},
        ]},
        "modelConfigRules": {"providerModelRules": [], "manualProviderModelRules": []},
    }})
    original = personal.read_bytes()
    monkeypatch.setenv("ZCODE_PERSONAL_PROVIDER_CONFIG_FILE", str(personal))
    script = Path(runs.registry.resolve("zcode")[-1])
    script.write_text("""
const fs=require('node:fs');
const config=JSON.parse(fs.readFileSync(process.env.ZCODE_PERSONAL_PROVIDER_CONFIG_FILE,'utf8'));
const selected=config.config.defaultModelSelection;
console.log(JSON.stringify({type:'result',response:selected.providerId+'/'+selected.modelId,projection:{status:'idle'}}));
""", encoding="utf-8")
    request = body(work, [node("zcode", "选择模型")])
    request["team"]["nodes"][0]["model"] = "fixture/chosen"
    result = wait(flows, flows.start(request)["id"])
    assert result["status"] == "succeeded"
    run = runs.get(result["steps"][0]["run_id"])
    assert run["result"] == "fixture/chosen"
    assert personal.read_bytes() == original
    assert not (Path(run["directory"]) / "zcode-model.json").exists()


def test_dsh_patch_is_run_local_and_keeps_approval_denied(tmp_path):
    directory = tmp_path / "run"
    directory.mkdir()
    environment = {}
    cmd = prepare_command(["node", "launcher.js"], dict(
        agent="deepseek-harness", cwd=str(tmp_path), sandbox="read-only", model="example-model",
    ), directory, environment)
    patch = json.loads((directory / "headless.patch.json").read_text(encoding="utf-8"))
    assert cmd[-2:] == ["--json", "-"] and "--patch" in cmd
    assert next(p for p in patch if p["id"] == "sandbox-policy")["config"]["mode"] == "read-only"
    assert next(p for p in patch if p["id"] == "approval")["config"]["policy"] == "never"
    assert environment["DSH_PERMISSION_MODE"] == "read-only"


def test_restart_marks_remaining_steps_interrupted_without_dispatch(runtime):
    flows, runs, _ = runtime
    ident = uuid.uuid4().hex
    write_json(flows.root / ident / "workflow.json", dict(
        id=ident, status="running", title="previous", created_at="", finished_at=None, error="",
        steps=[{"status": "succeeded"}, {"status": "running"}, {"status": "waiting"}],
    ))
    restarted = WorkflowManager(flows.root.parent, runs)
    record = restarted.get(ident)
    assert record["status"] == "interrupted"
    assert [s["status"] for s in record["steps"]] == ["succeeded", "interrupted", "interrupted"]
    assert runs.list() == []
    restarted.close()


def test_workflow_history_pages_past_one_hundred(tmp_path):
    runs = ExecutionManager(tmp_path)
    flows = WorkflowManager(tmp_path, runs)
    for index in range(105):
        ident = f"{index:032x}"
        created = f"2026-05-01T00:{index // 60:02d}:{index % 60:02d}"
        write_json(flows.root / ident / "workflow.json", {
            "id": ident,
            "title": f"collab-{index}",
            "status": "succeeded",
            "created_at": created,
            "finished_at": created,
            "steps": [],
            "error": "",
            "cwd": f"D:\\shared\\job-{index}",
        })
    assert len(flows.list()) == 105
    found = flows.page(query="job-0")
    assert found["pages"] >= 1
    assert [item["title"] for item in found["items"]] == ["collab-0"]
    assert flows.page(size=20)["pages"] == 6
    flows.close()
    runs.close()


def test_zcode_reuses_desktop_data_path_without_copying_credentials(tmp_path, monkeypatch):
    home, desktop = tmp_path / "home", tmp_path / "desktop-data"
    write_json(home / ".zcode/v2/setting.json", {"dataBaseDir": str(desktop)})
    personal = desktop / ".zcode/v2/provider_config.json"
    write_json(personal, {"fixture": "private-provider-configuration"})
    before = personal.read_bytes()
    monkeypatch.setattr(Path, "home", lambda: home)
    environment = {}
    zcode_environment(environment, tmp_path / "resources/glm/zcode.cjs")
    assert environment["ZCODE_DATA_BASE_DIR"] == str(desktop.resolve())
    assert environment["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"] == str(personal)
    assert "private-provider-configuration" not in str(environment)
    assert personal.read_bytes() == before


def test_zcode_explicit_environment_takes_precedence(tmp_path, monkeypatch):
    home, other = tmp_path / "home", tmp_path / "explicit"
    other.mkdir()
    write_json(home / ".zcode/v2/setting.json", {"dataBaseDir": str(tmp_path / "missing")})
    monkeypatch.setattr(Path, "home", lambda: home)
    environment = {"ZCODE_DATA_BASE_DIR": str(other), "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE": "chosen.json"}
    zcode_environment(environment, tmp_path / "resources/glm/zcode.cjs")
    assert environment["ZCODE_DATA_BASE_DIR"] == str(other)
    assert environment["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"] == "chosen.json"
