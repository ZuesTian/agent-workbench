import os
import sys
import threading
import time
import uuid
from types import SimpleNamespace

import httpx
import psutil
import pytest

from agent_workbench.catalog import Catalog
from agent_workbench.desktop import DesktopController, EXIT, OPEN, WINDOW_TITLE
from agent_workbench.server import WorkbenchServer


def controller():
    """In-memory window handles ensure unit checks cannot affect user windows."""
    desktop = object.__new__(DesktopController)
    calls = []
    desktop.gui = SimpleNamespace(
        GetForegroundWindow=lambda: 11,
        ShowWindow=lambda hwnd, state: calls.append(("show", hwnd, state)),
        SetForegroundWindow=lambda hwnd: calls.append(("focus", hwnd)),
        PostMessage=lambda hwnd, msg, w, lparam: calls.append(("post", hwnd, msg)),
        IsWindow=lambda hwnd: True,
        error=OSError,
    )
    desktop.con = SimpleNamespace(
        SW_RESTORE=9, SW_MINIMIZE=6, WM_CLOSE=16,
        MB_YESNO=4, MB_ICONWARNING=48, IDYES=6,
    )
    desktop._windows = lambda: [11]
    desktop._is_workbench_window = lambda hwnd: hwnd == 11
    desktop.previous_windows = {11}
    desktop.lock = threading.RLock()
    desktop.closing = lambda: False
    desktop.busy = lambda: False
    desktop.shutdown = lambda: calls.append(("shutdown",))
    desktop.open_window = lambda: calls.append(("open",))
    desktop.available = True
    desktop.last_window = None
    desktop.launch_until = 0
    desktop.hwnd = 20
    desktop.thread = None
    return desktop, calls


def test_minimize_preserves_service_and_restore_reuses_window():
    desktop, calls = controller()
    assert desktop.minimize() == {"minimized": True}
    assert calls == [("show", 11, 6)]
    assert 11 not in desktop.previous_windows
    desktop.open()
    assert calls[-2:] == [("show", 11, 9), ("focus", 11)]
    assert not any(c[0] in ("shutdown", "open") for c in calls)


def test_regular_browser_is_not_minimized_or_closed():
    desktop, calls = controller()
    desktop.gui.GetForegroundWindow = lambda: 99
    desktop._windows = lambda: []
    result = desktop.minimize()
    assert result["minimized"] is False and "浏览器" in result["message"]
    assert calls == []
    desktop._windows = lambda: [11]
    desktop.stop(close_windows=True)
    assert calls == [("post", 11, 16), ("post", 20, 16)]


@pytest.mark.parametrize("title,window_class,process_name,expected", [
    (WINDOW_TITLE, "Chrome_WidgetWin_1", "msedge.exe", True),
    ("工作空间 · Agent 工坊", "Chrome_WidgetWin_1", "msedge.exe", True),
    ("会话 · Agent 工坊", "Chrome_WidgetWin_1", "msedge.exe", True),
    ("会话 · Agent 工坊 - Microsoft Edge", "Chrome_WidgetWin_1", "msedge.exe", False),
    (WINDOW_TITLE + " - Microsoft Edge", "Chrome_WidgetWin_1", "msedge.exe", False),
    (WINDOW_TITLE, "OtherApplication", "msedge.exe", False),
    (WINDOW_TITLE, "Chrome_WidgetWin_1", "unrelated.exe", False),
])
def test_window_identity_excludes_other_apps_and_browser_tabs(
    monkeypatch, title, window_class, process_name, expected,
):
    import agent_workbench.desktop as module

    desktop = object.__new__(DesktopController)
    desktop.gui = SimpleNamespace(
        GetWindowText=lambda hwnd: title,
        GetClassName=lambda hwnd: window_class,
        error=OSError,
    )
    desktop.user = SimpleNamespace(
        GetWindowThreadProcessId=lambda hwnd, pid: setattr(pid._obj, "value", 123),
    )
    monkeypatch.setattr(module.psutil, "Process",
                        lambda pid: SimpleNamespace(name=lambda: process_name))
    assert desktop._is_workbench_window(11) is expected


def test_repeated_open_without_a_window_does_not_spawn_duplicates():
    desktop, calls = controller()
    desktop._windows = lambda: []
    desktop.open()
    desktop.open()
    assert calls == [("open",)]


def test_quitting_blocks_new_window_actions():
    desktop, calls = controller()
    desktop.closing = lambda: True
    with pytest.raises(ValueError, match="退出"):
        desktop.open()
    desktop._command(OPEN)
    desktop._command(EXIT)
    assert calls == []


def test_active_task_exit_can_be_cancelled():
    desktop, calls = controller()
    desktop.busy = lambda: True
    desktop.gui.MessageBox = lambda *args: 7
    desktop._command(EXIT)
    assert calls == []
    desktop.gui.MessageBox = lambda *args: 6
    desktop._command(EXIT)
    assert calls == [("shutdown",)]


