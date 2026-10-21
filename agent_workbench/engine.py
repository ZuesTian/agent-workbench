"""Preview, optimistic concurrency, transactional backups and guarded restore."""

from __future__ import annotations

import copy
import json
import os
import re
import time
import uuid
from pathlib import Path

from .adapters import (
    dump_document,
    read_document,
    resolve_config_target,
    config_targets,
    translate,
    validate_mcp,
    mcp_entries,
    set_mcp_entries,
)
from .storage import stamp, read_json, write_json
from .resources import (
    digest,
    copy_resource,
    is_link,
    remove_owned,
    replace_resource,
    assert_target,
    path_identity,
)
from .validation import selection, identifier


class Engine:
    def __init__(self, catalog):
        self.catalog = catalog
        self.pending = {}

    def preview(self, body):
        with self.catalog.lock:
            body = selection(body)
            self.catalog.get()
            now = time.time()
            self.pending = {
                k: v for k, v in self.pending.items() if now - v["created"] < 900
            }
            if len(self.pending) > 30:
                self.pending.clear()
            agent_ids = list(dict.fromkeys(body.get("agents", [])))
            if not agent_ids:
                raise ValueError("请先选择要应用的 Agent")
            skills = [
                self.catalog.skills[i]
                for i in dict.fromkeys(body.get("skills", []))
                if i in self.catalog.skills
            ]
            mcps = [
                self.catalog.mcps[i]
                for i in dict.fromkeys(body.get("mcps", []))
                if i in self.catalog.mcps
            ]
            if len(skills) != len(set(body.get("skills", []))) or len(mcps) != len(
                set(body.get("mcps", []))
            ):
                raise ValueError("方案中有资源已不存在，请重新扫描并检查选择")
            for items, field in [(skills, "slug"), (mcps, "name")]:
                names = [
                    x[field].casefold() if field == "slug" else x[field] for x in items
                ]
                if len(names) != len(set(names)):
                    raise ValueError(
                        "存在同名的多个版本，请为每个名称保留一个来源："
                        + ", ".join(sorted(n for n in set(names) if names.count(n) > 1))
                    )
            mode = body.get("mode", "merge")
            if mode not in ("merge", "replace_mcp"):
                raise ValueError("无效装配模式")
            allow = body["overwrite"]
            operations, rows, errors, warnings, sources = [], [], [], [], {}
            choices = []
            for m in mcps:
                source = Path(m["source"])
                current_hash = digest(source)
                if current_hash != m.get("_source_hash"):
                    errors.append(m["name"] + " 的来源配置已变化，请重新扫描")
                sources[str(source)] = current_hash
            for agent_id in agent_ids:
                agent = self.catalog.agents.get(agent_id)
                if not agent or not agent.detected:
                    errors.append("Agent 未发现：" + agent_id)
                    continue
                try:
                    if (mcps or mode == "replace_mcp") and not agent.mcp_writable:
                        errors.append(
                            agent.name
                            + " 的 MCP 目前仅支持只读盘点；可单独装配 Skill。"
                        )
                        continue
                    if not agent.installations and not agent.id.startswith("custom-"):
                        warnings.append(
                            agent.name
                            + " 仅发现配置，未找到程序；请确认客户端仍在使用。"
                        )
                    if mcps or mode == "replace_mcp":
                        candidates = config_targets(agent)
                        selected = body["config_targets"].get(agent.id)
                        if len(candidates) > 1 or selected:
                            choices.append(
                                {
                                    "agent_id": agent.id,
                                    "agent": agent.name,
                                    "paths": [str(path) for path in candidates],
                                    "selected": next(
                                        (
                                            str(path)
                                            for path in candidates
                                            if selected and Path(selected) == path
                                        ),
                                        "",
                                    ),
                                }
                            )
                        try:
                            config_path, config_note = resolve_config_target(
                                agent, selected
                            )
                        except ValueError as exc:
                            errors.append(str(exc))
                        else:
                            warnings.append(agent.name + "：" + config_note)
                            doc, before_hash = read_document(config_path, agent.kind)
                            old = copy.deepcopy(dict(mcp_entries(doc, agent)))
                            new = copy.deepcopy(old) if mode == "merge" else {}
                            changes = []
                            for m in mcps:
                                try:
                                    validate_mcp(m["_raw"], m["_kind"])
                                    raw_text = json.dumps(m["_raw"], ensure_ascii=False)
                                    if m["scope"].startswith("插件") and any(
                                        token in raw_text
                                        for token in (
                                            "${",
                                            "{{",
                                            "__PLUGIN",
                                            "${CLAUDE_PLUGIN_ROOT}",
                                            "${CURSOR_PLUGIN_ROOT}",
                                        )
                                    ):
                                        raise ValueError(
                                            "此插件 MCP 使用插件变量，需在目标客户端安装原插件，或新增独立 MCP 定义"
                                        )
                                    if (
                                        m["agent"] == agent.id
                                        and m["_kind"] == agent.kind
                                        and m["scope"] == "全局"
                                    ):
                                        config = copy.deepcopy(m["_raw"])
                                        if "disabled" in config:
                                            config["disabled"] = False
                                        if "enabled" in config:
                                            config["enabled"] = True
                                        notes = []
                                    else:
                                        config, notes = translate(
                                            m["_raw"], m["_kind"], agent
                                        )
                                    warnings += [
                                        f"{agent.name} / {m['name']}：{n}"
                                        for n in notes
                                    ]
                                    name = m["name"]
                                    if (
                                        name in old
                                        and old[name] != config
                                        and not allow
                                    ):
                                        # Enabling the exact same connection is allowed.
                                        old_connection, new_connection = copy.deepcopy(
                                            old[name]
                                        ), copy.deepcopy(config)
                                        for flag in ("enabled", "disabled"):
                                            old_connection.pop(flag, None)
                                            new_connection.pop(flag, None)
                                        if old_connection != new_connection:
                                            raise ValueError(
                                                "同名 MCP 配置不同；需勾选允许覆盖，或改选来源"
                                            )
                                    if old.get(name) != config:
                                        changes.append(
                                            {
                                                "name": name,
                                                "action": (
                                                    "更新" if name in old else "新增"
                                                ),
                                            }
                                        )
                                    new[name] = config
                                except ValueError as exc:
                                    errors.append(f"{agent.name} / {m['name']}：{exc}")
                            removed = (
                                sorted(set(old) - set(new))
                                if mode == "replace_mcp"
                                else []
                            )
                            changes += [{"name": n, "action": "移除"} for n in removed]
                            if old != new:
                                set_mcp_entries(doc, agent, new)
                                content = dump_document(doc, agent.kind)
                                operations.append(
                                    {
                                        "kind": "config",
                                        "target": str(config_path),
                                        "before": before_hash,
                                        "content": content,
                                    }
                                )
                                rows.append(
                                    {
                                        "agent": agent.name,
                                        "kind": "MCP 配置",
                                        "target": str(config_path),
                                        "action": "更新配置",
                                        "changes": changes,
                                    }
                                )
                                if agent.kind != "codex":
                                    warnings.append(
                                        agent.name
                                        + " 的 JSON 配置会格式化，注释不会保留；原文件将完整备份。"
                                    )
                    if agent.note:
                        warnings.append(agent.name + "：" + agent.note)
                    if skills and not agent.skills:
                        errors.append(agent.name + " 没有配置 Skill 目录；请仅装配 MCP")
                    if agent.skills:
                        for s in skills:
                            slug = s["slug"]
                            if (
                                slug in (".", "..")
                                or Path(slug).name != slug
                                or re.search(r'[<>:"/\\|?*]', slug)
                            ):
                                errors.append("Skill 目录名无法安装：" + slug)
                                continue
                            source, target = s["_path"], agent.skills / slug
                            if digest(source / "SKILL.md") != s["_definition_hash"]:
                                errors.append(
                                    s["name"] + " 的 Skill 定义已变化，请重新扫描"
                                )
                                continue
                            if source.resolve() == target.resolve():
                                continue
                            source_hash = digest(source)
                            target_hash = digest(target)
                            sources[str(source)] = source_hash
                            if source_hash == target_hash:
                                continue
                            if target_hash and not allow:
                                errors.append(
                                    f"{agent.name} / {s['name']}：同名 Skill 内容不同，请勾选允许覆盖或改选版本"
                                )
                                continue
                            operations.append(
                                {
                                    "kind": "skill",
                                    "target": str(target),
                                    "before": target_hash,
                                    "source": str(source),
                                    "source_hash": source_hash,
                                }
                            )
                            rows.append(
                                {
                                    "agent": agent.name,
                                    "kind": "Skill",
                                    "target": str(target),
                                    "action": "替换" if target_hash else "安装",
                                    "name": s["name"],
                                }
                            )
                            if any("插件" in x["label"] for x in s["sources"]):
                                warnings.append(
                                    s["name"]
                                    + " 来自插件目录；仅复制 Skill 文件夹，插件关联 MCP 与变量需另外配置。"
                                )
                except Exception as exc:
                    errors.append(
                        f"{agent.name}：配置或资源无法处理（{type(exc).__name__}）。请检查路径或文件权限。"
                    )
            # A shared Skill directory may be referenced by several adapters.
            unique = {}
            for op in operations:
                key = str(Path(op["target"]).resolve()).casefold()
                if key in unique:
                    other = unique[key]
                    if op.get("content") != other.get("content") or op.get(
                        "source_hash"
                    ) != other.get("source_hash"):
                        errors.append("目标路径重叠且内容不同：" + op["target"])
                else:
                    unique[key] = op
            operations = list(unique.values())
            for op in operations:
                target = Path(op["target"])
                op["identity"] = path_identity(target)
                try:
                    assert_target(target, op["identity"])
                    if (
                        self.catalog.data.resolve() in target.resolve().parents
                        or target.resolve() == self.catalog.data.resolve()
                    ):
                        errors.append("装配目标不能位于工坊自身的数据目录")
                except ValueError as exc:
                    errors.append(str(exc))
            targets = [Path(op["target"]).resolve() for op in operations]
            for i, target in enumerate(targets):
                for other in targets[i + 1 :]:
                    if target in other.parents or other in target.parents:
                        errors.append("目标配置与 Skill 路径互相嵌套，拒绝装配")
            for target in targets:
                for source in sources:
                    original = Path(source).resolve()
                    if target in original.parents or original in target.parents:
                        errors.append("来源与写入目标互相嵌套，拒绝装配")
            plan_id = uuid.uuid4().hex
            if not errors:
                self.pending[plan_id] = {
                    "created": now,
                    "operations": operations,
                    "sources": sources,
                    "name": str(body.get("name", "自由装配"))[:80],
                }
            warnings += [
                "只处理目标的全局 MCP 文件；项目级、插件及云连接器配置仍可能提供其他工具。",
                "Skill 按所选项安装或替换；取消勾选不会卸载既有 Skill。",
                "写入后请重启或重新加载目标 Agent；本软件不启动 MCP，也不验证服务连通。",
            ]
            return {
                "id": plan_id,
                "operations": rows,
                "config_choices": choices,
                "operation_count": len(operations),
                "errors": list(dict.fromkeys(errors)),
                "warnings": list(dict.fromkeys(warnings)),
                "expires_in": 900,
            }

    def apply(self, plan_id):
        identifier(plan_id, "预览编号")
        with self.catalog.lock:
            plan = self.pending.get(plan_id)
            if not plan or time.time() - plan["created"] > 900:
                raise ValueError("预览已过期，请重新预览")
            for source, expected in plan["sources"].items():
                if digest(Path(source)) != expected:
                    raise ValueError(
                        "来源已变化，请重新扫描并预览：" + str(Path(source).name)
                    )
            for op in plan["operations"]:
                p = Path(op["target"])
                assert_target(p, op["identity"])
                if digest(p) != op["before"]:
                    raise ValueError("目标在预览后已变化，请重新扫描并预览：" + p.name)
            if not plan["operations"]:
                self.pending.pop(plan_id, None)
                return {
                    "ok": True,
                    "message": "目标配置已与所选资源一致，无需写入",
                    "transaction": None,
                }
            tx = uuid.uuid4().hex
            folder = self.catalog.data / "backups" / tx
            folder.mkdir(parents=True)
            manifest = {
                "format_version": 2,
                "id": tx,
                "name": plan["name"],
                "created_at": stamp(),
                "status": "preparing",
                "entries": [],
            }
            try:
                for i, op in enumerate(plan["operations"]):
                    target = Path(op["target"])
                    before = folder / f"{i}.before"
                    stage = folder / f"{i}.after"
                    if target.exists():
                        copy_resource(target, before)
                    if digest(before) != op["before"]:
                        raise ValueError("备份校验失败，目标可能已变化")
                    if op["kind"] == "config":
                        stage.write_bytes(op["content"])
                    else:
                        copy_resource(Path(op["source"]), stage)
                        if digest(stage) != op["source_hash"]:
                            raise ValueError("Skill 复制校验失败")
                    manifest["entries"].append(
                        {
                            "target": str(target),
                            "kind": op["kind"],
                            "before": op["before"],
                            "after": digest(stage),
                            "index": i,
                            "applied": False,
                            "phase": "staged",
                            "stage": str(stage),
                            "previous_path": str(
                                target.with_name(
                                    "." + target.name + ".awb-old-" + tx + "-" + str(i)
                                )
                            ),
                            "identity": op["identity"],
                        }
                    )
                for source, expected in plan["sources"].items():
                    if digest(Path(source)) != expected:
                        raise ValueError("来源在准备期间已变化")
                manifest["status"] = "applying"
                write_json(folder / "manifest.json", manifest)
                for entry in manifest["entries"]:
                    target = Path(entry["target"])
                    assert_target(target, entry["identity"])
                    if digest(target) != entry["before"]:
                        raise ValueError("提交前目标已变化：" + target.name)
                    # The journal is flushed before the destination is renamed.
                    entry["applied"] = True
                    entry["phase"] = "renaming-aside"
                    write_json(folder / "manifest.json", manifest)
                    replace_resource(
                        folder / f"{entry['index']}.after",
                        target,
                        entry["after"],
                        entry["before"],
                        entry["identity"],
                        aside=Path(entry["previous_path"]),
                    )
                    entry["phase"] = "committed"
                    write_json(folder / "manifest.json", manifest)
                    self._release_aside(folder, entry, "previous_path")
                manifest["status"] = "applied"
                write_json(folder / "manifest.json", manifest)
            except Exception as failure:
                rollback_errors = []
                manifest["status"] = "restoring"
                for entry in reversed(manifest["entries"]):
                    if not entry.get("applied"):
                        continue
                    try:
                        self._restore_entry(folder, manifest, entry)
                    except Exception:
                        rollback_errors.append(self._recovery_record(entry))
                manifest["status"] = (
                    "recovery_needed" if rollback_errors else "rolled_back"
                )
                manifest["recovery_errors"] = rollback_errors
                manifest["failure_type"] = type(failure).__name__
                write_json(folder / "manifest.json", manifest)
                raise ValueError(
                    "应用失败（"
                    + type(failure).__name__
                    + "），"
                    + (
                        "部分文件需要从备份恢复，请查看备份记录"
                        if rollback_errors
                        else "已回滚本次修改"
                    )
                ) from None
            self.pending.pop(plan_id, None)
            self.catalog.invalidate()
            return {
                "ok": True,
                "message": "装配完成，原文件已备份。请重新加载目标 Agent。",
                "transaction": tx,
            }

    def history(self):
        root = self.catalog.data / "backups"
        result = []
        for p in root.glob("*/manifest.json"):
            try:
                m = read_json(p, {})
                if not isinstance(m, dict) or not isinstance(m.get("entries"), list):
                    raise ValueError()
            except (ValueError, OSError):
                result.append(
                    {
                        "id": p.parent.name,
                        "name": "备份清单无法读取",
                        "status": "unreadable",
                        "entries": [],
                        "created_at": "",
                        "error": "原备份已保留，请检查数据目录。",
                    }
                )
                continue
            result.append(
                {
                    k: m.get(k)
                    for k in (
                        "id",
                        "name",
                        "created_at",
                        "status",
                        "entries",
                        "restored_at",
                        "recovery_errors",
                    )
                }
            )
            result[-1]["restorable"] = m.get("format_version") == 2
        return sorted(result, key=lambda x: x.get("created_at", ""), reverse=True)

    def restore(self, tx):
        identifier(tx, "备份编号")
        with self.catalog.lock:
            folder = self.catalog.data / "backups" / tx
            manifest = read_json(folder / "manifest.json", {})
            if manifest.get("format_version") != 2:
                raise ValueError(
                    "此备份由其他版本生成，请使用生成它的原版本恢复；原文件已保留"
                )
            if manifest.get("status") == "restored":
                for entry in manifest["entries"]:
                    if entry.get("applied"):
                        self._assert_unchanged_since_restore(folder, entry)
                return {"ok": True, "message": "这些文件已经是恢复后的内容"}
            if manifest.get("status") not in (
                "applied",
                "applying",
                "recovery_needed",
                "restoring",
                "preparing",
            ):
                raise ValueError("此记录不可恢复")
            active = [entry for entry in manifest["entries"] if entry.get("applied")]
            # Inspect the whole transaction without moving, deleting or journalling
            # anything. A later invalid entry must not cause an earlier write.
            for entry in active:
                self._preflight_restore(folder, entry)
            manifest["status"] = "restoring"
            write_json(folder / "manifest.json", manifest)
            for entry in reversed(active):
                self._restore_entry(folder, manifest, entry)
            manifest["status"], manifest["restored_at"] = "restored", stamp()
            write_json(folder / "manifest.json", manifest)
            self.catalog.invalidate()
            return {"ok": True, "message": "已恢复到本次应用之前的内容"}

    def _recovery_record(self, entry):
        return {
            "target": str(entry.get("target") or ""),
            "previous_path": str(entry.get("previous_path") or ""),
            "restore_path": str(entry.get("restore_path") or ""),
            "restore_phase": str(entry.get("restore_phase") or ""),
            "stage": str(entry.get("stage") or ""),
            "phase": str(entry.get("phase") or ""),
        }

    def _aside_path(self, folder, entry, field):
        """Only the exact transaction-owned sibling may be moved or removed."""
        target = Path(entry["target"])
        assert_target(target, entry["identity"])
        index = entry["index"]
        if type(index) is not int or not 0 <= index < 10000:
            raise ValueError("备份条目编号无效")
        tag = "old" if field == "previous_path" else "restore"
        expected = target.with_name(f".{target.name}.awb-{tag}-{folder.name}-{index}")
        if not entry.get(field):
            return expected
        aside = Path(entry[field])
        if os.path.normcase(str(aside)) != os.path.normcase(str(expected)):
            raise ValueError("事务暂存路径不匹配，拒绝恢复：" + target.name)
        assert_target(
            aside, os.path.normcase(str(Path(entry["identity"]).parent / expected.name))
        )
        return aside

    def _preflight_restore(self, folder, entry):
        target = Path(entry["target"])
        assert_target(target, entry["identity"])
        retained = {}
        for field, expected in (
            ("previous_path", entry["before"]),
            ("restore_path", entry["after"]),
        ):
            aside = self._aside_path(folder, entry, field)
            # An unrecorded path is never considered recovery evidence.
            if not entry.get(field):
                if aside.exists() or is_link(aside):
                    raise ValueError("事务暂存路径已被占用：" + aside.name)
                continue
            actual = digest(aside)
            if actual is not None:
                if actual != expected:
                    raise ValueError("事务暂存内容又被修改，恢复已停止：" + aside.name)
                retained[field] = actual
        backup = folder / f"{entry['index']}.before"
        if entry["before"] is not None and (
            is_link(backup) or digest(backup) != entry["before"]
        ):
            raise ValueError("备份校验失败，拒绝恢复")
        current = digest(target)
        if current not in (entry["after"], entry["before"]):
            interrupted = current is None and (
                (
                    entry.get("phase") == "renaming-aside"
                    and entry.get("restore_phase") != "committed"
                    and "previous_path" in retained
                )
                or (
                    entry.get("restore_phase") == "prepared"
                    and "restore_path" in retained
                )
            )
            if not interrupted:
                raise ValueError(
                    "应用后文件又被修改，恢复已停止以保留新内容：" + target.name
                )
        return current

    def _release_aside(self, folder, entry, field):
        aside = self._aside_path(folder, entry, field)
        if not entry.get(field) or not aside.exists():
            return
        expected = entry["before"] if field == "previous_path" else entry["after"]
        if digest(aside) != expected:
            raise ValueError("事务暂存内容又被修改，已保留：" + aside.name)
        remove_owned(aside)

    def _assert_unchanged_since_restore(self, folder, entry):
        target = Path(entry["target"])
        assert_target(target, entry["identity"])
        if digest(target) != entry["before"]:
            raise ValueError(
                "应用后文件又被修改，恢复已停止以保留新内容：" + target.name
            )

    def _restore_entry(self, folder, manifest, entry):
        current = self._preflight_restore(folder, entry)
        if current == entry["before"]:
            # The last rename may have finished before its journal update.
            entry["restore_phase"] = "committed"
        else:
            target = Path(entry["target"])
            aside = self._aside_path(folder, entry, "restore_path")
            entry["restore_path"] = str(aside)
            entry["restore_phase"] = "prepared"
            # Persist a known path BEFORE moving the applied destination away.
            write_json(folder / "manifest.json", manifest)
            self._preflight_restore(folder, entry)
            if entry["before"] is None:
                if aside.exists():
                    raise ValueError("事务暂存路径已被占用：" + aside.name)
                os.replace(target, aside)
            else:
                replace_resource(
                    folder / f"{entry['index']}.before",
                    target,
                    entry["before"],
                    current,
                    entry["identity"],
                    aside=aside,
                )
            entry["restore_phase"] = "committed"
        write_json(folder / "manifest.json", manifest)
        self._release_aside(folder, entry, "previous_path")
        self._release_aside(folder, entry, "restore_path")
