import json
import queue
import subprocess
import sys
import threading
import time
import uuid
from types import SimpleNamespace

import httpx
import psutil
import pytest

from agent_workbench.catalog import Catalog
from agent_workbench.execution import ExecutionManager, ACTIVE, read_stream_records, OutputTooLarge
from agent_workbench.runners import decode_event
from agent_workbench.server import WorkbenchServer
from agent_workbench.storage import write_json
import agent_workbench.execution as execution


FAKE_CLI = r'''
import json, pathlib, subprocess, sys, time
prompt = sys.stdin.read()
out = pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1])
def emit(event):
    print(json.dumps(event), flush=True)
emit({"type":"thread.started","thread_id":"fixture-session"})
emit({"type":"turn.started"})
if prompt == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    (out.parent / "child.json").write_text(json.dumps({"pid":child.pid}))
    emit({"type":"item.started","item":{"type":"command_execution","command":"fixture waiting"}})
    time.sleep(120)
elif prompt == "fail":
    emit({"type":"turn.failed","error":{"message":"fixture model unavailable"}})
    sys.exit(7)
elif prompt == "empty":
    sys.exit(0)
elif prompt == "item-error":
    emit({"type":"item.completed","item":{"type":"error","message":"tool failed once"}})
    sys.stdout.buffer.write(b'{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}')
    out.write_text("finished-after-item-error", encoding="utf-8")
elif prompt == "fatal":
    emit({"type":"turn.failed","error":{"message":"model failed"}})
    out.write_text("should-not-count", encoding="utf-8")
elif prompt == "wide":
    pad = "x" * 1_100_000
    sys.stdout.buffer.write(json.dumps({"type":"turn.completed","usage":{"pad":pad}}).encode("utf-8"))
    out.write_text("wide-ok", encoding="utf-8")
else:
    result = "真实进程输出：" + prompt
    out.write_text(result, encoding="utf-8")
    emit({"type":"item.completed","item":{"type":"agent_message","text":result}})
    emit({"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":5}})
'''


def wait_for(manager, run_id, predicate=lambda r: r["status"] not in ACTIVE):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        record = manager.get(run_id)
        if predicate(record):
            return record
        time.sleep(0.05)
    raise AssertionError(manager.get(run_id))


@pytest.fixture
def runtime(tmp_path):
    script = tmp_path / "fake_cli.py"
    script.write_text(FAKE_CLI, encoding="utf-8")
    work = tmp_path / "中文 工作目录"
    work.mkdir()
    manager = ExecutionManager(tmp_path / "data", lambda: [sys.executable, str(script)])
    yield manager, work
    manager.close()


def request(work, prompt="test"):
    return dict(
        request_id=uuid.uuid4().hex, agent="codex", cwd=str(work),
        prompt=prompt, sandbox="read-only", timeout_minutes=1,
    )


def test_real_subprocess_output_saved_and_request_retry_is_idempotent(runtime):
    manager, work = runtime
    body = request(work, '中文 "quoted" $() & text')
    run = manager.start(body)
    repeated = manager.start(body)
    assert repeated["id"] == run["id"]
    result = wait_for(manager, run["id"])
    assert result["status"] == "succeeded" and result["exit_code"] == 0
    assert result["result"] == "真实进程输出：" + body["prompt"]
    assert result["thread_id"] == "fixture-session"
    assert result["usage"]["output_tokens"] == 5
    assert "Agent 已返回完成事件" in result["log"]
    assert len(manager.list()) == 1
    assert (manager.root / run["id"] / "prompt.txt").read_text(encoding="utf-8") == body["prompt"]
    with pytest.raises(ValueError, match="不同任务"):
        manager.start({**body, "prompt": "different"})


@pytest.mark.parametrize("prompt,code", [("fail", 7), ("empty", 0)])
def test_success_requires_completion_event_not_just_exit_code(runtime, prompt, code):
    manager, work = runtime
    run = manager.start(request(work, prompt))
    result = wait_for(manager, run["id"])
    assert result["status"] == "failed" and result["exit_code"] == code
    assert result["error"]