@pytest.fixture
def desktop_web(tmp_path):
    catalog = Catalog(tmp_path / "home", tmp_path / "roaming", tmp_path / "data")
    server = WorkbenchServer(("127.0.0.1", 0), catalog, "desktop-test-token")
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}",
            trust_env=False, timeout=5,
        ) as client:
            yield client, server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(3)


def authenticate(client):
    client.post("/api/auth", json={"token": "desktop-test-token"},
                headers={"X-Workbench": "1"}).raise_for_status()


def test_desktop_endpoints_require_session_origin_and_write_header(desktop_web):
    client, server = desktop_web
    desktop, calls = controller()
    server.desktop = desktop
    assert client.get("/api/desktop/status").status_code == 401
    assert client.post("/api/desktop/open", json={}, headers={"X-Workbench": "1"}).status_code == 401
    authenticate(client)
    assert client.get("/api/desktop/status").json()["available"] is True
    assert client.post("/api/desktop/minimize", json={}).status_code == 403
    assert client.post(
        "/api/desktop/minimize", json={},
        headers={"X-Workbench": "1", "Origin": "https://example.com"},
    ).status_code == 403
    assert calls == []
    response = client.post("/api/desktop/minimize", json={}, headers={"X-Workbench": "1"})
    assert response.json() == {"minimized": True}
    client.post("/api/desktop/open", json={}, headers={"X-Workbench": "1"}).raise_for_status()
    assert calls == [("show", 11, 6), ("show", 11, 9), ("focus", 11)]


def test_browser_only_server_reports_unavailable_controls(desktop_web):
    client, _ = desktop_web
    authenticate(client)
    assert client.get("/api/desktop/status").json()["available"] is False
    response = client.post("/api/desktop/minimize", json={}, headers={"X-Workbench": "1"})
    assert response.status_code == 400
    assert "桌面主程序" in response.json()["error"]


def test_launcher_local_ipc_ignores_external_proxy(desktop_web, monkeypatch):
    from agent_workbench.storage import write_json
    from launcher import find_running, open_desktop

    _, server = desktop_web
    desktop, calls = controller()
    server.desktop = desktop
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    info = {"pid": os.getpid(), "port": server.server_port, "token": server.token}
    write_json(server.catalog.data / "runtime.json", info)
    assert find_running(server.catalog.data)["port"] == server.server_port
    open_desktop(info)
    assert calls == [("show", 11, 9), ("focus", 11)]


def test_tray_full_exit_cancels_owned_running_process(desktop_web, tmp_path):
    _, server = desktop_web
    work = tmp_path / "work"
    work.mkdir()
    fixture = tmp_path / "wait.py"
    fixture.write_text(
        "import os,time\nfrom pathlib import Path\n"
        "Path('owned.pid').write_text(str(os.getpid()))\ntime.sleep(120)\n",
        encoding="utf-8",
    )
    server.service.runs.registry.resolvers["codex"] = lambda: [sys.executable, str(fixture)]
    run = server.service.runs.start({
        "request_id": uuid.uuid4().hex, "agent": "codex", "cwd": str(work),
        "sandbox": "read-only", "timeout_minutes": 1, "prompt": "Lifecycle fixture",
    })
    marker = work / "owned.pid"
    deadline = time.monotonic() + 8
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert marker.exists()
    child = psutil.Process(int(marker.read_text()))
    desktop, _ = controller()
    desktop.busy = lambda: True
    desktop.gui.MessageBox = lambda *args: desktop.con.IDYES
    desktop.shutdown = server.request_shutdown
    desktop._command(EXIT)
    assert server.closing.is_set()
    server.server_close()
    child.wait(timeout=5)
    assert not child.is_running()
    assert server.service.runs.get(run["id"])["status"] == "cancelled"


@pytest.mark.skipif(os.name != "nt", reason="Windows notification area")
def test_real_tray_registration_restart_commands_and_cleanup():
    calls = []
    quitting = threading.Event()
    desktop = DesktopController(
        lambda: calls.append("open"), quitting.set, lambda: False, lambda: False,
    )
    try:
        desktop.start()
        assert desktop.available and desktop.gui.IsWindow(desktop.hwnd)
        desktop.gui.PostMessage(desktop.hwnd, desktop.con.WM_COMMAND, OPEN, 0)
        deadline = time.monotonic() + 3
        while not calls and time.monotonic() < deadline:
            time.sleep(0.02)
        assert calls == ["open"]
        # Recreate only this test's icon; Explorer and user icons are untouched.
        desktop.gui.Shell_NotifyIcon(desktop.gui.NIM_DELETE, (desktop.hwnd, 1))
        desktop.available = False
        desktop.gui.PostMessage(desktop.hwnd, desktop.restart_message, 0, 0)
        deadline = time.monotonic() + 3
        while not desktop.available and time.monotonic() < deadline:
            time.sleep(0.02)
        assert desktop.available
        desktop.gui.PostMessage(desktop.hwnd, desktop.con.WM_COMMAND, EXIT, 0)
        assert quitting.wait(3)
    finally:
        desktop.stop()
    assert not desktop.available and not desktop.thread.is_alive()
