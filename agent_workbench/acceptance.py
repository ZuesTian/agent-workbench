"""Bounded, read-only artifact checks; execution success is a separate verdict."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PureWindowsPath

from .storage import stamp
from .validation import text

MAX_CHECKS = 20
MAX_BYTES = 16 * 1024 * 1024
KINDS = {"file": "非空文件", "json": "有效 JSON", "contains": "包含指定文本"}
STATUSES = {
    "not_configured": "未配置文件验收", "pending": "等待文件验收",
    "not_run": "未执行文件验收", "passed": "文件验收通过", "failed": "文件验收未通过",
}


def rules(value):
    if not isinstance(value, list) or len(value) > MAX_CHECKS:
        raise ValueError("最多配置 20 项文件验收")
    result = []
    for raw in value:
        if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
            raise ValueError("文件验收类型不正确")
        path = text(raw.get("path"), "交付文件路径", 240, required=True).replace("\\", "/")
        parts = path.split("/")
        if (
            PureWindowsPath(path).drive or path.startswith("/")
            or any(part in {"", ".", ".."} or part.endswith((".", " ")) for part in parts)
            or re.search(r'[<>:"|?*\x00-\x1f]', path)
            or any(re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p) for p in parts)
        ):
            raise ValueError("交付文件必须是工作目录内的相对路径，不支持上级路径或特殊设备名")
        expected = text(raw.get("expected", ""), "验收文本", 1000, required=raw["kind"] == "contains")
        changed = raw.get("changed", True)
        if not isinstance(changed, bool):
            raise ValueError("本次新增或变化选项必须是布尔值")
        result.append({"path": path, "kind": raw["kind"], "expected": expected if raw["kind"] == "contains" else "", "changed": changed})
    if sum(len(item["path"]) + len(item["expected"]) for item in result) > 4000:
        raise ValueError("交付路径与验收文本合计最多 4000 个字符")
    return result


def _target(root, relative):
    root = Path(root).resolve(strict=True)
    target = root
    for part in relative.split("/"):
        target /= part
        if target.is_symlink() or target.is_junction():
            raise ValueError("交付路径包含链接或目录联接，未读取文件")
    if not target.resolve().is_relative_to(root):
        raise ValueError("交付文件超出工作目录")
    return target


def _read(root, relative):
    """Capture a stable small file; refuse links, devices and concurrent changes."""
    path = _target(root, relative)
    try:
        before = path.stat()
    except FileNotFoundError:
        return {"exists": False}, None
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("交付路径不是普通文件")
    if before.st_size > MAX_BYTES:
        raise ValueError("文件超过 16 MiB，本版未检查")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if not os.path.samestat(before, opened):
            raise ValueError("文件在检查时被替换，请重新执行验收")
        data = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
    _target(root, relative)
    final = path.stat()
    if (len(data) > MAX_BYTES or before.st_size != len(data)
            or before.st_mtime_ns != after.st_mtime_ns
            or final.st_mtime_ns != after.st_mtime_ns
            or not os.path.samestat(opened, final)):
        raise ValueError("文件在检查时发生变化，无法保存稳定证据")
    return {"exists": True, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "modified_ns": str(final.st_mtime_ns)}, data


def prepare(root, checks):
    captured = []
    for check in checks:
        try:
            before, _ = _read(root, check["path"])
        except (OSError, ValueError) as exc:
            raise ValueError(f"无法建立交付文件基线：{check['path']}：{exc}") from exc
        captured.append({**check, "before": before, "status": "pending"})
    return {"status": "pending" if checks else "not_configured", "baseline_at": stamp(),
            "checked_at": None, "checks": captured}


def _reject_constant(value):
    raise ValueError("JSON 包含非标准数值：" + value)


def evaluate(root, evidence, execution_status, cancelled=lambda: False):
    checks = evidence.get("checks", [])
    if not checks:
        return {**evidence, "status": "not_configured"}
    if execution_status != "succeeded" or cancelled():
        return {**evidence, "status": "not_run", "checks": [
            {**check, "status": "not_run", "reason": "流程未完整执行，未验收产物"} for check in checks
        ]}
    results = []
    for check in checks:
        if cancelled():
            results.append({**check, "status": "not_run", "reason": "验收已取消"})
            continue
        result = {**check, "status": "failed"}
        try:
            after, data = _read(root, check["path"])
            result["after"] = after
            if data is None:
                raise ValueError("文件不存在")
            if not data:
                raise ValueError("文件为空")
            if check["changed"] and after["sha256"] == check["before"].get("sha256"):
                raise ValueError("文件内容与启动前相同，不能确认本次交付")
            if check["kind"] == "json":
                json.loads(data.decode("utf-8-sig"), parse_constant=_reject_constant)
            elif check["kind"] == "contains" and check["expected"] not in data.decode("utf-8-sig"):
                raise ValueError("未找到指定文本")
            result.update(status="passed", reason="符合已配置的文件规则")
        except UnicodeError:
            result["reason"] = "文件不是有效 UTF-8 文本"
        except (json.JSONDecodeError, RecursionError):
            result["reason"] = "JSON 格式无效或嵌套过深"
        except (OSError, ValueError) as exc:
            result["reason"] = str(exc)
        results.append(result)
    status = "not_run" if cancelled() else "passed" if all(r["status"] == "passed" for r in results) else "failed"
    return {**evidence, "status": status, "checked_at": stamp(), "checks": results}


def prompt_rules(checks):
    if not checks:
        return ""
    lines = ["\n\n## 最终交付文件与自动检查", "全部步骤结束后由工坊只读检查下列文件。格式通过不代表业务结论正确。"]
    for check in checks:
        lines.append(f"- {check['path']}：{KINDS[check['kind']]}；" + (
            "要求本次新增或内容变化" if check["changed"] else "允许使用已有文件"
        ) + (f"；指定文本：{check['expected']}" if check["expected"] else ""))
    return "\n".join(lines)


def summary_lines(evidence):
    lines = ["", "## 文件验收", STATUSES[evidence["status"]],
             "检查仅覆盖配置的文件规则，不代表业务或科学结论已通过人工审核。"]
    for check in evidence.get("checks", []):
        lines.append(f"- {check['path']} / {KINDS[check['kind']]} / {check['status']}：{check.get('reason', '')}")
        if check.get("after", {}).get("sha256"):
            lines.append(f"  SHA256：{check['after']['sha256']}；大小：{check['after']['bytes']} 字节")
    return lines
