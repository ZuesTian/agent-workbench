"""Reversible task organization, independent of immutable execution evidence."""

import copy
import uuid

from .storage import read_json, write_json, stamp
from .validation import identifier, text


class TaskLibrary:
    def __init__(self, data):
        self.path = data / "task-library.json"

    def read(self):
        value = read_json(self.path, {"groups": [], "records": {}})
        if not isinstance(value, dict) or not isinstance(value.get("groups"), list) or not isinstance(value.get("records"), dict):
            raise ValueError("任务库索引格式损坏，请从备份恢复")
        return value

    def metadata(self, kind, ident):
        return copy.deepcopy(self.read()["records"].get(f"{kind}:{ident}", {}))

    def decorate(self, kind, rows, scope="active", group=""):
        data = self.read()
        result = []
        for row in rows:
            meta = data["records"].get(f"{kind}:{row['id']}", {})
            owner = row.get("workflow_id")
            if owner:
                meta = data["records"].get(f"workflow:{owner}", data["records"].get(f"conversation:{owner}", meta))
            location = "trash" if meta.get("deleted_at") else "archived" if meta.get("archived") else "active"
            if (scope == "all" or scope == location) and (not group or meta.get("group_id", "") == group):
                result.append({**row, **meta, "location": location})
        return result

    def update(self, kind, ident, body):
        if kind not in {"run", "workflow", "conversation"}:
            raise ValueError("任务类型不正确")
        identifier(ident, "任务编号")
        data = self.read()
        meta = data["records"].setdefault(f"{kind}:{ident}", {})
        action = body.get("action")
        if action == "group":
            group = body.get("group_id", "")
            if group and not any(g["id"] == group for g in data["groups"]):
                raise ValueError("分组不存在")
            meta["group_id"] = group
        elif action in {"archive", "unarchive"}:
            meta["archived"] = action == "archive"
        elif action == "delete":
            meta["deleted_at"] = stamp()
        elif action == "restore":
            meta.pop("deleted_at", None)
            meta["archived"] = False
        else:
            raise ValueError("不支持的任务操作")
        write_json(self.path, data)
        return {"id": ident, **meta}

    def group(self, body):
        data = self.read()
        ident = body.get("id")
        if body.get("action") == "delete":
            identifier(ident, "分组编号")
            data["groups"] = [g for g in data["groups"] if g["id"] != ident]
            for record in data["records"].values():
                if record.get("group_id") == ident:
                    record["group_id"] = ""
        else:
            name = text(body.get("name"), "分组名称", 60, required=True)
            if any(g["name"].casefold() == name.casefold() and g["id"] != ident for g in data["groups"]):
                raise ValueError("分组名称已存在")
            if ident:
                group = next((g for g in data["groups"] if g["id"] == ident), None)
                if group is None:
                    raise ValueError("分组不存在")
                group["name"] = name
            else:
                if len(data["groups"]) >= 100:
                    raise ValueError("最多创建 100 个分组")
                ident = uuid.uuid4().hex
                data["groups"].append({"id": ident, "name": name})
        write_json(self.path, data)
        return {"groups": data["groups"], "id": ident}
