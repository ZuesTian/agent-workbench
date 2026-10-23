"""Opt-in browser smoke; --live calls all three saved local client accounts."""

import json
import runpy
import secrets
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_workbench.catalog import Catalog
from agent_workbench.runners import RunnerRegistry
from agent_workbench.server import WorkbenchServer


def main():
    live = "--live" in sys.argv
    project = Path(__file__).resolve().parents[1]
    fixtures = runpy.run_path(str(project / "tests/test_scheduling.py"))
    root = project / ".test-artifacts" / (("collaboration-live-" if live else "collaboration-ui-") + uuid.uuid4().hex[:8])
    work = root / "work"
    work.mkdir(parents=True)
    request = fixtures["body"](work)
    if live:
        request["team"]["goal"] = (
            "这是跨客户端交接测试。所有步骤禁止使用工具、读写文件、创建子 Agent。"
            "负责人先分派；DeepSeek 收到分派后回复 DSH_HANDOFF_OK；"
            "Zcode 确认交接中有 DSH_HANDOFF_OK 后回复 ZCODE_HANDOFF_OK；"
            "负责人最后核对两个标记齐全，仅回复 AGENT_WORKBENCH_COLLAB_OK。"
        )
        request["team"]["nodes"][0]["prompt"] = "分派阶段用一两句话说明分工。审核阶段检查交接标记并按总目标返回结果。"
        request["team"]["nodes"][1]["prompt"] = "仅回复 DSH_HANDOFF_OK，不调用工具。"
        request["team"]["nodes"][2]["prompt"] = "若收到 DSH_HANDOFF_OK 则仅回复 ZCODE_HANDOFF_OK，不调用工具。"
    token = secrets.token_urlsafe(24)
    server = WorkbenchServer(("127.0.0.1", 0), Catalog(root / "home", root / "roaming", root / "data"), token)
    if not live:
        fake = root / "fixture.py"
        fake.write_text(fixtures["FAKE"], encoding="utf-8")
        js = root / "fixture.cjs"
        js.write_text(fixtures["FAKE_ZCODE"], encoding="utf-8")
        def command():
            return [sys.executable, str(fake)]
        server.service.runs.registry = RunnerRegistry(command, {
            "deepseek-harness": command, "zcode": lambda: [shutil.which("node"), str(js)],
        })
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    summary = {"live": live, "root": str(root)}
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1050})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script(
                "localStorage.setItem('awb-team-draft-v1', " + json.dumps(json.dumps({
                    "team": request["team"], "dirty": True,
                })) + ");localStorage.setItem('awb-workflow-settings', " + json.dumps(json.dumps({
                    "cwd": str(work), "permission": "read-only", "timeout_minutes": 2,
                })) + ");"
            )
            page.goto(f"http://127.0.0.1:{server.server_port}/#token={token}")
            page.locator('.nav-button[data-tab="execution"]').click()
            page.locator("#execution-agent option[value=zcode]").wait_for(state="attached")
            page.locator("#execution-agent").select_option("zcode")
            assert page.locator("#execution-sandbox").input_value() == "plan"
            page.locator('select[name="model"]').wait_for(state="attached")
            assert page.locator('select[name="model"]').evaluate("el => el.tagName") == "SELECT"
            page.locator('.nav-button[data-tab="teams"]').click()
            page.locator('.team-goal [data-tab="workflows"]').click()
            page.locator("#workflow-start").click()
            print("Submitted", "live" if live else "fixture", "collaboration through browser", flush=True)
            deadline = time.monotonic() + (300 if live else 35)
            record = None
            last = None
            while time.monotonic() < deadline:
                records = server.service.workflows.list()
                if records:
                    record = server.service.workflows.get(records[0]["id"])
                    progress = (record["status"], tuple(s["status"] for s in record["steps"]))
                    if progress != last:
                        print(progress, flush=True)
                        last = progress
                    if record["status"] not in {"starting", "running", "cancelling"}:
                        break
                page.wait_for_timeout(400)
            assert record and record["status"] == "succeeded", record
            summary.update(id=record["id"], status=record["status"], steps=record["steps"])
            page.locator("#workflow-status").get_by_text("执行已完成", exact=True).wait_for()
            expected = "AGENT_WORKBENCH_COLLAB_OK" if live else "HANDOFF_TOKEN"
            assert expected in record["result"]
            with page.expect_download() as download:
                page.locator("#workflow-download").click()
            download.value.save_as(root / "summary-download.md")
            assert (root / "summary-download.md").read_bytes().decode("utf-8") == record["result"]
            page.screenshot(path=str(root / "collaboration-desktop.png"), full_page=True)
            for width in (1280, 1024, 768, 390):
                page.set_viewport_size({"width": width, "height": 900})
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), width
            page.screenshot(path=str(root / "collaboration-mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 1050})
            page.locator("[data-workflow-run]").nth(1).click()
            page.locator("#run-result").wait_for()
            assert page.locator("#run-result").inner_text()
            page.reload()
            page.locator('.nav-button[data-tab="workflows"]').click()
            page.locator("#workflow-status").get_by_text("执行已完成", exact=True).wait_for()
            summary.update(result_handoff=True, download_exact=True, reload_history=True,
                           responsive_widths=[1440, 1280, 1024, 768, 390], browser_errors=errors)
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5)
        (root / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
