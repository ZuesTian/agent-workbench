"""Resolve locations from this Windows user's environment, without running shells."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path


def environment_path(name, default=None, *, environment=None, home=None):
    environment = os.environ if environment is None else environment
    value = next((v for k, v in environment.items() if k.casefold() == name.casefold()), "")
    path = expanded_path(value, environment=environment, home=home)
    return path if path is not None else default


def expanded_path(value, *, environment=None, home=None):
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        return None
    environment = os.environ if environment is None else environment
    values = {k.casefold(): v for k, v in environment.items()}
    value = value.strip().strip('"')
    value = re.sub(r"%([^%]+)%|\$\{([^}]+)\}",
                   lambda m: values.get((m[1] or m[2]).casefold(), m[0]), value)
    if value == "~" or value.startswith(("~/", "~\\")):
        value = str(home or Path.home()) + value[1:]
    path = Path(value)
    return path if path.is_absolute() else None


def local_appdata(home=None, *, environment=None):
    home = home or Path.home()
    fallback = home / "AppData/Local" if os.name == "nt" else home / ".local/share"
    return environment_path("LOCALAPPDATA", fallback, environment=environment, home=home)


def roaming_appdata(home=None, *, environment=None):
    home = home or Path.home()
    fallback = home / "AppData/Roaming" if os.name == "nt" else home / ".config"
    return environment_path("APPDATA", fallback, environment=environment, home=home)


def unique_paths(paths):
    seen = set()
    for path in paths:
        if path is None:
            continue
        path = Path(path)
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            yield path


def program_directories(*, environment=None):
    environment = os.environ if environment is None else environment
    paths = [environment_path(name, environment=environment)
             for name in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)")]
    drive = environment.get("SystemDrive") or Path.home().anchor
    if drive:
        paths += [Path(drive + "/") / name for name in ("Program Files", "Program Files (x86)")]
    return list(unique_paths(paths))


def installation_bases(home=None, *, environment=None):
    home = home or Path.home()
    local = local_appdata(home, environment=environment)
    return list(unique_paths([local / "Programs", local, *program_directories(environment=environment),
                              home / ".local/bin"]))


def path_directories(*, environment=None):
    environment = os.environ if environment is None else environment
    return list(unique_paths(expanded_path(value, environment=environment)
                             for value in environment.get("PATH", "").split(os.pathsep) if value))


def npm_prefixes(home=None, roaming=None, *, environment=None):
    """Inspect prefix settings and launcher locations; never execute npm or a shim."""
    home = home or Path.home()
    environment = os.environ if environment is None else environment
    prefixes = [environment_path("NPM_CONFIG_PREFIX", environment=environment, home=home)]
    config = environment_path("NPM_CONFIG_USERCONFIG", home / ".npmrc", environment=environment, home=home)
    try:
        if config.is_file() and config.stat().st_size <= 1_000_000:
            for line in config.read_text(encoding="utf-8-sig").splitlines():
                match = re.match(r"\s*prefix\s*=\s*(.+?)\s*$", line, re.I)
                if match:
                    prefixes.append(expanded_path(match[1], environment=environment, home=home))
    except (OSError, UnicodeError):
        pass
    prefixes += path_directories(environment=environment)
    prefixes.append((roaming or roaming_appdata(home, environment=environment)) / "npm")
    return list(unique_paths(prefixes))


def npm_entrypoints(package, command, fallback, *, prefixes=None, home=None, roaming=None):
    for prefix in prefixes if prefixes is not None else npm_prefixes(home, roaming):
        root = Path(prefix) / "node_modules" / package
        metadata = root / "package.json"
        entry = fallback
        try:
            if metadata.is_file():
                if metadata.stat().st_size > 1_000_000:
                    continue
                document = json.loads(metadata.read_text(encoding="utf-8-sig"))
                if not isinstance(document, dict) or document.get("name", package) != package:
                    continue
                binary = document.get("bin", {})
                entry = binary.get(command, fallback) if isinstance(binary, dict) else binary
            if not isinstance(entry, str) or not entry or Path(entry).is_absolute():
                continue
            script = root / entry
            if script.is_file() and script.resolve().is_relative_to(root.resolve()):
                yield script
        except (OSError, ValueError, TypeError):
            continue


def node_executable(near=()):
    binary = shutil.which("node")
    candidates = [Path(binary)] if binary else []
    candidates += [Path(folder) / "node.exe" for folder in near]
    candidates += [base / "nodejs/node.exe" for base in installation_bases()]
    return next((str(path.resolve()) for path in unique_paths(candidates) if path.is_file()), None)


def registered_app(executable):
    """Read Windows App Paths, including per-user and 32-bit registrations."""
    if os.name != "nt":
        return None
    import winreg

    key = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{executable}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as handle:
                    path = expanded_path(winreg.QueryValueEx(handle, "")[0])
                    if path and path.is_file():
                        return path
            except OSError:
                continue
    return None


def edge_executable():
    binary = shutil.which("msedge.exe")
    candidates = [Path(binary) if binary else None, registered_app("msedge.exe")]
    candidates += [base / "Microsoft/Edge/Application/msedge.exe" for base in installation_bases()]
    return next((path for path in unique_paths(candidates) if path.is_file()), None)


def git_bash_executable():
    git = shutil.which("git.exe" if os.name == "nt" else "git")
    candidates = []
    if git:
        root = Path(git).resolve().parent.parent
        candidates += [root / "bin/bash.exe", root / "usr/bin/bash.exe"]
    candidates += [base / "Git/bin/bash.exe" for base in installation_bases()]
    return next((str(path.resolve()) for path in unique_paths(candidates) if path.is_file()), None)