def test_cancel_kills_child_process_and_rejects_concurrent_launch(runtime):
    manager, work = runtime
    run = manager.start(request(work, "hang"))
    marker = manager.root / run["id"] / "child.json"
    wait_for(manager, run["id"], lambda _: marker.exists())
    child = psutil.Process(json.loads(marker.read_text())["pid"])
    with pytest.raises(ValueError, match="已有任务"):
        manager.start(request(work))
    assert manager.cancel(run["id"])["status"] == "cancelling"
    assert wait_for(manager, run["id"])["status"] == "cancelled"
    child.wait(timeout=5)
    assert not child.is_running()


def test_shutdown_cancels_owned_run(runtime):
    manager, work = runtime
    run = manager.start(request(work, "hang"))
    wait_for(manager, run["id"], lambda r: r["status"] == "running")
    manager.close()
    assert manager.get(run["id"])["status"] == "cancelled"
    with pytest.raises(ValueError, match="正在退出"):
        manager.start(request(work))


def test_worker_setup_failure_releases_slot_and_allows_next_run(runtime, monkeypatch):
    manager, work = runtime
    path = manager._path
    failed = False

    def fail_once(run_id):
        nonlocal failed
        if threading.current_thread() is manager.worker and not failed:
            failed = True
            raise ValueError("injected redirected path")
        return path(run_id)

    monkeypatch.setattr(manager, "_path", fail_once)
    run = manager.start(request(work))
    manager.worker.join(timeout=5)
    assert manager.active is None
    assert manager.get(run["id"])["status"] == "failed"
    resumed = manager.start(request(work, "after setup failure"))
    assert wait_for(manager, resumed["id"])["status"] == "succeeded"


def test_final_persistence_failure_does_not_leave_a_running_ghost(runtime, monkeypatch):
    manager, work = runtime
    update = manager._update

    def fail_terminal(run_id, **changes):
        if changes.get("status") in {"succeeded", "failed"}:
            raise OSError("injected final disk failure")
        update(run_id, **changes)

    monkeypatch.setattr(manager, "_update", fail_terminal)
    run = manager.start(request(work))
    manager.worker.join(timeout=10)
    assert not manager.worker.is_alive() and manager.active is None
    assert manager.get(run["id"])["status"] == "interrupted"
    assert manager.page()["active"] == 0
    monkeypatch.setattr(manager, "_update", update)
    resumed = manager.start(request(work, "after persistence failure"))
    assert wait_for(manager, resumed["id"])["status"] == "succeeded"


def test_cancel_signals_process_even_when_record_cannot_be_read(runtime, monkeypatch):
    manager, work = runtime
    run = manager.start(request(work, "hang"))
    wait_for(manager, run["id"], lambda r: r["status"] == "running")
    read = manager._record

    def unreadable(_run_id):
        raise ValueError("injected unreadable record")

    with monkeypatch.context() as patch:
        patch.setattr(manager, "_record", unreadable)
        with pytest.raises(ValueError, match="unreadable"):
            manager.cancel(run["id"])
        assert manager.cancel_requested.is_set()
    manager.worker.join(timeout=10)
    assert manager.active is None and read(run["id"])["status"] == "cancelled"


def test_execution_thread_start_failure_releases_slot(runtime, monkeypatch):
    manager, work = runtime

    def fail_start(_thread):
        raise RuntimeError("injected thread start failure")

    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, "start", fail_start)
        with pytest.raises(RuntimeError, match="thread start"):
            manager.start(request(work))
    assert manager.active is None and manager.worker is None
    assert manager.list()[0]["status"] == "failed"
    resumed = manager.start(request(work, "after thread failure"))
    assert wait_for(manager, resumed["id"])["status"] == "succeeded"


def test_finishing_worker_cannot_clear_next_runs_ownership(runtime, monkeypatch):
    manager, work = runtime
    execute = manager._run
    first_finished, release_first = threading.Event(), threading.Event()

    def delay_return(record, prefix):
        execute(record, prefix)
        if record["prompt"] == "first":
            first_finished.set()
            assert release_first.wait(10)

    monkeypatch.setattr(manager, "_run", delay_return)
    manager.start(request(work, "first"))
    first_worker = manager.worker
    try:
        assert first_finished.wait(10)
        second = manager.start(request(work, "hang"))
        wait_for(manager, second["id"], lambda r: r["status"] == "running")
    finally:
        release_first.set()
        first_worker.join(10)
    assert manager.active == second["id"]
    manager.cancel(second["id"])
    assert wait_for(manager, second["id"])["status"] == "cancelled"


