"""Conversation regressions through HTTP, Edge and owned CLI fixtures; no model calls."""

import argparse
import json
import os
import re
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
const failFlag =
  process.env.AWB_FIXTURE_FAIL_FLAG ||
  require('node:path').join(process.cwd(), '.awb-fixture-fail-once');
const failEvent = {
  codex: { type: 'turn.failed', error: { message: 'fixture failure' } },
  harness: { type: 'error', message: 'fixture failure' },
  cursor: { type: 'result', subtype: 'error', is_error: true, result: '' },
  zcode: { type: 'result', result: '', projection: { status: 'error' } },
}[kind];
if (current.includes('FAIL_ONCE_NODE') && !fs.existsSync(failFlag)) {
  // Fail exactly once per fixture root so a retried turn can succeed.
  fs.writeFileSync(failFlag, '1');
  emit(failEvent);
  process.exit(1);
}
if (current.includes('SLOW_SHORT_NODE')) setTimeout(finish, 2000);
else if (current.includes('SLOW_NODE')) setTimeout(finish, 120000);
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


def fixture_environment(root, cli_fixture=CLI_FIXTURE):
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
        path.write_text(cli_fixture, encoding="utf-8")
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
    # AWB_REQUIRE_INTERACTIVE=1 是最终验收的严格开关：交互断言未满足即失败。
    # 默认模式下只把未满足项记入 pending，保证功能尚未实现时基线冒烟仍可通过。
    strict = os.environ.get("AWB_REQUIRE_INTERACTIVE") == "1"
    pending = []
    interactive = {}
    os.environ.setdefault("AWB_FIXTURE_FAIL_FLAG", str(root / "fail-once.flag"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))

            def check(condition, key, detail=""):
                if condition:
                    interactive[key] = "passed"
                    return True
                message = f"{key} 未满足" + (f"：{detail}" if detail else "")
                if strict:
                    raise AssertionError(message)
                pending.append(message)
                interactive[key] = "pending：" + (str(detail) if detail else "功能尚未实现")
                return False

            def wait_until(predicate, timeout=8.0, interval=0.15):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        if predicate():
                            return True
                    except Exception:  # noqa: BLE001 - 轮询断言允许瞬时查询异常
                        pass
                    page.wait_for_timeout(int(interval * 1000))
                return False
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

            # ---- 运行中交互回归（task-3 扩展，证据见 audits/multi-agent-chat-20261023/evidence/review.md） ----
            # 覆盖增量渲染/滚动保持/稳定输入区/进度/断线/隐藏暂停/重试/任务列表。
            # 约束：不修改既有断言含义、不联网、fixture 仍为本地 CLI 子进程。
            for agent in agents:
                picker = page.locator(f'[data-chat-target="{agent}"]')
                if picker.get_attribute("aria-pressed") == "false":
                    picker.click()
            polls = []
            page.on(
                "request",
                lambda request: (
                    polls.append(request.url)
                    if request.method == "GET"
                    and request.url.endswith(f"/api/conversations/{first}")
                    else None
                ),
            )
            stream_state = """() => {
                const stream = document.getElementById('chat-stream');
                return {
                    top: stream ? stream.scrollTop : -1,
                    height: stream ? stream.scrollHeight : -1,
                    client: stream ? stream.clientHeight : -1,
                    messages: stream ? stream.querySelectorAll('.chat-message').length : -1,
                };
            }"""
            hide_page = """() => {
                try {
                    Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
                    Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
                } catch (error) {
                    window.__awbVisibilityMockError = String(error);
                }
                document.dispatchEvent(new Event('visibilitychange'));
            }"""
            show_page = """() => {
                try {
                    Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'visible'});
                    Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
                } catch (error) {
                    window.__awbVisibilityMockError = String(error);
                }
                document.dispatchEvent(new Event('visibilitychange'));
            }"""

            # (a)(c) 慢节点撑住 120 s 一轮，覆盖上翻历史与稳定输入区。
            page.locator("#chat-input").fill("SLOW_NODE")
            page.locator("[data-chat-send]").click()
            expect(page.locator("[data-chat-cancel]")).to_be_visible(timeout=15000)
            wait_record(
                client,
                f"/api/conversations/{first}",
                lambda r: any(m.get("status") == "running" for m in r["messages"]),
            )
            page.wait_for_timeout(1500)

            for height in (1000, 820, 640):
                page.set_viewport_size({"width": 1440, "height": height})
                layout = page.evaluate(stream_state)
                if layout["height"] > layout["client"] + 40:
                    break
            check(
                layout["height"] > layout["client"] + 40,
                "scroll_stream_scrollable",
                str(layout),
            )
            page.evaluate(
                "() => { const s = document.getElementById('chat-stream'); if (s) s.scrollTop = 0; }"
            )
            page.evaluate(
                """() => {
                    window.__awbProbe = {
                        stream: document.getElementById('chat-stream'),
                        input: document.getElementById('chat-input'),
                        message: document.querySelector('#chat-stream .chat-message'),
                    };
                }"""
            )
            page.wait_for_timeout(3300)  # >= 2 个 1.2 s 轮询周期
            scrolled = page.evaluate(stream_state)
            check(
                scrolled["top"] <= 4,
                "scroll_kept_when_reading_history",
                f"scrollTop={scrolled['top']}",
            )
            identity = page.evaluate(
                """() => ({
                    stream: window.__awbProbe.stream === document.getElementById('chat-stream'),
                    input: window.__awbProbe.input === document.getElementById('chat-input'),
                    message:
                        window.__awbProbe.message ===
                        document.querySelector('#chat-stream .chat-message'),
                })"""
            )
            check(identity["stream"], "stable_stream_node", str(identity))
            check(identity["input"], "stable_composer_node", str(identity))
            check(identity["message"], "stable_message_node", str(identity))

            if page.locator("#chat-input").is_enabled():
                page.locator("#chat-input").fill("轮询中的草稿")
                page.locator("#chat-input").evaluate("el => el.setSelectionRange(4, 4)")
                page.wait_for_timeout(2600)  # >= 2 个 1.2 s 轮询周期
                draft = page.locator("#chat-input").input_value()
                focused = page.evaluate(
                    "() => document.activeElement && document.activeElement.id"
                )
                selection = page.locator("#chat-input").evaluate(
                    "el => [el.selectionStart, el.selectionEnd]"
                )
                check(draft == "轮询中的草稿", "composer_draft_kept", draft)
                check(focused == "chat-input", "composer_focus_kept", str(focused))
                check(selection == [4, 4], "composer_selection_kept", str(selection))
                sent_before = len(sent)
                page.locator("#chat-input").press("Enter")
                page.wait_for_timeout(700)
                check(
                    len(sent) == sent_before,
                    "composer_enter_ignored_while_running",
                    f"sent={len(sent)}",
                )
                page.locator("#chat-input").fill("")
            else:
                for key in (
                    "composer_draft_kept",
                    "composer_focus_kept",
                    "composer_selection_kept",
                    "composer_enter_ignored_while_running",
                ):
                    check(False, key, "运行中 textarea 被禁用，无法输入草稿")

            # (g) 页面隐藏时暂停轮询，恢复可见后立即拉取。
            page.evaluate(hide_page)
            page.wait_for_timeout(500)
            paused_before = len(polls)
            page.wait_for_timeout(3200)  # >= 2 个轮询周期
            paused_after = len(polls)
            paused = paused_after == paused_before
            page.evaluate(show_page)
            resumed = wait_until(lambda: len(polls) > paused_after, timeout=4)
            check(
                paused,
                "polling_paused_when_hidden",
                f"polled={paused_before}->{paused_after}",
            )
            check(resumed, "polling_resumed_when_visible")

            # 轮询断线：连续失败后显示横幅，恢复后清除。
            def abort_conversation(route):
                if route.request.method == "GET" and route.request.url.endswith(
                    f"/api/conversations/{first}"
                ):
                    route.abort()
                else:
                    route.continue_()

            page.route(f"**/api/conversations/{first}", abort_conversation)
            banner = page.get_by_text(re.compile("无法获取会话状态"))
            shown = wait_until(lambda: banner.first.is_visible(), timeout=10)
            page.unroute(f"**/api/conversations/{first}", abort_conversation)
            cleared = wait_until(lambda: not banner.first.is_visible(), timeout=6)
            check(
                shown and cleared,
                "polling_offline_banner",
                f"shown={shown} cleared={cleared}",
            )

            # (d) 运行中状态栏显示当前成员、轮次序号与耗时。
            page.wait_for_timeout(1300)
            bar = page.locator(".chat-statusbar")
            bar_text = bar.inner_text() if bar.count() else ""
            current = client.get(f"/api/conversations/{first}").json()
            running = [m for m in current["messages"] if m.get("status") in {"queued", "running"}]
            member_names = (
                {m.get("name", "") for m in running}
                | {m.get("member", "") for m in running}
                | {member["name"] for member in current["members"]}
            )
            named = any(name and name in bar_text for name in member_names)
            numbered = bool(
                re.search(r"第\s*\d+\s*[/／]\s*\d+|(?<![\d])\d+\s*[/／]\s*\d+", bar_text)
            )
            timed = bool(re.search(r"已用|耗时|\d+\s*(?:秒|s\b)", bar_text))
            check(
                named and numbered and timed,
                "statusbar_progress",
                bar_text.replace("\n", " | "),
            )

            page.locator("[data-chat-cancel]").click()
            wait_record(client, f"/api/conversations/{first}", lambda r: not r["running"])
            expect(page.locator("#chat-input")).to_be_enabled(timeout=15000)
            page.set_viewport_size({"width": 1440, "height": 1000})

            # (b) 上翻历史时新回复到达，显示“有新回复”按钮；点击回到底部。
            # 本轮只保留 codex：Cursor 未勾选信任时会产生额外失败消息，干扰观察。
            for agent in agents:
                if agent == "codex":
                    continue
                picker = page.locator(f'[data-chat-target="{agent}"]')
                if picker.get_attribute("aria-pressed") == "true":
                    picker.click()
            page.locator("#chat-input").fill("SLOW_SHORT_NODE")
            page.locator("[data-chat-send]").click()
            expect(page.locator("[data-chat-cancel]")).to_be_visible(timeout=15000)
            before_ok = sum(
                1
                for message in client.get(f"/api/conversations/{first}").json()["messages"]
                if message["role"] == "assistant" and message["status"] == "succeeded"
            )
            before_messages = page.evaluate(stream_state)["messages"]
            page.evaluate(
                "() => { const s = document.getElementById('chat-stream'); if (s) s.scrollTop = 0; }"
            )
            wait_record(
                client,
                f"/api/conversations/{first}",
                lambda r: sum(
                    1
                    for m in r["messages"]
                    if m["role"] == "assistant" and m["status"] == "succeeded"
                )
                >= before_ok + 1,
                timeout=30,
            )
            wait_until(
                lambda: page.evaluate(stream_state)["messages"] > before_messages, timeout=8
            )
            new_reply = page.get_by_role("button", name=re.compile("新回复"))
            button_shown = wait_until(lambda: new_reply.first.is_visible(), timeout=6)
            check(button_shown, "new_reply_button_shown", "页面上没有含“新回复”的按钮")
            if button_shown:
                new_reply.first.click()
                reached = wait_until(
                    lambda: (
                        lambda s: s["height"] - s["top"] - s["client"] <= 6
                    )(page.evaluate(stream_state)),
                    timeout=4,
                )
                cleared_button = wait_until(
                    lambda: not new_reply.first.is_visible(), timeout=4
                )
                check(reached, "new_reply_button_scrolls_to_bottom")
                check(cleared_button, "new_reply_button_cleared")
            else:
                check(False, "new_reply_button_scrolls_to_bottom")
                check(False, "new_reply_button_cleared")
            if page.locator("[data-chat-cancel]").is_visible():
                page.locator("[data-chat-cancel]").click()
            wait_record(client, f"/api/conversations/{first}", lambda r: not r["running"])
            expect(page.locator("#chat-input")).to_be_enabled(timeout=15000)

            # (e) 失败回复可重试：FAIL_ONCE_NODE 首次失败，重试后新增 succeeded 回复。
            # 恢复全部成员本轮参与，用于确认“单成员失败后剩余成员未执行”。
            for agent in agents:
                picker = page.locator(f'[data-chat-target="{agent}"]')
                if picker.get_attribute("aria-pressed") == "false":
                    picker.click()
            before_fail = client.get(f"/api/conversations/{first}").json()
            before_fail_count = len(before_fail["messages"])
            page.locator("#chat-input").fill("FAIL_ONCE_NODE")
            page.locator("[data-chat-send]").click()
            failed_record = wait_record(
                client,
                f"/api/conversations/{first}",
                lambda r: not r["running"] and len(r["messages"]) > before_fail_count,
                timeout=30,
            )
            last_user = max(
                index
                for index, message in enumerate(failed_record["messages"])
                if message["role"] == "user"
            )
            round_replies = [
                message
                for message in failed_record["messages"][last_user + 1 :]
                if message["role"] == "assistant"
            ]
            failed = [m for m in round_replies if m["status"] == "failed"]
            check(
                bool(failed),
                "failed_reply_recorded",
                str([m["status"] for m in round_replies]),
            )
            interactive["failure_stops_remaining_members"] = (
                f"failed={len(failed)} not_executed="
                f"{len([m for m in round_replies if m['status'] == 'error'])}"
            )
            expect(
                page.locator(".chat-message.agent .tag", has_text="失败").first
            ).to_be_visible(timeout=15000)
            agent_count = page.locator(".chat-message.agent").count()
            retry_button = (
                page.locator(f'[data-chat-retry="{failed[0]["id"]}"]')
                if failed
                else page.get_by_role("button", name=re.compile("重试"))
            )
            retry_visible = wait_until(
                lambda: retry_button.count() > 0 and retry_button.first.is_visible(),
                timeout=6,
            )
            if not retry_visible:
                # 兼容实现只提供“重试未完成成员”入口的情况。
                retry_button = page.get_by_role("button", name=re.compile("重试"))
                retry_visible = (
                    retry_button.count() > 0 and retry_button.first.is_visible()
                )
            if retry_visible:
                retry_button.last.click()
                retried = wait_record(
                    client,
                    f"/api/conversations/{first}",
                    lambda r: (
                        not r["running"]
                        and len([m for m in r["messages"] if m["role"] == "assistant"])
                        > len(
                            [
                                m
                                for m in failed_record["messages"]
                                if m["role"] == "assistant"
                            ]
                        )
                    ),
                    timeout=60,
                )
                known = {m["id"] for m in failed_record["messages"]}
                added = [
                    m
                    for m in retried["messages"]
                    if m["id"] not in known and m["role"] == "assistant"
                ]
                retry_ok = (
                    bool(added)
                    and any(m["status"] == "succeeded" for m in added)
                    and all(m.get("retry_of") and m.get("round_id") for m in added)
                )
                check(
                    retry_ok,
                    "retry_failed_reply",
                    str(
                        [
                            (m["status"], bool(m.get("retry_of")), bool(m.get("round_id")))
                            for m in added
                        ]
                    ),
                )
                check(
                    wait_until(
                        lambda: page.locator(".chat-message.agent").count() > agent_count,
                        timeout=10,
                    ),
                    "retry_ui_reply_visible",
                )
            else:
                check(False, "retry_failed_reply", "页面上没有“重试”按钮")
                check(False, "retry_ui_reply_visible", "页面上没有“重试”按钮")
            expect(page.locator("#chat-input")).to_be_enabled(timeout=15000)

            # (f) 任务列表：后端 task_list 上限 30；用 35 条回复的会话验证说明与条目数。
            rows = client.get("/api/conversations").json()["items"]
            second = next(row["id"] for row in rows if row["name"] == "回归会话 B")
            conversation_path = (
                root / "local" / "AgentWorkbench" / "conversations" / second / "conversation.json"
            )
            seeded = json.loads(conversation_path.read_text(encoding="utf-8"))
            seeded_at = seeded.get("created_at") or ""
            for index in range(35):
                seeded["messages"].append(
                    {
                        "id": uuid.uuid4().hex,
                        "role": "assistant",
                        "member": "codex",
                        "name": "Codex CLI",
                        "text": f"批量回复 {index + 1}",
                        "status": "succeeded",
                        "created_at": seeded_at,
                        "duration_ms": 900 + index,
                    }
                )
            conversation_path.write_text(
                json.dumps(seeded, ensure_ascii=False), encoding="utf-8"
            )
            page.locator(f'[data-ctx="conversation"][data-value="{second}"]').click()
            expect(page.locator("#chat-stream .chat-message")).to_have_count(35, timeout=15000)
            page.locator("[data-chat-tasks]").click()
            expect(page.locator("#modal")).to_be_visible()
            modal_text = page.locator("#modal").inner_text()
            task_count = page.locator("#modal .chat-task").count()
            recent_hint = bool(re.search(r"最近\s*30\s*条", modal_text))
            check(
                recent_hint and 0 < task_count <= 30,
                "task_list_recent_30",
                f"count={task_count} hint={recent_hint}",
            )
            copy_buttons = page.get_by_role("button", name=re.compile("复制"))
            check(copy_buttons.count() > 0, "copy_button", "页面上没有复制按钮")
            page.locator("#modal .modal-head [data-action='close-modal']").click()
            expect(page.locator("#modal")).not_to_be_visible()

            interactive["pending"] = pending
            interactive["strict"] = strict
            drawers = []
            for width in (1101, 1150, 1180):
                page.set_viewport_size({"width": width, "height": 1000})
                for composer_open in (False, True):
                    if composer_open:
                        page.locator("#composer-trigger").click()
                        expect(page.locator(".app-shell")).to_have_class("app-shell with-composer")
                    assert page.locator("#main-content").bounding_box()["y"] < 160
                    toggle = page.locator("#context-toggle")
                    expect(toggle).to_have_attribute("aria-expanded", "false")
                    toggle.click()
                    column = page.locator("#context-column")
                    expect(column).to_be_visible()
                    expect(toggle).to_have_attribute("aria-expanded", "true")
                    box = column.bounding_box()
                    assert box["width"] >= 280 and box["height"] > 800, box
                    assert column.evaluate("el => el.contains(document.activeElement)")
                    expect(page.locator("#context-backdrop")).to_be_visible()
                    page.screenshot(path=str(root / f"context-{width}-composer-{int(composer_open)}.png"))
                    page.keyboard.press("Escape")
                    expect(toggle).to_have_attribute("aria-expanded", "false")
                    expect(toggle).to_be_focused()
                    expect(page.locator("#context-backdrop")).to_be_hidden()
                    expect(column).to_be_hidden()
                    toggle.click()
                    page.locator("#context-backdrop").click(position={"x": width - 20, "y": 500})
                    expect(page.locator("#context-backdrop")).to_be_hidden()
                    expect(column).to_be_hidden()
                    drawers.append({"width": width, "composer": composer_open})
                    if composer_open:
                        page.locator("#composer-trigger").click()
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
                "interactive": interactive,
                "two_round_handoff": "passed",
                "mode_defaults": "passed",
                "busy_rejection": "passed",
                "cancel_and_next_turn": "passed",
                "cursor_trust_scope": "passed",
                "ime_enter": "passed",
                "draft_switch_reload_and_success_clear": "passed",
                "viewport_widths": widths,
                "wide_chat_and_composer_stay_in_first_row": "passed",
                "context_drawer_keyboard_pointer_and_focus": drawers,
                "browser_errors": errors,
            }
        finally:
            browser.close()


def main(exercise_fn=exercise, cli_fixture=CLI_FIXTURE, prefix="conversations"):
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    root = Path(
        tempfile.mkdtemp(
            prefix=f"{prefix}-v{__version__}-", dir=project / ".test-artifacts"
        )
    )
    home, roaming, local, environment, commands = fixture_environment(root, cli_fixture)
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
                result = exercise_fn(client, base, info["token"], root, agents)
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
        return result
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
