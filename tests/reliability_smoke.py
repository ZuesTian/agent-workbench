"""Isolated source/packaged startup and browser assembly smoke; no model calls."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import psutil
from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from agent_workbench import __version__

    # The desktop mutex is global. Never interfere with a user's running service.
    if any(
        p.info["name"].lower() == "agentworkbench.exe"
        for p in psutil.process_iter(["name"])
        if p.info["name"]
    ):
        raise RuntimeError(
            "Close the existing AgentWorkbench before this isolated smoke"
        )
    artifacts = root / ".test-artifacts"
    artifacts.mkdir(exist_ok=True)
    mode = "packaged" if args.exe else "source"
    fixture = Path(tempfile.mkdtemp(prefix=f"v{__version__}-{mode}-", dir=artifacts))
    home, roaming, local = fixture / "home", fixture / "roaming", fixture / "local"
    data = local / "AgentWorkbench"
    for directory in (home, roaming, data):
        directory.mkdir(parents=True)
    primary = home / ".cline/data/settings/cline_mcp_settings.json"
    extra = (
        roaming
        / "Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json"
    )
    for path in (primary, extra):
        path.parent.mkdir(parents=True)
        path.write_text('{"mcpServers":{},"preserve":"fixture"}\n', encoding="utf-8")
    source = home / ".cursor/mcp.json"
    source.parent.mkdir()
    source.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fixture-tools": {
                        "command": "fixture-never-execute",
                        "env": {"TOKEN": "fixture-only"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    original = {path: path.read_bytes() for path in (primary, extra, source)}
    environment = os.environ.copy()
    environment.update(
        USERPROFILE=str(home),
        HOME=str(home),
        APPDATA=str(roaming),
        LOCALAPPDATA=str(local),
        PYTHONUTF8="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    command = (
        [str(args.exe.resolve()), "--serve"]
        if args.exe
        else [sys.executable, str(root / "launcher.py"), "--serve"]
    )
    client = None
    result = {
        "version": __version__,
        "mode": mode,
        "fixture": str(fixture),
        "live_model_calls": "not run",
    }
    with (fixture / "server.log").open("wb") as log:
        process = subprocess.Popen(
            command,
            cwd=root,
            env=environment,
            stdout=log,
            stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            runtime = data / "runtime.json"
            deadline = time.monotonic() + 40
            while not runtime.exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"service exited with {process.returncode}; see {fixture / 'server.log'}"
                    )
                time.sleep(0.1)
            info = json.loads(runtime.read_text(encoding="utf-8"))
            assert info["pid"] == process.pid
            base = f"http://127.0.0.1:{info['port']}"
            client = httpx.Client(
                base_url=base, trust_env=False, timeout=40, headers={"X-Workbench": "1"}
            )
            client.post("/api/auth", json={"token": info["token"]}).raise_for_status()
            health = client.get("/api/health").json()
            assert health["version"] == __version__, health
            desktop = client.get("/api/desktop/status").json()
            assert desktop["available"] is True
            result.update(startup="passed", tray_available=True)
            errors = []
            with sync_playwright() as api:
                browser = api.chromium.launch(channel="msedge", headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/#token=" + info["token"])
                expect(
                    page.get_by_role("heading", name="你的 Agent 工作空间")
                ).to_be_visible(timeout=45000)
                page.locator('[data-ctx="agent"][data-value="cline"]').click(timeout=45000)
                page.locator('[data-assemble="cline"]').click()
                page.locator('.nav-button[data-tab="mcps"]').click()
                page.get_by_role("button", name="选择 fixture-tools", exact=True).click(
                    timeout=45000
                )
                page.get_by_role("button", name="预览变更", exact=True).click()
                selector = page.locator('[data-config-target="cline"]')
                expect(selector).to_be_visible()
                expect(selector.locator("option")).to_have_count(3)
                expect(
                    page.get_by_role("button", name="应用并备份", exact=True)
                ).to_be_disabled()
                selector.select_option(str(primary))
                page.get_by_role("button", name="重新预览", exact=True).click()
                expect(page.locator(".plan-row")).to_contain_text(str(primary))
                expect(
                    page.get_by_role("button", name="应用并备份", exact=True)
                ).to_be_enabled()
                selector.select_option(str(extra))
                expect(
                    page.get_by_role("button", name="应用并备份", exact=True)
                ).to_be_disabled()
                page.get_by_role("button", name="重新预览", exact=True).click()
                expect(page.locator(".plan-row")).to_contain_text(str(extra))
                page.screenshot(
                    path=str(fixture / "choice-desktop.png"), full_page=True
                )
                page.set_viewport_size({"width": 390, "height": 844})
                page.get_by_role("button", name="重新预览", exact=True).click(trial=True)
                assert not page.evaluate(
                    "document.documentElement.scrollWidth > innerWidth"
                )
                assert not page.locator(".modal-body").evaluate(
                    "el => el.scrollWidth > el.clientWidth + 1"
                )
                page.screenshot(path=str(fixture / "choice-mobile.png"), full_page=True)
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.get_by_role("button", name="应用并备份", exact=True).click()
                expect(page.locator("#modal")).not_to_be_visible(timeout=20000)
                assert primary.read_bytes() == original[primary]
                assert (
                    "fixture-tools"
                    in json.loads(extra.read_text(encoding="utf-8"))["mcpServers"]
                )
                page.locator('.nav-button[data-tab="history"]').click()
                page.get_by_role("button", name="恢复变更前的内容", exact=True).click()
                page.get_by_role("button", name="检查并恢复", exact=True).click()
                expect(page.get_by_text("已恢复", exact=True)).to_be_visible()
                assert {path: path.read_bytes() for path in original} == original
                page.reload()
                expect(
                    page.get_by_role("heading", name="你的 Agent 工作空间")
                ).to_be_visible()
                page.get_by_role("button", name="预览变更", exact=True).click()
                expect(selector).to_have_value(str(extra))
                expect(page.locator(".plan-row")).to_contain_text(str(extra))
                assert errors == [], errors
                browser.close()
            records = client.get("/api/history").json()
            assert len(records) == 1 and records[0]["status"] == "restored"
            assert records[0]["entries"][0]["target"] == str(extra)
            assert not list((data / "runs").glob("*/run.json"))
            result.update(
                browser_choice_apply_restore="passed",
                draft_reload="passed",
                responsive_widths=[1440, 390],
                browser_errors=errors,
            )
        finally:
            if client:
                try:
                    client.post("/api/shutdown", json={}).raise_for_status()
                finally:
                    client.close()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()  # This script owns this exact isolated child.
                process.wait(timeout=5)
                raise
    assert process.returncode == 0
    assert not (data / "runtime.json").exists()
    result["shutdown"] = "passed"
    (fixture / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
