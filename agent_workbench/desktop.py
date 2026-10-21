"""Windows notification icon and controls for the dedicated Workbench window."""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path

import psutil


WINDOW_TITLE = "Agent 工坊 · 资源与协作"
OPEN = 1001
MINIMIZE = 1002
EXIT = 1003


class DesktopController:
    def __init__(self, open_window, shutdown, busy, closing):
        import pywintypes  # noqa: F401 - include the frozen pywin32 support DLL.
        import win32con
        import win32gui

        self.gui, self.con = win32gui, win32con
        self.open_window, self.shutdown = open_window, shutdown
        self.busy, self.closing = busy, closing
        self.hwnd = None
        self.available = False
        self.ready = threading.Event()
        self.lock = threading.RLock()
        self.last_window = None
        self.launch_until = 0
        self.thread = None
        self.icon = None
        self.owns_icon = False
        self.callback_message = win32con.WM_APP + 41
        self.restart_message = win32gui.RegisterWindowMessage("TaskbarCreated")
        self.class_name = f"AgentWorkbench.Tray.{os.getpid()}"
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        # Old browser windows may still contain an expired session after a crash.
        # Opening a new service must obtain a fresh authenticated window first.
        self.previous_windows = set(self._windows(include_previous=True))

    def _is_workbench_window(self, hwnd):
        try:
            title = self.gui.GetWindowText(hwnd)
            if title != WINDOW_TITLE and not title.endswith(" · Agent 工坊"):
                return False
            if self.gui.GetClassName(hwnd) != "Chrome_WidgetWin_1":
                return False
            pid = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return psutil.Process(pid.value).name().lower() == "msedge.exe"
        except (psutil.Error, OSError):
            return False
        except self.gui.error:
            return False

    def _windows(self, include_previous=False):
        result = []

        def collect(hwnd, _):
            if self._is_workbench_window(hwnd) and (
                include_previous or hwnd not in self.previous_windows
            ):
                result.append(hwnd)

        self.gui.EnumWindows(collect, None)
        return result

    def status(self):
        return {"available": self.available, "closing": self.closing()}

    def start(self):
        self.thread = threading.Thread(target=self._run, name="workbench-tray")
        self.thread.start()
        if not self.ready.wait(5):
            raise RuntimeError("托盘初始化超时")

    def _add_icon(self):
        self.available = False
        try:
            self.gui.Shell_NotifyIcon(
                self.gui.NIM_ADD,
                (self.hwnd, 1, self.gui.NIF_ICON | self.gui.NIF_MESSAGE | self.gui.NIF_TIP,
                 self.callback_message, self.icon, "Agent 工坊 · 后台运行"),
            )
            self.available = True
        except self.gui.error:
            logging.exception("Unable to register Workbench notification icon")

    def _run(self):
        instance = self.gui.GetModuleHandle(None)
        registered = False
        try:
            wc = self.gui.WNDCLASS()
            wc.hInstance = instance
            wc.lpszClassName = self.class_name
            wc.lpfnWndProc = self._message
            self.gui.RegisterClass(wc)
            registered = True
            self.hwnd = self.gui.CreateWindow(
                self.class_name, "Agent Workbench Tray", 0,
                0, 0, 0, 0, 0, 0, instance, None,
            )
            icon_path = Path(__file__).resolve().parents[1] / "assets/app.ico"
            if icon_path.is_file():
                self.icon = self.gui.LoadImage(
                    0, str(icon_path), self.con.IMAGE_ICON, 0, 0,
                    self.con.LR_LOADFROMFILE | self.con.LR_DEFAULTSIZE,
                )
                self.owns_icon = True
            else:
                self.icon = self.gui.LoadIcon(0, self.con.IDI_APPLICATION)
            self._add_icon()
            self.ready.set()
            self.gui.PumpMessages()
        except Exception:
            logging.exception("Workbench notification icon stopped")
        finally:
            self.available = False
            if self.hwnd and self.gui.IsWindow(self.hwnd):
                self.gui.DestroyWindow(self.hwnd)
            if self.owns_icon and self.icon:
                self.gui.DestroyIcon(self.icon)
            if registered:
                self.gui.UnregisterClass(self.class_name, instance)
            self.hwnd = None
            self.ready.set()

    def _menu(self):
        menu = self.gui.CreatePopupMenu()
        try:
            disabled = self.con.MF_GRAYED if self.closing() else 0
            self.gui.AppendMenu(menu, self.con.MF_STRING | disabled, OPEN, "打开工坊")
            self.gui.AppendMenu(menu, self.con.MF_STRING | disabled, MINIMIZE, "最小化窗口")
            self.gui.AppendMenu(menu, self.con.MF_SEPARATOR, 0, "")
            self.gui.AppendMenu(
                menu, self.con.MF_STRING | disabled, EXIT,
                "完全退出（将取消运行中任务）" if self.busy() else "完全退出",
            )
            self.gui.SetForegroundWindow(self.hwnd)
            x, y = self.gui.GetCursorPos()
            command = self.gui.TrackPopupMenu(
                menu, self.con.TPM_RETURNCMD | self.con.TPM_RIGHTBUTTON,
                x, y, 0, self.hwnd, None,
            )
            self.gui.PostMessage(self.hwnd, self.con.WM_NULL, 0, 0)
            self._command(command)
        finally:
            self.gui.DestroyMenu(menu)

    def _command(self, command):
        if self.closing():
            return
        if command == OPEN:
            self.open()
        elif command == MINIMIZE:
            self.minimize(from_tray=True)
        elif command == EXIT:
            if self.busy() and self.gui.MessageBox(
                self.hwnd, "完全退出会取消正在运行的任务，并关闭后台服务。是否继续？",
                "退出 Agent 工坊", self.con.MB_YESNO | self.con.MB_ICONWARNING,
            ) != self.con.IDYES:
                return
            self.shutdown()

    def _message(self, hwnd, message, wparam, lparam):
        try:
            if message == self.restart_message:
                self._add_icon()
                return 0
            if message == self.callback_message:
                if lparam in (self.con.WM_LBUTTONUP, self.con.WM_LBUTTONDBLCLK):
                    self._command(OPEN)
                elif lparam in (self.con.WM_RBUTTONUP, self.con.WM_CONTEXTMENU):
                    self._menu()
                return 0
            if message == self.con.WM_COMMAND:
                self._command(wparam & 0xFFFF)
                return 0
            if message == self.con.WM_CLOSE:
                self.gui.DestroyWindow(hwnd)
                return 0
            if message == self.con.WM_DESTROY:
                self.gui.Shell_NotifyIcon(self.gui.NIM_DELETE, (hwnd, 1))
                self.available = False
                self.gui.PostQuitMessage(0)
                return 0
        except Exception:
            logging.exception("Workbench desktop action failed")
            return 0
        return self.gui.DefWindowProc(hwnd, message, wparam, lparam)

    def open(self):
        with self.lock:
            if self.closing():
                raise ValueError("工坊正在完全退出，请稍后重新打开")
            windows = self._windows()
            hwnd = self.last_window if self.last_window in windows else next(iter(windows), None)
            if hwnd:
                self.gui.ShowWindow(hwnd, self.con.SW_RESTORE)
                try:
                    self.gui.SetForegroundWindow(hwnd)
                except self.gui.error:
                    self.gui.FlashWindow(hwnd, True)
                self.last_window = hwnd
            elif time.monotonic() >= self.launch_until:
                self.launch_until = time.monotonic() + 3
                try:
                    self.open_window()
                except Exception:
                    self.launch_until = 0
                    raise
        return {"ok": True}

    def minimize(self, from_tray=False):
        with self.lock:
            if not self.available or self.closing():
                raise ValueError("托盘尚未就绪，请稍后重试")
            if from_tray:
                windows = self._windows()
            else:
                hwnd = self.gui.GetForegroundWindow()
                if self._is_workbench_window(hwnd):
                    self.previous_windows.discard(hwnd)
                    windows = [hwnd]
                else:
                    windows = self._windows()
                if not windows:
                    return {
                        "minimized": False,
                        "message": "浏览器标签页可直接最小化浏览器；任务会继续运行，可从右下角托盘重新打开。",
                    }
            for hwnd in windows:
                # Keep a taskbar recovery route even if the service crashes.
                self.gui.ShowWindow(hwnd, self.con.SW_MINIMIZE)
                self.last_window = hwnd
        return {"minimized": bool(windows)}

    def stop(self, close_windows=False):
        if close_windows:
            for hwnd in self._windows():
                self.gui.PostMessage(hwnd, self.con.WM_CLOSE, 0, 0)
        if self.hwnd and self.gui.IsWindow(self.hwnd):
            self.gui.PostMessage(self.hwnd, self.con.WM_CLOSE, 0, 0)
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(5)
