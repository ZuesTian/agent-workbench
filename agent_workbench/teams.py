"""Local collaboration blueprints. Saving a team never starts an agent."""

from __future__ import annotations

import math
import re
import uuid

from .storage import read_json, stamp, write_json
from .validation import identifier, text

ROLES = {"tutor", "employee", "sub"}


def node_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value):
        raise ValueError("节点编号只能包含字母、数字、短横线和下划线")
    return value


def references(value):
    if not isinstance(value, list) or len(value) > 200:
        raise ValueError("每个节点最多绑定 200 项同类资源")
    result = []
    seen = set()
    for ref in value:
        if not isinstance(ref, dict):
            raise ValueError("资源引用格式不正确")
        key = text(ref.get("id"), "资源编号", 200, required=True)
        if key in seen:
            raise ValueError("资源引用重复")
        seen.add(key)
        result.append(
            {"id": key, "name": text(ref.get("name"), "资源名称", 200, required=True)}
        )
    return result


def blueprint(body):
    """Project incoming JSON onto an explicit schema; discard runtime/config data."""
    if not isinstance(body, dict):
        raise ValueError("协作方案必须是 JSON 对象")
    if body.get("format") != "agent-workbench-team" or body.get("version") != 1:
        raise ValueError("协作方案格式或版本不正确")
    nodes = body.get("nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 100:
        raise ValueError("协作方案需要 1–100 个节点")
    layout = body.get("layout", "horizontal")
    if layout not in ("horizontal", "vertical"):
        raise ValueError("画布布局不正确")
    result = {
        "format": "agent-workbench-team",
        "version": 1,
        "name": text(body.get("name"), "方案名称", 80, required=True),
        "goal": text(body.get("goal", ""), "任务目标", 5000),
        "layout": layout,
        "nodes": [],
    }
    for raw in nodes:
        if not isinstance(raw, dict) or raw.get("role") not in ROLES:
            raise ValueError("节点类型不正确")
        position = {}
        for key in ("x", "y"):
            value = raw.get(key, 0)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or abs(value) > 100000
            ):
                raise ValueError("节点坐标超出范围")
            position[key] = round(value, 2)
        collapsed = raw.get("collapsed", False)
        if not isinstance(collapsed, bool):
            raise ValueError("折叠状态不正确")
        result["nodes"].append(
            {
                "id": node_id(raw.get("id")),
                "parentId": (
                    node_id(raw["parentId"])
                    if raw.get("parentId") is not None
                    else None
                ),
                "role": raw["role"],
                "name": text(raw.get("name"), "节点名称", 80, required=True),
                "agentId": text(raw.get("agentId", ""), "客户端编号", 200),
                "model": text(raw.get("model", ""), "执行模型", 120),
                "policy": text(raw.get("policy", ""), "审核策略", 500),
                "prompt": text(raw.get("prompt", ""), "职责说明", 10000),
                "skills": references(raw.get("skills", [])),
                "mcps": references(raw.get("mcps", [])),
                "collapsed": collapsed,
                **position,
            }
        )
    parents = {node["id"]: node["parentId"] for node in result["nodes"]}
    if len(parents) != len(nodes):
        raise ValueError("节点编号不能重复")
    for start in parents:
        visited = set()
        cursor = start
        while cursor is not None:
            if cursor not in parents:
                raise ValueError("节点引用了不存在的上级")
            if cursor in visited:
                raise ValueError("上下级关系形成了循环")
            visited.add(cursor)
            cursor = parents[cursor]
    return result


class Teams:
    def __init__(self, data):
        self.root = data / "teams"

    def list(self):
        items = []
        for path in self.root.glob("*.json"):
            try:
                raw = read_json(path, {})
                item = blueprint(raw)
                if identifier(raw.get("id"), "方案编号") != path.stem:
                    raise ValueError("方案文件编号不匹配")
                item.update(
                    id=raw["id"],
                    revision=identifier(raw.get("revision"), "方案版本"),
                    updated_at=text(raw.get("updated_at", ""), "更新时间", 100),
                )
                items.append(item)
            except (ValueError, TypeError, AttributeError, OSError):
                items.append(
                    {
                        "id": path.stem,
                        "name": "无法读取的协作方案",
                        "updated_at": "",
                        "error": "原文件已保留，请检查本地数据目录中的 teams 文件夹。",
                    }
                )
        return sorted(items, key=lambda item: item["updated_at"], reverse=True)

    def save(self, body):
        team = blueprint(body)
        key = identifier(body["id"], "方案编号") if body.get("id") else uuid.uuid4().hex
        path = self.root / (key + ".json")
        if body.get("id"):
            current = read_json(path, None)
            if not current:
                raise ValueError("方案已被删除，请另存为新方案")
            if current.get("revision") != body.get("revision"):
                raise ValueError("方案已在另一窗口更新，请重新载入或另存为新方案")
            # A corrupt saved file must not be silently overwritten.
            blueprint(current)
        team.update(id=key, revision=uuid.uuid4().hex, updated_at=stamp())
        write_json(path, team)
        return team

    def delete(self, body):
        path = self.root / (identifier(body.get("id"), "方案编号") + ".json")
        current = read_json(path, None)
        if current and current.get("revision") != body.get("revision"):
            raise ValueError("方案已在另一窗口更新，请重新载入后删除")
        path.unlink(missing_ok=True)
        return {"ok": True}
