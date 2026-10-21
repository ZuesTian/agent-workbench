"""Browser checks with an intentionally paused scan; never execute an Agent."""

import json
import sys
import threading
import time
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_workbench.catalog import Catalog
from agent_workbench.server import WorkbenchServer


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def exercise(browser, root, cached):
    folder = root / ("warm" if cached else "cold")
    home, roaming, data = (folder / name for name in ("home", "roaming", "data"))
    old_skill = home / ".cursor/skills/old/SKILL.md"
    write(old_skill, "---\nname: old-skill\ndescription: before\n---\nOld.")
    write(home / ".cursor/mcp.json", '{"mcpServers":{}}')
    if cached:
        Catalog(home, roaming, data).scan()
    old_skill.unlink()
    write(
        home / ".cursor/skills/new/SKILL.md",
        "---\nname: refreshed-skill\ndescription: after\n---\nCurrent.",
    )
    catalog = Catalog(home, roaming, data)
    entered, release = threading.Event(), threading.Event()
    original = catalog._scan

    def paused():
        entered.set()
        if not release.wait(30):
            raise TimeoutError("Browser did not release scan")
        return original()

    catalog._scan = paused
    server = WorkbenchServer(("127.0.0.1", 0), catalog, "scan-browser-fixture")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    errors = []
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        started = time.perf_counter()
        page.goto(f"http://127.0.0.1:{server.server_port}/#token=scan-browser-fixture")
        expect(page.get_by_role("heading", name="你的 Agent 工作空间")).to_be_visible()
        elapsed = time.perf_counter() - started
        assert entered.wait(2)
        expect(page.locator("#scan-status")).to_be_visible()
        expect(page.get_by_role("button", name="重新扫描本机资源")).to_be_disabled()
        assert "1970" not in page.locator("#main-content").inner_text()
        if cached:
            expect(page.locator("#scan-status")).to_contain_text("上次扫描")
            page.locator('.nav-button[data-tab="skills"]').click()
            expect(
                page.get_by_role("button", name="old-skill", exact=True)
            ).to_be_visible()
        else:
            expect(page.locator("#scan-status")).to_contain_text("正在发现")
        page.locator('.nav-button[data-tab="execution"]').click()
        prompt = page.locator("#execution-prompt")
        prompt.fill("保留这个尚未提交的任务")
        prompt.focus()
        prompt.evaluate("(element) => { element.dataset.keep = 'same-element'; }")
        release.set()
        expect(page.locator("#scan-status")).to_be_hidden(timeout=10000)
        expect(prompt).to_have_value("保留这个尚未提交的任务")
        assert prompt.get_attribute("data-keep") == "same-element"
        expect(prompt).to_be_focused()
        page.locator('.nav-button[data-tab="skills"]').click()
        expect(
            page.get_by_role("button", name="refreshed-skill", exact=True)
        ).to_be_visible()
        expect(page.get_by_role("button", name="old-skill", exact=True)).to_have_count(
            0
        )
        entered.clear()
        release.clear()
        page.get_by_role("button", name="重新扫描本机资源", exact=True).click()
        assert entered.wait(2)
        expect(page.locator("#scan-status")).to_be_visible()
        expect(page.get_by_role("button", name="重新扫描本机资源")).to_be_disabled()
        expect(page.locator(".busy-overlay")).to_have_count(0)
        page.locator('.nav-button[data-tab="execution"]').click()
        page.locator("#execution-prompt").fill("手动扫描期间也能编辑")
        release.set()
        expect(page.locator("#scan-status")).to_be_hidden(timeout=10000)
        expect(page.locator("#execution-prompt")).to_have_value("手动扫描期间也能编辑")
        page.locator('.nav-button[data-tab="skills"]').click()
        for width in (1440, 768, 390):
            page.set_viewport_size({"width": width, "height": 1000})
            assert not page.evaluate(
                "document.documentElement.scrollWidth > innerWidth"
            )
        page.screenshot(path=str(folder / "after-refresh-mobile.png"), full_page=True)
        assert errors == [], errors
        assert not server.service.runs.list()
        return {
            "cached": cached,
            "shell_seconds": round(elapsed, 3),
            "form_preserved": True,
            "inventory_updated": True,
            "manual_scan_nonblocking": True,
            "errors": errors,
        }
    finally:
        release.set()
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(3)


def main():
    root = (
        Path(__file__).resolve().parents[1]
        / ".test-artifacts"
        / ("scan-browser-" + uuid.uuid4().hex[:8])
    )
    root.mkdir(parents=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            results = [exercise(browser, root, cached) for cached in (True, False)]
        finally:
            browser.close()
    (root / "result.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({"results": results, "artifacts": str(root)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
