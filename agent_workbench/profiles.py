"""Profiles store references only; they never contain MCP credentials."""

from __future__ import annotations

import uuid
from pathlib import Path

from .storage import read_json, stamp, write_json
from .validation import identifier, selection, text


class Profiles:
    def __init__(self, data: Path):
        self.root = data / "profiles"

    def list(self):
        items = []
        for path in self.root.glob("*.json"):
            try:
                profile = read_json(path, {})
                identifier(profile.get("id"), "方案编号")
                if profile["id"] != path.stem:
                    raise ValueError("方案文件编号不匹配")
                selection(profile)
                text(profile.get("name"), "方案名称", 80, required=True)
                text(profile.get("description", ""), "用途说明")
                text(profile.get("updated_at", ""), "更新时间", 100)
                items.append(profile)
            except (ValueError, TypeError, AttributeError, OSError):
                items.append(
                    {
                        "id": path.stem,
                        "name": "无法读取的方案",
                        "description": "原文件已保留，请检查本地数据目录中的方案文件。",
                        "skills": [],
                        "mcps": [],
                        "agents": [],
                        "updated_at": "",
                        "error": "方案文件损坏或格式不正确",
                    }
                )
        return sorted(items, key=lambda x: x.get("updated_at", ""), reverse=True)

    def save(self, body):
        chosen = selection(body)
        name = text(body.get("name", ""), "方案名称", 80, required=True)
        profile = {
            "id": (
                identifier(body["id"], "方案编号")
                if body.get("id")
                else uuid.uuid4().hex
            ),
            "name": name,
            "description": text(body.get("description", ""), "用途说明"),
            **{
                key: chosen[key]
                for key in ("skills", "mcps", "agents", "mode", "config_targets")
            },
            "updated_at": stamp(),
        }
        write_json(self.root / (profile["id"] + ".json"), profile)
        return profile

    def delete(self, profile_id):
        identifier(profile_id, "方案编号")
        (self.root / (profile_id + ".json")).unlink(missing_ok=True)
        return {"ok": True}
