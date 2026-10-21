"""Native directory picker for forms that otherwise require a typed path."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

BIF_RETURNONLYFSDIRS = 0x0001
BIF_NEWDIALOGSTYLE = 0x0040
BIF_NONEWFOLDERBUTTON = 0x0200
BIF_EDITBOX = 0x0010
RPC_E_CHANGED_MODE = 0x80010106


class BROWSEINFO(ctypes.Structure):
    _fields_ = [
        ("hwndOwner", wintypes.HWND),
        ("pidlRoot", ctypes.c_void_p),
        ("pszDisplayName", ctypes.c_wchar_p),
        ("lpszTitle", ctypes.c_wchar_p),
        ("ulFlags", ctypes.c_uint),
        ("lpfn", ctypes.c_void_p),
        ("lParam", ctypes.c_ssize_t),
        ("iImage", ctypes.c_int),
    ]


def browse_directory():
    """Open the system folder dialog and return a Unicode path, or '' if cancelled."""
    if os.name != "nt":
        raise ValueError("请直接填写已有目录的绝对路径")
    ole32 = ctypes.OleDLL("ole32")
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFO)]
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND

    status = ole32.CoInitializeEx(None, 2) & 0xFFFFFFFF
    initialized = status in (0, 1)
    if not initialized and status != RPC_E_CHANGED_MODE:
        raise ValueError("无法打开目录选择窗口，请直接填写绝对路径")
    display = ctypes.create_unicode_buffer(260)
    info = BROWSEINFO()
    info.hwndOwner = user32.GetForegroundWindow() or None
    info.pszDisplayName = ctypes.cast(display, ctypes.c_wchar_p)
    info.lpszTitle = "选择已有目录"
    info.ulFlags = (
        BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE | BIF_NONEWFOLDERBUTTON | BIF_EDITBOX
    )
    pidl = None
    try:
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(info))
        if not pidl:
            return ""
        path = ctypes.create_unicode_buffer(32768)
        if not shell32.SHGetPathFromIDListW(pidl, path):
            raise ValueError("没有取得所选目录，请直接填写绝对路径")
        return path.value
    finally:
        if pidl:
            ole32.CoTaskMemFree(pidl)
        if initialized:
            ole32.CoUninitialize()
