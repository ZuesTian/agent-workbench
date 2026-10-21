"""Desktop entry and single-instance loopback server; tasks start only by request."""

import argparse
import json
import logging
import os
import secrets
import subprocess
import sys
import time
import urllib.request
import webbrowser
from contextlib import contextmanager
from pathlib import Path

from agent_workbench.storage import write_json
from agent_workbench import __version__
from agent_workbench.server import WorkbenchServer


def runtime_root():
    return (
        Path(os.getenv("LOCALAPPDATA", str(Path.home() / ".local/share")))
        / "AgentWorkbench"
    )


@contextmanager
def startup_lock(name="Local\\AgentWorkbench.Startup"):
    """Serialize desktop launches, including launches from different shortcuts."""
    if os.name != "nt":
        yield
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise OSError("无法建立启动锁")
    acquired = False
    try:
        result = kernel.WaitForSingleObject(handle, 30000)
        if result not in (0, 0x80):
            raise RuntimeError("另一个管理器入口仍在启动，请稍后重新打开")
        acquired = True
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def find_running(data):
    try:
        info = json.loads((data / "runtime.json").read_text(encoding="utf-8"))
        port = int(info["port"])
        if not 1024 <= port <= 65535 or not isinstance(info.get("token"), str):
            return None
        local = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/health")
        with local.open(req, timeout=1) as response:
            health = json.load(response)
            if health.get("app") == "agent-workbench":
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/auth",
                    data=json.dumps({"token": info["token"]}).encode("utf-8"),
                    headers={"X-Workbench": "1", "Content-Type": "application/json"},
                )
                with local.open(req, timeout=1) as auth:
                    if auth.status == 200:
                        return {**info, "version": health.get("version", "")}
    except Exception:
        pass
    return None


def serve(data):
    # Hold ownership until every accepted request and file operation has finished.
    with startup_lock("Local\\AgentWorkbench.Service"):
        token = secrets.token_urlsafe(40)
        # Prefer a stable port, fall back to an available one without displacing other apps.
        try:
            server = WorkbenchServer(("127.0.0.1", 47831), token=token)
        except OSError:
            server = WorkbenchServer(("127.0.0.1", 0), token=token)
        info = {"pid": os.getpid(), "port": server.server_port, "token": token}
        try:
            if os.name == "nt":
                from agent_workbench.desktop import DesktopController

                server.desktop = DesktopController(
                    open_window=lambda: open_window(info),
                    shutdown=server.request_shutdown,
                    busy=lambda: (
                        server.service.runs.active is not None
                        or server.service.workflows.active is not None
                        or server.service.conversations.active is not None
                    ),
                    closing=server.closing.is_set,
                )
                server.desktop.start()
            write_json(data / "runtime.json", info)
            server.serve_forever(poll_interval=0.3)
        finally:
            server.server_close()
            if server.desktop:
                server.desktop.stop(close_windows=True)
            try:
                if (
                    json.loads((data / "runtime.json").read_text(encoding="utf-8")).get(
                        "pid"
                    )
                    == os.getpid()
                ):
                    (data / "runtime.json").unlink()
            except Exception:
                pass


def open_window(info, browser=False):
    url = f"http://127.0.0.1:{info['port']}/#token={info['token']}"
    edge = (
        Path(os.getenv("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
        / "Microsoft/Edge/Application/msedge.exe"
    )
    if edge.is_file() and not browser:
        subprocess.Popen(
            [str(edge), "--app=" + url, "--window-size=1440,1000"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        webbrowser.open(url)


def open_desktop(info):
    local = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        f"http://127.0.0.1:{info['port']}/api/desktop/open",
        data=b"{}",
        headers={
            "X-Workbench": "1", "Content-Type": "application/json",
            "Cookie": f"awb_session_{info['port']}={info['token']}",
        },
    )
    with local.open(request, timeout=5) as response:
        json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    data = runtime_root()
    data.mkdir(parents=True, exist_ok=True)
    if getattr(sys, "frozen", False) and os.name == "nt":
        import ctypes

        ctypes.windll.kernel32.SetDllDirectoryW(None)
    if args.serve:
        serve(data)
        return
    with startup_lock():
        info = find_running(data)
        if info and info["version"] != __version__:
            raise RuntimeError(
                "另一个版本的工坊仍在后台运行。请先从右下角托盘或窗口左下角"
                "完全退出正在运行的工坊，再打开新版。"
            )
        if not info:
            environment = os.environ.copy()
            if getattr(sys, "frozen", False):
                command = [sys.executable, "--serve"]
                # The service is an independent process, not a worker of this launcher.
                environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            else:
                executable = Path(sys.executable)
                windowless = executable.with_name("pythonw.exe")
                command = [
                    str(windowless if windowless.exists() else executable),
                    str(Path(__file__).resolve()),
                    "--serve",
                ]
            with (data / "server.log").open("ab") as log:
                subprocess.Popen(
                    command,
                    stdout=log,
                    stderr=log,
                    env=environment,
                    cwd=str(Path(__file__).parent),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            for _ in range(120):
                time.sleep(0.25)
                info = find_running(data)
                if info:
                    break
            if not info:
                raise RuntimeError("启动失败，请查看 " + str(data / "server.log"))
    if args.no_open:
        print(f"Agent Workbench is running on port {info['port']}")
        return
    if os.name == "nt" and not args.browser:
        open_desktop(info)
    else:
        open_window(info, browser=args.browser)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logging.exception("Workbench startup failed")
        if os.name == "nt":
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, str(exc), "Agent Workbench", 0x10)
        else:
            raise
