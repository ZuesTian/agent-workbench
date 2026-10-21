"""Bounded, complete resource copies and content checks for local transactions."""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path

MAX_BYTES = 100 * 1024 * 1024
MAX_ENTRIES = 10_000


def is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def path_identity(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def assert_target(path: Path, identity: str):
    if is_link(path) or path_identity(path) != identity:
        raise ValueError("目标路径变为链接或位置已变化，请重新预览：" + path.name)


def tree_files(root: Path):
    """Include every file, including hidden/dependency files; never prune backups."""
    root = root.resolve()
    total, count = 0, 0

    def unreadable(error):
        raise error

    for parent, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
        dirs.sort()
        for name in sorted(dirs + files):
            path = Path(parent) / name
            count += 1
            if count > MAX_ENTRIES:
                raise ValueError("单个 Skill 超过 10000 个条目，已停止装配")
            if is_link(path):
                raise ValueError("Skill 内含链接，请先整理为独立目录：" + str(path))
            relative = path.relative_to(root).as_posix()
            if path.is_dir():
                yield path, relative + "/"
            else:
                if not path.is_file():
                    raise ValueError("Skill 含不支持的特殊文件：" + path.name)
                total += path.stat().st_size
                if total > MAX_BYTES:
                    raise ValueError("单个 Skill 超过 100 MB，已停止装配")
                yield path, relative


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def digest(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.is_file():
        return file_digest(path)
    h = hashlib.sha256()
    for file, relative in sorted(tree_files(path), key=lambda item: item[1]):
        h.update(relative.encode("utf-8"))
        h.update(b"\0")
        if not relative.endswith("/"):
            h.update(bytes.fromhex(file_digest(file)))
    return h.hexdigest()


def copy_resource(source: Path, target: Path):
    if source.is_dir():
        # Validate before copying; a race is caught by the staged digest.
        list(tree_files(source))
        shutil.copytree(source.resolve(), target, symlinks=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def remove_owned(path: Path):
    """Remove an exact, already verified transaction-owned path."""
    if is_link(path):
        raise ValueError("资源变为链接，拒绝覆盖或恢复：" + str(path))
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def replace_resource(
    source: Path,
    target: Path,
    expected: str,
    before: str | None,
    identity: str,
    *,
    aside=None,
):
    """Stage beside the destination and recheck it immediately before commit."""
    assert_target(target, identity)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name("." + target.name + ".awb-" + uuid.uuid4().hex)
    old = (
        Path(aside)
        if aside
        else target.with_name("." + target.name + ".awb-old-" + uuid.uuid4().hex)
    )
    if old.parent != target.parent or old == target:
        raise ValueError("事务暂存路径必须是目标的独立同级路径")
    old_identity = os.path.normcase(str(Path(identity).parent / old.name))
    temporary_identity = os.path.normcase(str(Path(identity).parent / temporary.name))
    moved = False
    try:
        copy_resource(source, temporary)
        if digest(temporary) != expected:
            raise ValueError("写入校验失败")
        assert_target(target, identity)
        assert_target(old, old_identity)
        if digest(target) != before:
            raise ValueError("提交前目标已变化，已停止写入：" + target.name)
        if target.exists():
            if old.exists():
                raise ValueError("事务暂存路径已被占用：" + old.name)
            os.replace(target, old)
            moved = True
        os.replace(temporary, target)
    except Exception:
        if moved and not target.exists() and old.exists() and not is_link(old):
            assert_target(target, identity)
            assert_target(old, old_identity)
            if digest(old) != before:
                raise ValueError("事务暂存内容已变化，拒绝自动回退")
            os.replace(old, target)
        raise
    finally:
        if temporary.exists() and not is_link(temporary):
            assert_target(temporary, temporary_identity)
            remove_owned(temporary)
    if aside is None and old.exists() and not is_link(old):
        assert_target(old, old_identity)
        remove_owned(old)
