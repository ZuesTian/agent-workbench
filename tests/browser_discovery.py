"""Exercise discovery UI and Zcode apply/restore only in the isolated fixture."""

import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


root = Path(__file__).resolve().parents[1]
runtime = json.loads(
    (root / ".test-artifacts/ui-runtime.json").read_text(encoding="utf-8")
)
fixture = Path(runtime["root"])
original = (fixture / "home/.zcode/cli/config.json").read_bytes()
errors = []

with sync_playwright() as browser_api:
    browser = browser_api.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"http://127.0.0.1:{runtime['port']}/#token=ui-fixture-session")
    expect(page.get_by_role("heading", name="你的 Agent 工作空间")).to_be_visible()
    expect(page.locator("#agent-cards > .agent-grid .agent-card")).to_have_count(4)
    expect(
        page.get_by_role("heading", name="DeepSeek Harness", exact=True)
    ).to_be_visible()
    expect(page.get_by_role("heading", name="Zcode", exact=True)).to_be_visible()
    expect(
        page.get_by_role("heading", name="Claude Code", exact=True)
    ).not_to_be_visible()
    page.locator("#config-only-list summary").click()
    expect(page.get_by_role("heading", name="Claude Code", exact=True)).to_be_visible()
    page.locator("#card-target-claude").check()
    assert page.locator("#config-only-list").evaluate("(e) => e.open")
    page.locator("#card-target-claude").uncheck()
    page.locator('[data-agent-detail="deepseek-harness"]').click()
    expect(page.locator("#modal")).to_contain_text("DeepSeek Harness.exe")
    expect(page.locator("#modal")).to_contain_text("仅盘点")
    page.get_by_role("button", name="关闭", exact=True).click()
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(
        path=str(root / ".test-artifacts/discovery-v031-desktop.png"), full_page=True
    )

    # The new nested config target must work through the existing preview and backup UI.
    page.locator('.nav-button[data-tab="mcps"]').click()
    page.get_by_role("button", name="选择 lab-tools", exact=True).click()
    page.locator("#compose-target-zcode").check()
    page.get_by_role("button", name="预览变更", exact=True).click()
    expect(page.get_by_role("heading", name="检查这次装配")).to_be_visible()
    expect(page.locator(".plan-row")).to_have_count(1)
    page.get_by_role("button", name="应用并备份", exact=True).click()
    expect(page.locator("#modal")).not_to_be_visible()
    result = json.loads(
        (fixture / "home/.zcode/cli/config.json").read_text(encoding="utf-8")
    )
    assert (
        "lab-tools" in result["mcp"]["servers"]
        and result["provider"]["id"] == "fixture"
    )
    page.locator('.nav-button[data-tab="history"]').click()
    page.get_by_role("button", name="恢复变更前的内容", exact=True).click()
    page.get_by_role("button", name="检查并恢复", exact=True).click()
    expect(page.get_by_text("已恢复", exact=True)).to_be_visible()
    assert (fixture / "home/.zcode/cli/config.json").read_bytes() == original
    page.get_by_role("button", name="清空组合", exact=True).click()
    page.get_by_role("button", name="收起装配台", exact=True).click()

    # Adding a portable location and removing the executable must update discovery.
    extra = fixture / "other-drive/custom"
    extra.mkdir(parents=True)
    program = extra / "OpenCode.exe"
    program.write_bytes(b"fixture-never-execute")
    page.locator('.nav-button[data-tab="overview"]').click()
    page.get_by_role("button", name="添加程序目录", exact=True).click()
    page.locator("#install-root-path").fill(str(extra))
    page.get_by_role("button", name="添加并扫描", exact=True).click()
    expect(page.locator("#agent-cards > .agent-grid .agent-card")).to_have_count(5)
    program.unlink()
    page.get_by_role("button", name="重新扫描本机资源", exact=True).click()
    expect(page.locator("#agent-cards > .agent-grid .agent-card")).to_have_count(4)
    expect(page.get_by_role("heading", name="OpenCode", exact=True)).to_have_count(0)
    for width in (1280, 1024, 768, 390):
        page.set_viewport_size({"width": width, "height": 1000})
        assert not page.evaluate(
            "document.documentElement.scrollWidth > innerWidth"
        ), width
    expect(page.locator("#toasts > *")).to_have_count(0, timeout=10000)
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(
        path=str(root / ".test-artifacts/discovery-v031-mobile.png"), full_page=True
    )
    page.locator('.nav-button[data-tab="settings"]').click()
    expect(page.get_by_role("heading", name="程序查找目录", exact=True)).to_be_visible()
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    assert errors == [], errors
    browser.close()

print(
    json.dumps(
        {
            "ui": "passed",
            "zcode_apply_restore": "passed",
            "portable_rescan": "passed",
            "leftovers_collapsed": "passed",
            "responsive": "passed",
            "errors": errors,
        }
    )
)
