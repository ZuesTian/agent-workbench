"""Verified data relocation; runtime discovery remains at a stable user location."""

import hashlib
import os
import shutil
import uuid
from pathlib import Path

from .storage import read_json, write_json, stamp
from .validation import text


def control_root(home=None):
    return Path(os.getenv("LOCALAPPDATA", str((home or Path.home()) / ".local/share"))) / "AgentWorkbench"


def data_root(home=None):
    control = control_root(home)
    pointer = read_json(control / "data-location.json", {})
    if not pointer:
        return control
    target = Path(pointer["path"])
    if not target.is_absolute() or not target.is_dir():
        raise ValueError("迁移的数据目录不可用，请恢复该目录或 data-location.json 中的路径")
    return target


EXCLUDED = {"runtime.json", "server.log", "data-location.json", "relocation-receipt.json", "cache", "browser-profile"}


def inventory(root):
    files = {}
    def visit(folder):
        for path in folder.iterdir():
            relative = path.relative_to(root)
            if relative.parts[0] in EXCLUDED:
                continue
            if path.is_symlink() or path.is_junction():
                raise ValueError("数据目录含链接或目录联接，无法安全迁移：" + str(relative))
            if path.is_dir():
                visit(path)
            elif path.is_file():
                with path.open("rb") as stream:
                    files[relative.as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
            else:
                raise ValueError("数据目录含非常规文件：" + str(relative))
    visit(root)
    return files


def relocate_references(source, target, manifest):
    """Rewrite only editable schemes; historical task evidence stays immutable."""
    from .catalog import uid

    mapping = {}
    for relative in manifest:
        if relative.startswith("library/skills/") and Path(relative).name == "SKILL.md":
            mapping[uid("skill", str((source / relative).resolve()).casefold())] = uid("skill", str((target / relative).resolve()).casefold())
    library = read_json(source / "library/mcps.json", [])
    for item in library:
        mapping[uid("mcp", source / "library/mcps.json", "全局", item["name"], item["id"])] = uid("mcp", target / "library/mcps.json", "全局", item["name"], item["id"])
    rewritten = []
    for relative in manifest:
        parts = Path(relative).parts
        if len(parts) != 2 or parts[0] not in {"profiles", "teams"} or not relative.endswith(".json"):
            continue
        path = target / relative
        document = read_json(path, {})
        changed = False
        if parts[0] == "profiles":
            for kind in ("skills", "mcps"):
                previous = document.get(kind, [])
                updated = [mapping.get(ident, ident) for ident in previous]
                if previous != updated:
                    document[kind] = updated
                    changed = True
        else:
            for node in document.get("nodes", []):
                for kind in ("skills", "mcps"):
                    for ref in node.get(kind, []):
                        if ref["id"] in mapping and mapping[ref["id"]] != ref["id"]:
                            ref["id"] = mapping[ref["id"]]
                            changed = True
            if changed:
                document["revision"] = uuid.uuid4().hex
        if changed:
            document["updated_at"] = stamp()
            write_json(path, document)
            if read_json(path, None) != document:
                raise ValueError("迁移后方案引用校验失败，未切换数据目录")
            rewritten.append(relative)
    return rewritten


def relocate(source, destination, *, control=None, switch=True):
    logical_source = source
    source = source.resolve(strict=True)
    raw = Path(text(destination, "目标目录", 4096, required=True)).expanduser()
    if not raw.is_absolute():
        raise ValueError("目标目录必须是绝对路径")
    target = raw.resolve()
    if target == source or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("新旧数据目录不能相同或互相包含")
    if any(p.is_symlink() or p.is_junction() for p in [raw, *raw.parents]):
        raise ValueError("目标目录不能包含链接或目录联接")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError("目标目录必须为空；不会覆盖已有数据")
    manifest = inventory(source)
    target.mkdir(parents=True, exist_ok=True)
    # Leave an incomplete copy in place on error; never delete user data on rollback.
    for relative in manifest:
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, dest)
    if manifest != inventory(target) or manifest != inventory(source):
        raise ValueError("复制校验失败或源文件发生变化；未切换数据目录，原数据保留")
    rewritten = relocate_references(logical_source, target, manifest) if switch else []
    final = inventory(target)
    if manifest != inventory(source) or set(final) != set(manifest) or any(final[p] != value for p, value in manifest.items() if p not in rewritten):
        raise ValueError("引用更新期间文件发生变化，未切换数据目录")
    receipt = {"source": str(source), "target": str(target), "files": len(manifest),
               "sha256": manifest, "target_sha256": final, "rewritten_schemes": rewritten,
               "at": stamp(), "restart_required": switch}
    write_json(target / "relocation-receipt.json", receipt)
    if switch:
        write_json((control or control_root()) / "data-location.json", {"path": str(target), "previous": str(source), "at": receipt["at"]})
    return {key: value for key, value in receipt.items() if key not in {"sha256", "target_sha256"}}
