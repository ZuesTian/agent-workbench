"""Isolated fixture test for cancellation, errors, and downloads in the UI."""

import json
import runpy
import secrets
import sys
import threading
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_workbench.catalog import Catalog
from agent_workbench.server import WorkbenchServer


def main():
    project = Path(__file__).resolve().parents[1]
    root = project / ".test-artifacts" / ("execution-controls-" + uuid.uuid4().hex[:8])
    work = root / "work"
    work.mkdir(parents=True)
    fake = root / "fake_cli.py"
    fake.write_text(runpy.run_path(str(project / "tests/test_execution.py"))["FAKE_CLI"], encoding="utf-8")
    token = secrets.token_urlsafe(24)
    server = WorkbenchServer(
        ("127.0.0.1", 0), Catalog(root / "home", root / "roaming", root / "data"), token
    )
    server.service.runs.registry.resolvers["codex"] = lambda: [sys.executable, str(fake)]
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    errors = []
    summary = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/#token={token}")
            page.locator('.nav-button[data-tab="execution"]').click()
            page.locator("#execution-cwd").fill(str(work))
            page.locator("#execution-prompt").fill("hang")
            page.locator("#execution-start").click()
            page.locator("#run-status").get_by_text("运行中", exact=True).wait_for()
            assert page.locator("#execution-start").is_disabled()
            page.locator("#execution-cancel").click()
            page.locator("#run-status").get_by_text("已取消", exact=True).wait_for()
            summary["cancel_ui"] = "passed with fixture process"
            page.locator("#execution-prompt").fill("fail")
            page.locator("#execution-start").click()
            page.locator("#run-status").get_by_text("执行失败", exact=True).wait_for()
            assert "fixture model unavailable" in page.locator("#run-log").inner_text()
            summary["failure_ui"] = "passed with fixture process"
            prompt = '<script>window.BAD_EXECUTION = true</script> 中文'
            page.locator("#execution-prompt").fill(prompt)
            page.locator("#execution-start").click()
            page.locator("#run-status").get_by_text("已完成", exact=True).wait_for()
            assert not page.evaluate("Boolean(window.BAD_EXECUTION)")
            with page.expect_download() as download:
                page.locator("#execution-download").click()
            target = root / "download.md"
            download.value.save_as(target)
            assert target.read_text(encoding="utf-8") == "真实进程输出：" + prompt
            summary.update(download_matches_result=True, untrusted_text_escaped=True, browser_errors=errors)
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        (root / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