def test_deadline_terminates_task_and_preserves_timeout_status(runtime, monkeypatch):
    manager, work = runtime
    real_clock = time.monotonic
    monkeypatch.setattr(execution, "time", SimpleNamespace(monotonic=lambda: real_clock() * 1000))
    run = manager.start(request(work, "hang"))
    result = wait_for(manager, run["id"])
    assert result["status"] == "timed_out"
    assert "超过设定时间" in result["error"]


def test_restart_records_interruption_without_relaunch(tmp_path):
    run_id = uuid.uuid4().hex
    write_json(tmp_path / "runs" / run_id / "run.json", {
        "id": run_id, "status": "running", "created_at": "", "prompt": "never rerun",
    })
    manager = ExecutionManager(tmp_path, lambda: None)
    assert manager.get(run_id)["status"] == "interrupted"
    assert manager.active is None
    manager.close()


def test_invalid_inputs_do_not_spawn_or_create_records(runtime):
    manager, work = runtime
    for changes in (
        {"cwd": "relative"}, {"agent": "unknown"}, {"sandbox": "danger-full-access"},
        {"timeout_minutes": True}, {"request_id": "../../escape"}, {"prompt": ""},
    ):
        with pytest.raises(ValueError):
            manager.start({**request(work), **changes})
    assert not manager.list()


def test_system_proxy_is_inherited_without_overwriting_explicit_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://explicit.invalid:1234")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.setattr("agent_workbench.runners.urllib.request.getproxies", lambda: {
        "http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890",
    })
    env = execution.execution_environment()
    assert env["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert env["HTTPS_PROXY"] == "http://explicit.invalid:1234"
    assert "CODEX_THREAD_ID" not in env


def test_http_execution_routes_reuse_auth_and_do_not_need_catalog_lock(runtime, tmp_path):
    manager, work = runtime
    catalog = Catalog(tmp_path / "home", tmp_path / "roaming", tmp_path / "other-data")
    server = WorkbenchServer(("127.0.0.1", 0), catalog, "execution-test-token")
    server.service.runs.close()
    server.service.runs = manager
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False, timeout=5,
        ) as client:
            assert client.get("/api/runners").status_code == 401
            assert client.get("/api/runners/codex/models").status_code == 401
            assert client.post("/api/runs/start", json=request(work)).status_code == 403
            headers = {"X-Workbench": "1"}
            assert client.post(
                "/api/auth", json={"token": server.token}, headers=headers,
            ).status_code == 200
            with catalog.lock:
                assert client.get("/api/runners").json()[0]["available"]
                assert client.get("/api/runners/codex/models").json()["agent"] == "codex"
                response = client.post("/api/runs/start", json=request(work), headers=headers)
                assert response.status_code == 200
                run = wait_for(manager, response.json()["id"])
                detail = client.get("/api/runs/" + run["id"]).json()
                assert detail["status"] == "succeeded"
            assert client.get("/api/runs").json()["total"] >= 1
            assert client.get("/execution.js").status_code == 200
            assert client.get("/execution.css").status_code == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_execution_history_can_search_past_the_first_hundred(tmp_path):
    manager = ExecutionManager(tmp_path)
    oldest = f"{0:032x}"
    for index in range(110):
        run_id = f"{index:032x}"
        created = f"2026-04-01T00:{index // 60:02d}:{index % 60:02d}"
        folder = manager.root / run_id
        write_json(folder / "run.json", {
            "id": run_id,
            "title": f"archive-{index}",
            "cwd": f"D:\\projects\\dir-{index:03d}",
            "agent": "codex",
            "sandbox": "read-only",
            "model": "",
            "timeout_minutes": 30,
            "cursor_trusted_cwd": "",
            "created_at": created,
            "started_at": created,
            "finished_at": created,
            "status": "succeeded",
            "exit_code": 0,
            "error": "",
            "trust_required": False,
            "thread_id": None,
            "usage": None,
            "workflow_id": "a" * 32 if index == 3 else None,
        })
    assert len(manager.list()) == 110
    first = manager.page(size=20)
    assert first["total"] == 110 and first["pages"] == 6
    assert all(item["title"] != "archive-0" for item in first["items"])
    found = manager.page(query="archive-0")
    assert [item["id"] for item in found["items"]] == [oldest]
    assert manager.page(query="dir-007")["items"][0]["title"] == "archive-7"
    assert manager.page(query="2026-04-01")["total"] == 110
    steps = manager.page(kind="step")
    assert [item["title"] for item in steps["items"]] == ["archive-3"]
    assert manager.page(kind="task")["total"] == 109
    assert manager.get(oldest)["title"] == "archive-0"
    manager.close()


