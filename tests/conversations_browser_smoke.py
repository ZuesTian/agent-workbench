"""Conversation regressions through HTTP, Edge and owned CLI fixtures; no model calls."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_workbench import __version__
from agent_workbench.catalog import Catalog
from agent_workbench.runners import RunnerRegistry
from agent_workbench.server import WorkbenchServer


CLI_FIXTURE = r"""
const fs = require('node:fs');
const args = process.argv.slice(2);
const value = (name) => args[args.indexOf(name) + 1];
const prompt = args.includes('--prompt') ? value('--prompt') : fs.readFileSync(0, 'utf8');
const kind = args.includes('--workspace') ? 'cursor' : args.includes('--profile') ? 'harness' : args.includes('--workflow-mode') ? 'zcode' : 'codex';
const emit = (record) => console.log(JSON.stringify(record));
if (kind === 'cursor' && prompt.includes('TRUST_NODE') && !args.includes('--trust')) {
  console.error('Workspace Trust Required'); process.exit(1);
}
const reply = 'HANDOFF_TOKEN ' + kind + (prompt.includes('HANDOFF_TOKEN') ? ' received' : ' initial');
const finish = () => {
  if (kind === 'codex') {
    fs.writeFileSync(value('--output-last-message'), reply);
    emit({type:'turn.completed'});
  } else if (kind === 'harness') {
    emit({type:'status',phase:'turn_end',reason:{kind:'completed'}});
    emit({type:'final',text:reply});
  } else if (kind === 'cursor') {
    emit({type:'result',subtype:'success',is_error:false,result:reply});
  } else {
    emit({type:'result',response:reply,projection:{status:'idle'}});
  }
};
// Only the current message can request a delay, not a previously cancelled turn.
const current = prompt.split(/## 用户的新消息\r?\n/).at(-1);
if (current.includes('SLOW_NODE')) setTimeout(finish, 120000);
else finish();
"""


def wait_record(client, path, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(path)
        response.raise_for_status()
        record = response.json()
        if predicate(record):
            return record
        time.sleep(0.1)
    raise AssertionError(record)


def fixture_environment(root):
    (root / "package.json").write_text('{"type":"commonjs"}', encoding="utf-8")
    home, roaming, local = (root / name for name in ("home", "roaming", "local"))
    for path in (home, roaming, local):
        path.mkdir(parents=True)
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for protocol fixtures")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    node_copy = bin_dir / "node.exe"
    shutil.copy2(node, node_copy)
    codex = roaming / "npm/node_modules/@openai/codex/bin/codex.js"
    harness = roaming / "npm/node_modules/@deepseek-ai/dsh/bin.cjs"
    cursor = local / "cursor-agent/versions/2026.10.21-abc/index.js"
    zcode = local / "Programs/ZCode/resources/glm/zcode.cjs"
    for path in (codex, harness, cursor, zcode):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CLI_FIXTURE, encoding="utf-8")
    shutil.copy2(node, cursor.parent / "node.exe")
    (harness.parent / "package.json").write_text(
        '{"bin":{"dsh":"bin.cjs"}}', encoding="utf-8"
    )
    environment = os.environ.copy()
    environment.update(
        USERPROFILE=str(home),
        HOME=str(home),
        APPDATA=str(roaming),
        LOCALAPPDATA=str(local),
        PATH=str(bin_dir)
        + os.pathsep
        + str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"),
        PYTHONUTF8="1",
        ZCODE_DATA_BASE_DIR=str(home),
    )
    commands = {
        key: [str(node_copy), str(path)]
        for key, path in zip(
            ("codex", "deepseek-harness", "cursor", "zcode"),
            (codex, harness, cursor, zcode),
        )
    }
    return home, roaming, local, environment, commands


def exercise(client, base, token, root, agents):
    workspace = root / "workspace"
    workspace.mkdir()
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            sent = []
            page.on(
                "request",
                lambda request: (
                    sent.append(request.post_data_json)
                    if request.url.endswith("/api/conversations/message")
                    and request.method == "POST"
                    else None
                ),
            )
            page.goto(base + "/#token=" + token)
            expect(page.locator(".version")).to_have_text(f"Agent 工坊 · {__version__}")
            page.locator('.nav-button[data-tab="chat"]').click()
            page.locator("[data-chat-new]").click()

            def save_dialog(name):
                page.locator("#chat-name").fill(name)
                page.locator("#chat-workspace").fill(str(workspace))
                for agent in agents:
                    page.locator(f'[data-chat-member][value="{agent}"]').check()
                for agent in ("cursor", "zcode"):
                    expect(page.locator(f'[data-chat-mode="{agent}"]')).to_have_value(
                        "plan"
                    )
                page.locator("[data-chat-save]").click()
                expect(page.locator("#modal")).not_to_be_visible()
                expect(page.locator("#chat-input")).to_be_visible()

            save_dialog("回归会话 A")
            rows = client.get("/api/conversations").json()["items"]
            first = next(row["id"] for row in rows if row["name"] == "回归会话 A")
            page.locator("#chat-input").fill("草稿 A：切页与刷新都要保留")
            page.locator('.nav-button[data-tab="skills"]').click()
            page.locator('.nav-button[data-tab="chat"]').click()
            expect(page.locator("#chat-input")).to_have_value(
                "草稿 A：切页与刷新都要保留"
            )
            page.reload()
            page.locator('.nav-button[data-tab="chat"]').click()
            expect(page.locator("#chat-input")).to_have_value(
                "草稿 A：切页与刷新都要保留"
            )
            page.locator('[data-ctx="new-conversation"]').click()
            save_dialog("回归会话 B")
            expect(page.locator("#chat-input")).to_have_value("")
            page.locator("#chat-input").fill("草稿 B")
            page.locator(f'[data-ctx="conversation"][data-value="{first}"]').click()
            expect(page.locator("#chat-input")).to_have_value(
                "草稿 A：切页与刷新都要保留"
            )

            page.locator("#chat-input").fill("输入法还在选字")
            for event in (
                {"key": "Enter", "isComposing": True},
                {"key": "Enter", "keyCode": 229},
            ):
                page.locator("#chat-input").dispatch_event("keydown", event)
            page.locator("#chat-input").dispatch_event("compositionstart")
            page.locator("#chat-input").dispatch_event("keydown", {"key": "Enter"})
            page.locator("#chat-input").dispatch_event("compositionend")
            page.locator("#chat-input").dispatch_event(
                "keydown", {"key": "Enter", "shiftKey": True}
            )
            page.wait_for_timeout(200)
            assert sent == [], sent

            trust = page.locator("#chat-form [data-cursor-trust-check]")
            expect(trust).not_to_be_checked()
            trust.check()
            page.locator('[data-chat-target="cursor"]').click()
            expect(page.locator("#chat-form [data-cursor-trust]")).to_be_hidden()
            page.locator('[data-chat-target="cursor"]').click()
            expect(trust).not_to_be_checked()

            for number in (1, 2):
                page.locator("#chat-input").fill(f"TRUST_NODE ROUND_{number}")
                trust.check()
                page.locator("#chat-input").press("Enter")
                record = wait_record(
                    client,
                    f"/api/conversations/{first}",
                    lambda r: (
                        not r["running"]
                        and len(r["messages"]) == number * (1 + len(agents))
                    ),
                )
                assert record["status"] == "succeeded", record
                expect(page.locator("#chat-input")).to_be_enabled(timeout=10000)
                expect(page.locator("#chat-input")).to_have_value("")
                expect(trust).not_to_be_checked()
                assert (
                    page.evaluate(
                        '(id) => localStorage.getItem("awb-chat-draft:" + id)', first
                    )
                    is None
                )
                replies = [m for m in record["messages"] if m["role"] == "assistant"]
                assert all(m["status"] == "succeeded" for m in replies), replies
                for index, reply in enumerate(replies):
                    run = client.get("/api/runs/" + reply["run_id"]).json()
                    submitted = (Path(run["directory"]) / "prompt.txt").read_text(
                        encoding="utf-8"
                    )
                    assert (
                        submitted.count(f"ROUND_{1 if index < len(agents) else 2}") == 1
                    )
                    if index:
                        assert replies[index - 1]["text"] in submitted
                    if reply["member"] == "cursor":
                        assert Path(run["cursor_trusted_cwd"]) == workspace

            # Reject a send while a separate run owns the slot; no phantom messages.
            for agent in agents:
                if agent != "codex":
                    page.locator(f'[data-chat-target="{agent}"]').click()
            request = {
                "request_id": uuid.uuid4().hex,
                "agent": "codex",
                "prompt": "SLOW_NODE",
                "cwd": str(workspace),
                "sandbox": "read-only",
                "timeout_minutes": 1,
            }
            manual = client.post("/api/runs/start", json=request)
            manual.raise_for_status()
            before = len(record["messages"])
            page.locator("#chat-input").fill("忙碌时保留的消息")
            page.locator("[data-chat-send]").click()
            expect(page.locator("#chat-input")).to_be_enabled()
            expect(page.locator("#chat-input")).to_have_value("忙碌时保留的消息")
            assert (
                len(client.get(f"/api/conversations/{first}").json()["messages"])
                == before
            )
            client.post(
                "/api/runs/cancel", json={"id": request["request_id"]}
            ).raise_for_status()
            wait_record(
                client,
                "/api/runs/" + request["request_id"],
                lambda r: r["status"] not in {"starting", "running", "cancelling"},
            )
            page.locator("[data-chat-send]").click()
            record = wait_record(
                client,
                f"/api/conversations/{first}",
                lambda r: not r["running"] and len(r["messages"]) == before + 2,
            )
            assert record["status"] == "succeeded"
            expect(page.locator("#chat-input")).to_be_enabled()
            page.locator("#chat-input").fill("SLOW_NODE")
            page.locator("[data-chat-send]").click()
            wait_record(
                client,
                f"/api/conversations/{first}",
                lambda r: any(m.get("status") == "running" for m in r["messages"]),
            )
            page.locator("[data-chat-cancel]").click()
            record = wait_record(
                client, f"/api/conversations/{first}", lambda r: not r["running"]
            )
            assert (
                record["status"] == "cancelled"
                and record["messages"][-1]["status"] == "cancelled"
            )
            expect(page.locator("#chat-input")).to_be_enabled()
            page.locator("#chat-input").fill("取消后可以继续")
            page.locator("[data-chat-send]").click()
            record = wait_record(
                client,
                f"/api/conversations/{first}",
                lambda r: not r["running"] and r["status"] == "succeeded",
            )
            expect(page.locator("#chat-input")).to_be_enabled()

            expect(page.locator("#toasts .toast")).to_have_count(0, timeout=16000)
            widths = []
            for width in (1440, 1749, 1750, 1920, 2292, 2560, 1024, 390):
                page.set_viewport_size({"width": width, "height": 1000})
                assert not page.evaluate(
                    "document.documentElement.scrollWidth > innerWidth"
                )
                expect(page.locator("[data-chat-send]")).to_be_visible()
                if width >= 1749:
                    main = page.locator("#main-content").bounding_box()
                    context = page.locator("#context-column").bounding_box()
                    assert main["y"] < 160 and main["width"] >= min(width - 600, 1400), main
                    assert main["x"] >= context["x"] + context["width"], (main, context)
                    page.locator("#composer-trigger").click()
                    expect(page.locator(".app-shell")).to_have_class("app-shell with-composer")
                    main = page.locator("#main-content").bounding_box()
                    assert main["y"] < 160 and main["width"] >= min(width - 1000, 1400), main
                    page.screenshot(path=str(root / f"chat-composer-{width}.png"))
                    page.locator("#composer-trigger").click()
                widths.append(width)
                page.screenshot(path=str(root / f"chat-{width}.png"), full_page=True)
            assert errors == [], errors
            return {
                "agents_executed": agents,
                "two_round_handoff": "passed",
                "mode_defaults": "passed",
                "busy_rejection": "passed",
                "cancel_and_next_turn": "passed",
                "cursor_trust_scope": "passed",
                "ime_enter": "passed",
                "draft_switch_reload_and_success_clear": "passed",
                "viewport_widths": widths,
                "wide_chat_and_composer_stay_in_first_row": "passed",
                "browser_errors": errors,
            }
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    root = Path(
        tempfile.mkdtemp(
            prefix=f"conversations-v{__version__}-", dir=project / ".test-artifacts"
        )
    )
    home, roaming, local, environment, commands = fixture_environment(root)
    server = thread = process = log = None
    mode = "packaged" if args.exe else "source"
    try:
        if args.exe:
            # Codex/Harness/Cursor resolve only fixture paths. Zcode's registry
            # resolver can discover a real install, so never execute it here.
            agents = ["codex", "deepseek-harness", "cursor"]
            log = (root / "service.log").open("wb")
            process = subprocess.Popen(
                [str(args.exe.resolve()), "--serve"],
                cwd=project,
                env=environment,
                stdout=log,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            runtime = local / "AgentWorkbench/runtime.json"
            deadline = time.monotonic() + 40
            while not runtime.exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Fixture service exited early")
                time.sleep(0.1)
            info = json.loads(runtime.read_text(encoding="utf-8"))
            assert info["pid"] == process.pid
        else:
            agents = ["codex", "deepseek-harness", "zcode", "cursor"]
            server = WorkbenchServer(
                ("127.0.0.1", 0),
                Catalog(home, roaming, local / "AgentWorkbench", system_scan=False),
                "conversation-fixture",
            )
            server.service.runs.registry = RunnerRegistry(
                lambda: commands["codex"],
                {
                    key: (lambda command=command: command)
                    for key, command in commands.items()
                },
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            info = {"port": server.server_port, "token": server.token}
        base = f"http://127.0.0.1:{info['port']}"
        with httpx.Client(
            base_url=base, trust_env=False, timeout=30, headers={"X-Workbench": "1"}
        ) as client:
            client.post("/api/auth", json={"token": info["token"]}).raise_for_status()
            assert client.get("/api/health").json()["version"] == __version__
            for runner in client.get("/api/runners").json():
                if runner["id"] in agents:
                    assert str(root) in runner["path"], runner
            try:
                result = exercise(client, base, info["token"], root, agents)
            finally:
                if process:
                    client.post("/api/shutdown", json={}).raise_for_status()
        if process:
            assert process.wait(timeout=20) == 0
        result.update(
            mode=mode, version=__version__, fixture=str(root), live_model_calls=0
        )
        (root / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False))
    finally:
        if server:
            server.shutdown()
            server.server_close()
            thread.join(3)
        if process and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if log:
            log.close()


if __name__ == "__main__":
    main()
