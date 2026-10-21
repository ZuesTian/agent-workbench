"""Isolated browser-test server. All writable targets are under .test-artifacts."""

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_workbench.catalog import Catalog
from agent_workbench.storage import write_json
from agent_workbench.server import WorkbenchServer

root = (
    Path(__file__).resolve().parents[1]
    / ".test-artifacts"
    / ("ui-" + uuid.uuid4().hex[:8])
)
home, roaming, data = root / "home", root / "roaming", root / "data"
(home / ".codex").mkdir(parents=True)
(home / ".codex/config.toml").write_text(
    '# fixture configuration\nmodel = "unchanged"\n', encoding="utf-8"
)
(home / ".cursor").mkdir(parents=True)
(home / ".cursor/mcp.json").write_text(
    json.dumps(
        {
            "mcpServers": {
                "lab-tools": {
                    "command": "python",
                    "args": ["example.py"],
                    "env": {"LAB_TOKEN": "fixture-private-value"},
                }
            }
        }
    ),
    encoding="utf-8",
)
skill = home / ".cursor/skills/lab-notes"
(skill / "references").mkdir(parents=True)
(skill / "SKILL.md").write_text(
    "---\nname: lab-notes\ndescription: 整理实验记录与参考资料。\n---\nUse references/check.txt.\n",
    encoding="utf-8",
)
(skill / "references/check.txt").write_text("reference preserved", encoding="utf-8")
programs = root / "portable"
programs.mkdir()
for filename in ("Codex.exe", "Cursor.exe", "ZCode.exe", "DeepSeek Harness.exe"):
    (programs / filename).write_bytes(b"fixture-only-never-execute")
write_json(
    data / "settings.json",
    {"roots": [], "agents": [], "install_roots": [str(programs)]},
)
# A removed client retains reusable configuration, but no program evidence.
(home / ".claude").mkdir()
(home / ".claude.json").write_text('{"mcpServers": {}}', encoding="utf-8")
write_json(
    home / ".zcode/cli/config.json",
    {"mcp": {"servers": {}}, "provider": {"id": "fixture"}},
)
server = WorkbenchServer(
    ("127.0.0.1", 47832), Catalog(home, roaming, data), "ui-fixture-session"
)
write_json(
    Path(__file__).resolve().parents[1] / ".test-artifacts/ui-runtime.json",
    {"root": str(root), "port": server.server_port},
)
print("Isolated UI fixture ready", flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