def test_stream_records_keep_split_lines_eof_chunks_and_reject_oversize():
    class Chunks:
        def __init__(self, parts):
            self.parts = list(parts)
        def read(self, _size):
            return self.parts.pop(0) if self.parts else b""

    pieces = Chunks([b'{"type":"tu', b'rn.completed"}\n{"type":"item.error"}'])
    assert list(read_stream_records(pieces)) == [
        b'{"type":"turn.completed"}\n',
        b'{"type":"item.error"}',
    ]
    wide = json.dumps({"type": "turn.completed", "usage": {"pad": "x" * 1_100_000}}).encode()
    assert len(wide) > 1_000_000
    assert list(read_stream_records(Chunks([wide]))) == [wide]
    with pytest.raises(OutputTooLarge, match="8 MB"):
        list(read_stream_records(Chunks([b"y" * (8 * 1024 * 1024 + 1)])))


def test_stream_progress_arrives_while_real_child_waits_for_acknowledgement():
    child = subprocess.Popen([
        sys.executable, "-u", "-c",
        "import sys; sys.stdout.buffer.write(b'progress\\n'); sys.stdout.flush(); sys.stdin.buffer.readline(); sys.stdout.buffer.write(b'done\\n'); sys.stdout.flush()",
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    received = queue.Queue()
    reader = threading.Thread(target=lambda: [received.put(line) for line in read_stream_records(child.stdout)], daemon=True)
    reader.start()
    try:
        # The child cannot exit or produce more data until the first event is
        # consumed. A buffered read(65536) deadlocks this handshake.
        assert received.get(timeout=5) == b"progress\n"
        assert child.poll() is None
        child.stdin.write(b"ack\n")
        child.stdin.flush()
        assert received.get(timeout=5) == b"done\n"
        assert child.wait(timeout=5) == 0
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        reader.join(timeout=5)
        child.stdin.close()
        child.stdout.close()
        child.stderr.close()


def test_item_error_does_not_fail_a_completed_turn_but_turn_failed_does():
    item = decode_event("codex", {"type": "item.completed", "item": {"type": "error", "message": "tool failed once"}})
    done = decode_event("codex", {"type": "turn.completed", "usage": {}})
    fatal = decode_event("codex", {"type": "turn.failed", "error": {"message": "model failed"}})
    assert "failed" not in item and done.get("completed") is True and fatal.get("failed") is True


def test_nonfatal_item_error_and_long_final_event_still_succeed(runtime):
    manager, work = runtime
    item = manager.start(request(work, "item-error"))
    item_result = wait_for(manager, item["id"])
    assert item_result["status"] == "succeeded"
    assert item_result["result"] == "finished-after-item-error"
    assert "tool failed once" in item_result["log"]
    wide = manager.start(request(work, "wide"))
    wide_result = wait_for(manager, wide["id"])
    assert wide_result["status"] == "succeeded" and wide_result["result"] == "wide-ok"
    fatal = manager.start(request(work, "fatal"))
    fatal_result = wait_for(manager, fatal["id"])
    assert fatal_result["status"] == "failed"


def test_restart_removes_leftover_zcode_model_file(tmp_path):
    data = tmp_path / "data"
    run_id = f"{7:032x}"
    folder = data / "runs" / run_id
    folder.mkdir(parents=True)
    (folder / "zcode-model.json").write_text('{"config":{"apiKey":"VERY_SECRET"}}', encoding="utf-8")
    write_json(folder / "run.json", {
        "id": run_id, "status": "running", "created_at": "2026-10-21T00:00:00",
        "title": "left", "cwd": "D:\\work", "agent": "zcode",
    })
    manager = ExecutionManager(data, lambda: None)
    assert not (folder / "zcode-model.json").exists()
    assert manager.get(run_id)["status"] == "interrupted"
    assert "VERY_SECRET" not in manager.get(run_id)["log"]
    manager.close()
