"""Inventory literal Cordis MCP definitions without evaluating YAML code tags."""

import yaml


class Expression:
    pass


class LiteralLoader(yaml.SafeLoader):
    pass


LiteralLoader.add_constructor(None, lambda loader, node: Expression())


def _dynamic(value, seen=None):
    if isinstance(value, Expression):
        return True
    seen = set() if seen is None else seen
    if isinstance(value, (dict, list)):
        if id(value) in seen:
            return True
        seen.add(id(value))
    if isinstance(value, dict):
        return any(_dynamic(v, seen) or _dynamic(k, seen) for k, v in value.items())
    if isinstance(value, list):
        return any(_dynamic(v, seen) for v in value)
    return False


def read_static_mcps(text):
    document = yaml.load(text, Loader=LiteralLoader)
    entries, issues = {}, []
    # YAML aliases may be cyclic; visit each collection once.
    visited = set()

    def walk(value):
        if not isinstance(value, (dict, list)) or id(value) in visited:
            return
        visited.add(id(value))
        if isinstance(value, dict):
            if value.get("name") == "@deepseek-ai/dsh-mcp-client":
                raw = value.get("config")
                if isinstance(raw, dict):
                    if _dynamic(raw) or _dynamic(value.get("disabled")):
                        issues.append(
                            "有一项 Harness MCP 含动态 YAML 表达式，未求值且未导入。"
                        )
                    else:
                        name, transport = raw.get("serverName"), raw.get("transport")
                        if isinstance(name, str) and transport in (
                            "stdio",
                            "streamable-http",
                        ):
                            out = {
                                k: v
                                for k, v in raw.items()
                                if k not in ("serverName", "transport")
                            }
                            out["type"] = "stdio" if transport == "stdio" else "http"
                            out["enabled"] = not bool(value.get("disabled", False))
                            key = name
                            if key in entries:
                                key = name + "@" + str(value.get("id", len(entries)))
                            entries[key] = out
            for item in value.values():
                walk(item)
        else:
            for item in value:
                walk(item)

    walk(document)
    return {"mcpServers": entries, "_scan_issues": issues}
