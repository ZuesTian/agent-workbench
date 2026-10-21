"""Opt-in real Codex/browser smoke. Uses saved auth; never part of pytest."""

import json
import secrets
import sys
import threading
import time
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_workbench.catalog import Catalog
from agent_workbench.server import WorkbenchServer


def main():
    verify_write = "--write" in sys.argv
    project = Path(__file__).resolve().parents[1]
    root = project / ".test-artifacts" / ("execution-live-" + uuid.uuid4().hex[:8])
    work = root / "work"
    work.mkdir(parents=True)
    catalog = Catalog(root / "home", root / "roaming", root / "data")
    token = secrets.token_urlsafe(24)
    server = WorkbenchServer(("127.0.0.1", 0), catalog, token)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    errors = []
    summary = {"root": str(root)}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1080})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/#token={token}")
            page.get_by_role("heading", name="你的 Agent 工作空间").wait_for()
            page.locator('.nav-button[data-tab="execution"]').click()
            page.get_by_role("heading", name="让 Agent 开始工作").wait_for()
            page.locator("#execution-cwd").fill(str(work))
            expected = "AGENT_WORKBENCH_WRITE_OK" if verify_write else "AGENT_WORKBENCH_EXEC_OK"
            if verify_write:
                page.locator("#execution-sandbox").select_option("workspace-write")
                task = (
                    "这是一项工作目录写入测试。只在当前工作目录创建 agent-execution-check.txt，"
                    "文件内容恰好为 ASCII 字符串 AGENT_WORKBENCH_WRITE_OK。"
                    "不要使用 MCP，不要创建子 Agent，不要修改其他文件。"
                    "完成后只回复 AGENT_WORKBENCH_WRITE_OK。"
                )
            else:
                task = (
                    "这是一项接口连通性测试。不要调用任何工具，不要读写文件，不要创建子 Agent。"
                    "只回复以下 ASCII 字符串，不要加其他内容：AGENT_WORKBENCH_EXEC_OK"
                )
            page.locator("#execution-prompt").fill(task)
            page.locator(".execution-options summary").click()
            page.locator('input[name="timeout_minutes"]').fill("2")
            page.locator("#execution-start").click()
            page.locator("#run-status").wait_for(timeout=15000)
            print("Real Codex run submitted through browser", flush=True)
            deadline = time.monotonic() + 150
            last_state = ""
            while time.monotonic() < deadline:
                record = server.service.runs.list()[0]
                if record["status"] != last_state:
                    last_state = record["status"]
                    print("Status:", last_state, flush=True)
                if record["status"] not in {"starting", "running", "cancelling"}:
                    break
                page.wait_for_timeout(500)
            detail = server.service.runs.get(record["id"])
            page.wait_for_timeout(1300)
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(root / "execution-desktop.png"), full_page=True)
            summary.update(
                run_id=record["id"], status=detail["status"],
                exit_code=detail["exit_code"], result=detail["result"].strip(),
                error=detail["error"], browser_errors=errors,
            )
            assert detail["status"] == "succeeded", summary
            assert detail["result"].strip() == expected, summary
            assert page.locator("#run-result").inner_text().strip() == expected
            if verify_write:
                assert (work / "agent-execution-check.txt").read_text(encoding="utf-8-sig").strip() == expected
                summary["workspace_write_verified"] = True
            page.reload()
            page.locator('.nav-button[data-tab="execution"]').click()
            page.locator("#run-result").get_by_text(expected).wait_for()
            summary["reload_preserves_result"] = True
            for width in (1280, 1024, 768, 390):
                page.set_viewport_size({"width": width, "height": 900})
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), width
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(root / "execution-mobile.png"), full_page=True)
            summary["responsive_widths"] = [1440, 1280, 1024, 768, 390]
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        (root / "verification.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
