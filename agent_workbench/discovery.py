"""Read-only installation evidence, independent of leftover configuration."""

from __future__ import annotations

import json
import os
import re
import shutil
from collections import deque
from pathlib import Path

import psutil

# A directory name alone is never installation evidence.
PROGRAMS = {
    "codex": ("Codex", ("codex.exe",), ("Codex", "OpenAI/Codex")),
    "claude": ("Claude Code", ("claude.exe",), ("Claude Code",)),
    "claude-desktop": ("Claude", ("claude.exe",), ("Claude",)),
    "cursor": ("Cursor", ("cursor.exe",), ("Cursor",)),
    "trae": ("Trae(?:Code)?", ("trae.exe",), ("Trae",)),
    "trae-cn": ("Trae CN", ("trae cn.exe", "trae.exe"), ("Trae CN",)),
    "workbuddy": ("WorkBuddy", ("workbuddy.exe",), ("WorkBuddy",)),
    "opencode": ("OpenCode", ("opencode.exe",), ("OpenCode",)),
    "windsurf": ("Windsurf", ("windsurf.exe",), ("Windsurf",)),
    "antigravity": ("Antigravity", ("antigravity.exe",), ("Antigravity",)),
    "vscode": (
        "(?:Microsoft )?Visual Studio Code",
        ("code.exe",),
        ("Microsoft VS Code",),
    ),
    "deepseek-harness": (
        "DeepSeek Harness",
        ("deepseek harness.exe",),
        ("DeepSeek Harness",),
    ),
    "zcode": ("ZCode", ("zcode.exe",), ("ZCode",)),
}


def registry_records():
    """Read both registry views. Never run uninstallers or registered commands."""
    if os.name != "nt":
        return []
    import winreg

    result = []
    key_name = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, key_name, 0, winreg.KEY_READ | view) as root:
                    for index in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            with winreg.OpenKey(
                                root, winreg.EnumKey(root, index)
                            ) as key:
                                row = {}
                                for field in (
                                    "DisplayName",
                                    "InstallLocation",
                                    "DisplayIcon",
                                    "UninstallString",
                                ):
                                    try:
                                        row[field] = winreg.QueryValueEx(key, field)[0]
                                    except OSError:
                                        pass
                                result.append(row)
                        except OSError:
                            continue
            except OSError:
                continue
    return result


def executable_path(value):
    """Extract a path from registry icon/command text without executing it."""
    if not isinstance(value, str):
        return None
    value = os.path.expandvars(value.strip())
    match = re.match(r'^"([^"]+)"', value) or re.match(
        r"^(.+?\.(?:exe|ico))(?=,|\s|$)", value, re.I
    )
    if match:
        path = Path(match[1])
        if path.is_absolute():
            return path
    return None


def fixed_drives():
    if os.name != "nt":
        return []
    import ctypes

    kernel = ctypes.windll.kernel32
    return [
        Path(f"{letter}:/")
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        if kernel.GetLogicalDrives() & (1 << (ord(letter) - ord("A")))
        and kernel.GetDriveTypeW(f"{letter}:\\") == 3
    ]


def _process_candidates():
    """One Windows process snapshot, without opening every process on the machine."""
    import ctypes
    from ctypes import wintypes

    # https://learn.microsoft.com/windows/win32/api/tlhelp32/ns-tlhelp32-processentry32w
    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        method = getattr(kernel, name)
        method.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        method.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if handle == ctypes.c_void_p(-1).value:
        return []
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(entry)
    result = []
    try:
        available = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while available:
            result.append((entry.th32ProcessID, entry.szExeFile))
            available = kernel.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    return result


def process_paths():
    """Read executable paths only, never process arguments or environment."""
    if os.name != "nt":
        return []
    names = {n for _, executables, _ in PROGRAMS.values() for n in executables}
    paths = set()
    for pid, name in _process_candidates():
        if name.casefold() not in names:
            continue
        try:
            executable = psutil.Process(pid).exe()
            if executable:
                paths.add(executable)
        except (psutil.Error, OSError):
            continue
    return [Path(p) for p in sorted(paths)]


def _path_agent(path):
    name, parts = path.name.casefold(), str(path).replace("\\", "/").casefold()
    if name == "claude.exe":
        return (
            "claude-desktop"
            if "/claude/" in parts or "claudedesktop" in parts
            else "claude"
        )
    if name == "trae.exe":
        return "trae-cn" if "trae cn" in parts or "trae-cn" in parts else "trae"
    return next(
        (key for key, (_, executables, _) in PROGRAMS.items() if name in executables),
        None,
    )


