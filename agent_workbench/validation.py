"""Validate persisted and incoming data before it reaches filesystem operations."""

from __future__ import annotations

import re


def text(value, label, maximum=1000, required=False):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{label}必须是最多 {maximum} 个字符的文本")
    value = value.strip()
    if required and not value:
        raise ValueError(label + "不能为空")
    return value


def identifier(value, label="编号"):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("无效" + label)
    return value


def ids(value, label):
    if not isinstance(value, list) or len(value) > 5000:
        raise ValueError(label + "必须是资源编号列表")
    if any(not isinstance(x, str) or not x or len(x) > 200 for x in value):
        raise ValueError(label + "包含无效资源编号")
    return list(dict.fromkeys(value))


def selection(body):
    result = {
        key: ids(body.get(key, []), label)
        for key, label in (("skills", "Skill"), ("mcps", "MCP"), ("agents", "Agent"))
    }
    mode = body.get("mode", "merge")
    if mode not in ("merge", "replace_mcp"):
        raise ValueError("无效装配模式")
    overwrite = body.get("overwrite", False)
    if not isinstance(overwrite, bool):
        raise ValueError("覆盖选项必须是布尔值")
    targets = body.get("config_targets", {})
    if not isinstance(targets, dict) or len(targets) > 5000:
        raise ValueError("配置目标必须是 Agent 与路径的映射")
    checked_targets = {}
    for agent, path in targets.items():
        if agent not in result["agents"]:
            raise ValueError("配置目标不属于所选 Agent")
        checked_targets[agent] = text(path, "配置目标路径", 4096, required=True)
    result.update(
        mode=mode,
        overwrite=overwrite,
        name=text(body.get("name", "自由装配"), "方案名称", 80),
        config_targets=checked_targets,
    )
    return result
