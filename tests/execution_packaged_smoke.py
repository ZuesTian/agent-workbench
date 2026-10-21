"""Opt-in release smoke against the running main app and saved local accounts."""

import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
import psutil
from playwright.sync_api import sync_playwright


def main():
    release = Path(sys.argv[1]).resolve()
    executable = release / "dist/AgentWorkbench/AgentWorkbench.exe"
    runtime = Path(os.environ["LOCALAPPDATA"]) / "AgentWorkbench/runtime.json"
    info = json.loads(runtime.read_text(encoding="utf-8"))
    process = psutil.Process(info["pid"])
    assert Path(process.exe()).resolve() == executable, "Unexpected running package"
    root = release / "verification"
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)
    summary = {"exe": str(executable), "pid": process.pid}
    base_url = f"http://127.0.0.1:{info['port']}"
    workflow_id = None
    with httpx.Client(base_url=base_url, trust_env=False, timeout=20,
                      headers={"X-Workbench": "1"}) as client:
        client.post("/api/auth", json={"token": info["token"]}).raise_for_status()
        try:
            health = client.get("/api/health").json()
            assert health["version"] == "0.4.1", health
            summary["version"] = health["version"]
            for name in ("execution.js", "workflows.js", "execution.css", "views.js"):
                response = client.get("/" + name)
                response.raise_for_status()
                expected = release / "agent_workbench/static" / name
                assert hashlib.sha256(response.content).digest() == hashlib.sha256(expected.read_bytes()).digest()
            summary["assets_match_source"] = True
            summary["runners"] = {r["id"]: r["available"] for r in client.get("/api/runners").json()}
            assert all(summary["runners"].get(name) for name in ("codex", "deepseek-harness", "zcode")), summary
            parent = uuid.uuid4().hex

            def node(agent, name, prompt, parent_id=None):
                return {
                    "id": uuid.uuid4().hex if parent_id else parent, "parentId": parent_id,
                    "role": "employee" if parent_id else "tutor", "name": name,
                    "agentId": agent, "model": "", "prompt": prompt,
                    "policy": "核对交接标记与实际结果", "skills": [], "mcps": [],
                    "x": 0, "y": 0, "collapsed": False,
                }

            body = {
                "request_id": uuid.uuid4().hex, "cwd": str(work),
                "permission": "read-only", "timeout_minutes": 2,
                "team": {
                    "format": "agent-workbench-team", "version": 1,
                    "name": "0.4.1 打包验收", "layout": "horizontal",
                    "goal": (
                        "这是三客户端交接验收。禁止调用工具、读写文件、创建子 Agent。"
                        "负责人先简短分派；DeepSeek 回复 DSH_PACKAGED_OK；"
                        "Zcode 收到 DSH_PACKAGED_OK 后回复 ZCODE_PACKAGED_OK；"
                        "负责人最后确认两个标记齐全，仅回复 AGENT_WORKBENCH_PACKAGED_OK。"
                    ),
                    "nodes": [
                        node("codex", "负责人", "分派时简短说明分工；审核时按总目标核对并回复。"),
                        node("deepseek-harness", "实现", "仅回复 DSH_PACKAGED_OK。", parent),
                        node("zcode", "核验", "收到 DSH_PACKAGED_OK 后仅回复 ZCODE_PACKAGED_OK。", parent),
                    ],
                },
            }
            response = client.post("/api/workflows/start", json=body)
            response.raise_for_status()
            workflow_id = response.json()["id"]
            summary["workflow_id"] = workflow_id
            print("Packaged app started a real three-client collaboration", flush=True)
            deadline = time.monotonic() + 420
            last = None
            while time.monotonic() < deadline:
                response = client.get("/api/workflows/" + workflow_id)
                response.raise_for_status()
                flow = response.json()
                progress = (flow["status"], tuple(s["status"] for s in flow["steps"]))
                if progress != last:
                    print(json.dumps(progress), flush=True)
                    last = progress
                if flow["status"] not in {"starting", "running", "cancelling"}:
                    break
                time.sleep(0.5)
            summary.update(status=flow["status"], steps=flow["steps"], error=flow["error"])
            assert flow["status"] == "succeeded", summary
            assert "AGENT_WORKBENCH_PACKAGED_OK" in flow["result"], flow["result"]
            assert [s["agent"] for s in flow["steps"]] == ["codex", "deepseek-harness", "zcode", "codex"]
            runs = []
            for step in flow["steps"]:
                run = client.get("/api/runs/" + step["run_id"]).json()
                assert run["status"] == "succeeded" and run["exit_code"] == 0, run
                runs.append({k: run[k] for k in ("id", "agent", "status", "exit_code")})
                runs[-1]["result"] = run["result"].strip()
            assert runs[1]["result"] == "DSH_PACKAGED_OK", runs[1]
            assert runs[2]["result"] == "ZCODE_PACKAGED_OK", runs[2]
            assert runs[3]["result"] == "AGENT_WORKBENCH_PACKAGED_OK", runs[3]
            summary["runs"] = runs
            (root / "packaged-summary.md").write_text(flow["result"], encoding="utf-8")
            errors = []
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel="msedge", headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1050})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base_url + "/#token=" + info["token"])
                page.locator('.nav-button[data-tab="workflows"]').click()
                page.locator("#workflow-status").get_by_text("已完成", exact=True).wait_for()
                assert "AGENT_WORKBENCH_PACKAGED_OK" in page.locator("#workflow-result").inner_text()
                page.screenshot(path=str(root / "packaged-desktop.png"), full_page=True)
                page.reload()
                page.locator('.nav-button[data-tab="workflows"]').click()
                page.locator("#workflow-status").get_by_text("已完成", exact=True).wait_for()
                browser.close()
            assert not errors, errors
            summary.update(browser_errors=errors, reload_history=True, result_handoff=True)
            print("Packaged app, actual clients, handoff and browser all passed", flush=True)
        except BaseException:
            if workflow_id:
                client.post("/api/workflows/cancel", json={"id": workflow_id})
            raise
        finally:
            summary["service_running"] = process.is_running()
            (root / "packaged-smoke.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