def probe_installations(home, roaming, extra_roots=(), *, system=True):
    found, issues, inspected = {}, [], []
    running = {str(p).casefold() for p in process_paths()} if system else set()

    def add(agent_id, path, source, kind="executable"):
        if not path.is_file():
            return
        records = found.setdefault(agent_id, [])
        canonical = str(path.resolve())
        if any(x["path"].casefold() == canonical.casefold() for x in records):
            return
        records.append(
            {
                "path": canonical,
                "source": source,
                "kind": kind,
                "running": str(path).casefold() in running,
            }
        )

    if system:
        for record in registry_records():
            title = str(record.get("DisplayName", ""))
            # Prefer specific names (Trae CN / Claude Code) over their families.
            candidates = sorted(PROGRAMS, key=lambda k: -len(PROGRAMS[k][0]))
            agent_id = next(
                (
                    key
                    for key in candidates
                    if re.fullmatch(PROGRAMS[key][0] + r"(?:\s+.*)?", title, re.I)
                ),
                None,
            )
            if not agent_id:
                continue
            _, names, _ = PROGRAMS[agent_id]
            directories = []
            location = record.get("InstallLocation")
            if isinstance(location, str) and Path(location.strip('"')).is_absolute():
                directories.append(Path(location.strip('"')))
            for key in ("DisplayIcon", "UninstallString"):
                path = executable_path(record.get(key))
                if path:
                    directories.append(path.parent)
                    if path.name.casefold() in names:
                        add(agent_id, path, "Windows 安装记录")
            for directory in directories:
                for name in names:
                    add(agent_id, directory / name, "Windows 安装记录")
        for value in running:
            path = Path(value)
            agent_id = _path_agent(path)
            if agent_id:
                add(agent_id, path, "运行中程序路径")
        local = Path(os.getenv("LOCALAPPDATA", str(home / "AppData/Local")))
        bases = [local / "Programs", local, home / ".local/bin"]
        for drive in fixed_drives():
            bases.extend(
                drive / name
                for name in (
                    "",
                    "Program Files",
                    "Program Files (x86)",
                    "Programs",
                    "Apps",
                    "Software",
                )
            )
        inspected = [str(p) for p in bases if p.is_dir()]
        for agent_id, (_, names, folders) in PROGRAMS.items():
            for name in names:
                path = shutil.which(name)
                if path:
                    add(agent_id, Path(path), "PATH")
                for base in bases:
                    for folder in folders:
                        add(agent_id, base / folder / name, "常用安装目录")
        for path in (local / "OpenAI/Codex/bin").glob("*/codex.exe"):
            add("codex", path, "Codex 安装目录")
        for path in (local / "AnthropicClaude").glob("*/claude.exe"):
            add("claude-desktop", path, "桌面程序目录")
        for name, package, script in (
            ("codex", "@openai/codex", "bin/codex.js"),
            ("claude", "@anthropic-ai/claude-code", "cli.js"),
            ("gemini", "@google/gemini-cli", "dist/index.js"),
            ("opencode", "opencode-ai", "bin/opencode"),
        ):
            add(
                name,
                roaming / "npm/node_modules" / package / script,
                "npm 启动入口",
                "entry",
            )
        add("claude", home / ".local/bin/claude.exe", "用户启动入口")
        for base in (home / ".vscode/extensions", home / ".cursor/extensions"):
            for agent_id, prefix in (
                ("cline", "saoudrizwan.claude-dev-"),
                ("roo", "rooveterinaryinc.roo-cline-"),
            ):
                for path in base.glob(prefix + "*/package.json"):
                    try:
                        manifest = json.loads(path.read_text(encoding="utf-8"))
                        main = manifest.get("main")
                        if isinstance(main, str) and (path.parent / main).is_file():
                            add(
                                agent_id,
                                path.parent / main,
                                "编辑器扩展文件",
                                "extension",
                            )
                    except (OSError, ValueError):
                        continue

    # User-selected portable locations are bounded; no whole-disk recursive sweep.
    for root in extra_roots:
        root = Path(root)
        inspected.append(str(root))
        if not root.is_dir():
            issues.append(f"程序查找目录不存在：{root}")
            continue
        queue, seen, count = deque([(root, 0)]), set(), 0
        while queue:
            directory, depth = queue.popleft()
            try:
                real = str(directory.resolve()).casefold()
                if real in seen:
                    continue
                seen.add(real)
                count += 1
                if count > 1500:
                    issues.append(f"程序查找目录超过 1500 个子目录，请缩小范围：{root}")
                    break
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if entry.is_file(follow_symlinks=False):
                            path = Path(entry.path)
                            agent_id = _path_agent(path)
                            if agent_id:
                                add(agent_id, path, "自选程序目录")
                        elif depth < 3 and entry.is_dir(follow_symlinks=False):
                            if (
                                entry.name.casefold()
                                not in {
                                    "node_modules",
                                    ".git",
                                    "resources",
                                    "cache",
                                    "logs",
                                    "__pycache__",
                                }
                                and not getattr(
                                    Path(entry.path), "is_junction", lambda: False
                                )()
                            ):
                                queue.append((Path(entry.path), depth + 1))
            except OSError:
                issues.append(f"程序目录无法读取：{directory}")
    return found, {"roots": inspected, "issues": issues, "system": system}
