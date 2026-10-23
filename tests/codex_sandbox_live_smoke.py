"""Opt-in real Codex shell/write checks against the already running packaged app."""

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_workbench import __version__

PROJECT = Path(__file__).resolve().parents[1]
ACTIVE = {"starting", "running", "cancelling"}


def sha256(path):
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save_json(path, value, token):
    text = json.dumps(value, ensure_ascii=False, indent=2)
    path.write_text(text.replace(token, "[REDACTED]") if token else text, encoding="utf-8")


def api(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


def check_idle(client):
    activity = api(client, "GET", "/api/activity")
    if any(activity.get(key, 0) for key in ("runs", "workflows", "conversations")):
        raise RuntimeError("Main workbench is busy; no test task was submitted")
    return activity


def sandbox_log_snapshot(codex_home):
    path = codex_home / ".sandbox" / f"sandbox.{datetime.now():%Y-%m-%d}.log"
    return {"path": str(path), "offset": path.stat().st_size if path.is_file() else 0}


def sandbox_log_delta(snapshot, codex_home):
    # This is a shared diagnostic log, not PID-bound proof of a particular run.
    snapshots = [snapshot]
    current = sandbox_log_snapshot(codex_home)
    if current["path"] != snapshot["path"]:
        snapshots.append({"path": current["path"], "offset": 0})
    evidence = []
    for start in snapshots:
        path = Path(start["path"])
        row = {**start, "lines": [], "scope": "Concurrent supporting evidence; not attributed to a run PID"}
        if path.is_file():
            with path.open("rb") as stream:
                end = stream.seek(0, os.SEEK_END)
                offset = min(start["offset"], end)
                tail_start = max(offset, end - 1_000_000)
                stream.seek(tail_start)
                added = stream.read(end - tail_start).decode("utf-8", errors="replace")
            row.update(end_offset=end, truncated=tail_start > offset, rotated=end < start["offset"])
            row["lines"] = [line for line in added.splitlines()
                            if "MXC availability" in line or "Windows sandbox selection" in line]
        evidence.append(row)
    return evidence


def run_directory(detail, data, run_id):
    directory = Path(detail["directory"])
    expected = data / "runs" / run_id
    if (not directory.is_absolute() or directory.is_symlink() or directory.is_junction()
            or directory.resolve() != expected.resolve()):
        raise RuntimeError("Unexpected run record directory")
    return directory


def capture_codex_process(service, directory):
    result_path = (directory / "result.md").resolve()
    for child in service.children(recursive=True):
        try:
            if child.name().casefold() != "codex.exe":
                continue
            command = child.cmdline()
            if "--output-last-message" not in command:
                continue
            index = command.index("--output-last-message")
            if index + 1 >= len(command) or Path(command[index + 1]).resolve() != result_path:
                continue
            # Only persist argv belonging to this run, never another process.
            return {"pid": child.pid, "exe": child.exe(), "command": command,
                    "source": "psutil: verified service child with exact run result path"}
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return None


def verify_command(evidence, cwd):
    if not evidence:
        raise RuntimeError("Actual Codex child argv was not captured; command evidence is insufficient")
    command = evidence["command"]
    exec_index = command.index("exec")
    override_index = command.index("-c")
    if command[override_index + 1] != "features.prefer_mxc=true" or override_index >= exec_index:
        raise RuntimeError("Actual Codex command did not prefer MXC before exec")
    for flag, value in (("-a", "never"), ("--sandbox", "workspace-write")):
        if command[command.index(flag) + 1] != value:
            raise RuntimeError(f"Actual Codex command has an unexpected {flag} policy")
    if command.index("-a") >= exec_index or Path(command[command.index("--cd") + 1]).resolve() != cwd:
        raise RuntimeError("Actual Codex command has an unexpected approval placement or working directory")
    if {"danger-full-access", "--dangerously-bypass-approvals-and-sandbox", "--full-auto"} & set(command):
        raise RuntimeError("Actual Codex command contains an unexpected permission override")


def smoke_case(client, service, data, output, name, cwd, target, token, cases):
    check_idle(client)
    marker = "AWB_CODEX_" + secrets.token_hex(24).upper()
    run_id = uuid.uuid4().hex
    case_output = output / run_id
    case_output.mkdir()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise RuntimeError("Refusing to overwrite an existing test marker file")
    literal = str(target).replace("'", "''")
    shell_command = (
        f"[System.IO.File]::WriteAllText('{literal}', '{marker}', [System.Text.UTF8Encoding]::new($false)); "
        f"[Console]::WriteLine([System.IO.File]::ReadAllText('{literal}', [System.Text.Encoding]::UTF8))"
    )
    prompt = (
        f"Codex Windows sandbox live verification: {name}\n"
        "Run exactly the following PowerShell command once using the shell tool. "
        "The command writes the test file and reads it back in the same shell invocation. "
        "Only this test file may be written. Do not read other files, use MCP, create subagents, "
        "inspect the workspace, or run any other command. Do not fabricate the read-back result. "
        "After the tool succeeds, reply with only the exact marker actually returned by its stdout. "
        "If the tool fails, report failure.\n\n" + shell_command
    )
    row = {"name": name, "run_id": run_id, "cwd": str(cwd), "marker_file": str(target),
           "expected_marker": marker, "evidence_directory": str(case_output)}
    cases.append(row)
    started = time.monotonic()
    detail = {}
    submitted = False
    try:
        submitted = True
        detail = api(client, "POST", "/api/runs/start", json={
            "request_id": run_id, "agent": "codex", "cwd": str(cwd), "prompt": prompt,
            "sandbox": "workspace-write", "model": "", "timeout_minutes": 3,
        })
        if detail.get("id") != run_id:
            raise RuntimeError("Workbench returned an unexpected run ID")
        directory = run_directory(detail, data, run_id)
        deadline = started + 220
        while True:
            if not row.get("process"):
                row["process"] = capture_codex_process(service, directory)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Owned run exceeded the 220-second outer deadline")
            detail = api(client, "GET", "/api/runs/" + run_id, timeout=min(10, remaining))
            if detail["status"] not in ACTIVE:
                break
            time.sleep(min(0.5, remaining))
        log = detail.get("log", "")
        checks = {
            "succeeded": detail.get("status") == "succeeded",
            "exit_code_zero": detail.get("exit_code") == 0,
            "result_marker": detail.get("result", "").strip() == marker,
            "file_marker": target.is_file() and target.read_bytes() == marker.encode("ascii"),
            "completion_event": "Agent 已返回完成事件" in log,
            "shell_readback": ("WriteAllText" in log and "ReadAllText" in log
                               and marker in (line.strip() for line in log.splitlines())),
        }
        row["checks"] = checks
        verify_command(row.get("process"), cwd)
        if not all(checks.values()):
            raise RuntimeError("Failed live checks: " + ", ".join(key for key, passed in checks.items() if not passed))
        row["passed"] = True
    except (Exception, KeyboardInterrupt) as exc:
        row.update(passed=False, verification_error=f"{type(exc).__name__}: {exc}")
        if submitted and detail.get("status", "starting") in ACTIVE:
            try:
                # A request may have reached the server even if its response was lost.
                api(client, "POST", "/api/runs/cancel", json={"id": run_id})
                row["cancel_requested"] = True
                cancel_deadline = time.monotonic() + 15
                while time.monotonic() < cancel_deadline:
                    detail = api(client, "GET", "/api/runs/" + run_id, timeout=5)
                    if detail["status"] not in ACTIVE:
                        break
                    time.sleep(0.5)
            except Exception as cancel_error:
                row["cancel_error"] = f"{type(cancel_error).__name__}: {cancel_error}"
        raise
    finally:
        row.update(elapsed_seconds=round(time.monotonic() - started, 3),
                   marker_file_sha256=sha256(target))
        row.update({key: detail.get(key) for key in (
            "status", "exit_code", "error", "started_at", "finished_at", "thread_id",
            "result", "log_truncated", "result_truncated",
        )})
        for filename, key in (("execution.log", "log"), ("result.md", "result")):
            text = detail.get(key, "")
            (case_output / filename).write_text(text.replace(token, "[REDACTED]"), encoding="utf-8")
        save_json(case_output / "case.json", row, token)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=PROJECT / "dist/AgentWorkbench/AgentWorkbench.exe")
    parser.add_argument("--output", type=Path,
                        default=PROJECT / "releases" / __version__ / "verification/codex-sandbox-live")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    executable = args.exe.resolve()
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()
    config = codex_home / "config.toml"
    summary = {"started_at": datetime.now().astimezone().isoformat(), "expected_version": __version__,
               "exe": str(executable), "cases": [], "errors": []}
    token = ""
    service = None
    log_start = None
    try:
        if os.name != "nt":
            raise RuntimeError("This opt-in live check requires Windows")
        summary["exe_sha256"] = sha256(executable)
        summary["config_before_sha256"] = sha256(config)
        summary["config_path"] = str(config)
        log_start = sandbox_log_snapshot(codex_home)
        data = Path(os.environ["LOCALAPPDATA"]) / "AgentWorkbench"
        runtime = json.loads((data / "runtime.json").read_text(encoding="utf-8"))
        runtime_token = runtime.get("token")
        if not isinstance(runtime_token, str) or not runtime_token or not 1024 <= int(runtime["port"]) <= 65535:
            raise RuntimeError("Invalid main workbench runtime metadata")
        token = runtime_token
        service = psutil.Process(runtime["pid"])
        if Path(service.exe()).resolve() != executable or not summary["exe_sha256"]:
            raise RuntimeError("Running service does not match the requested packaged executable")
        summary.update(pid=service.pid, process_created_at=service.create_time())
        with httpx.Client(base_url=f"http://127.0.0.1:{int(runtime['port'])}", trust_env=False,
                          timeout=20, headers={"X-Workbench": "1"}) as client:
            api(client, "POST", "/api/auth", json={"token": token})
            health = api(client, "GET", "/api/health")
            if health != {"app": "agent-workbench", "version": __version__}:
                raise RuntimeError("Packaged service health/version does not match the source version")
            summary["health"] = health
            summary["initial_activity"] = check_idle(client)
            if not any(row.get("id") == "codex" and row.get("available")
                       for row in api(client, "GET", "/api/runners")):
                raise RuntimeError("Main workbench cannot resolve a real Codex CLI")
            work = output / "work" / "中文 工作目录"
            work.mkdir(parents=True, exist_ok=True)
            smoke_case(client, service, data, output, "unicode-workspace", work,
                       work / (uuid.uuid4().hex + "-workspace-check.txt"), token, summary["cases"])
            home = Path.home().resolve()
            smoke_case(client, service, data, output, "home-workspace", home,
                       home / ".agent-workbench-tests" / uuid.uuid4().hex / "home-check.txt",
                       token, summary["cases"])
    except (Exception, KeyboardInterrupt) as exc:
        summary["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        try:
            summary["config_after_sha256"] = sha256(config)
            summary["config_unchanged"] = (
                "config_before_sha256" in summary
                and summary["config_before_sha256"] == summary["config_after_sha256"]
            )
            if not summary["config_unchanged"]:
                summary["errors"].append("Codex global config was changed or its initial hash was unavailable")
            if log_start is not None:
                summary["sandbox_log_evidence"] = sandbox_log_delta(log_start, codex_home)
            summary["service_running"] = bool(service and service.is_running())
            if service and not summary["service_running"]:
                summary["errors"].append("Main workbench service is no longer running")
        except Exception as exc:
            summary["errors"].append(f"Evidence collection failed: {type(exc).__name__}: {exc}")
        summary["passed"] = (not summary["errors"] and len(summary["cases"]) == 2
                             and all(row.get("passed") for row in summary["cases"]))
        summary["finished_at"] = datetime.now().astimezone().isoformat()
        save_json(output / "summary.json", summary, token)
        print(f"Live Codex sandbox check {'passed' if summary['passed'] else 'failed'}; "
              f"evidence: {output / 'summary.json'}", flush=True)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
