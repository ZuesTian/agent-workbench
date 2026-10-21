"""Small, shared persistence primitives. No discovery or HTTP dependencies."""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path


def stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def record_roots(root: Path) -> frozenset[Path]:
    """Pin the read and write locations of a merged Windows AppData directory.

    An existing directory can resolve to real AppData, while new children resolve
    to LocalCache. An exclusive empty probe observes the actual write location.
    Keep the logical directory for enumeration so existing records stay visible.
    """
    root.mkdir(parents=True, exist_ok=True)
    read_root = root.resolve(strict=True)
    probe = root / (".awb-location-" + uuid.uuid4().hex + ".tmp")
    created = False
    try:
        with probe.open("x", encoding="utf-8"):
            created = True
        return frozenset((read_root, probe.resolve(strict=True).parent))
    finally:
        if created:
            probe.unlink(missing_ok=True)


def record_directory(root: Path, name: str, roots: frozenset[Path], label: str) -> Path:
    path = root / name
    if path.is_symlink() or path.is_junction() or path.resolve().parent not in roots:
        raise ValueError(label + "记录路径已被重定向")
    return path


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.04 * (attempt + 1))
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path):
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        return
    finally:
        os.close(descriptor)
