"""Build an isolated execution preview without replacing the main distribution."""

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def main():
    project = Path(__file__).resolve().parent
    release = project / "releases" / ("execution-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    source = release / "source"
    source.mkdir(parents=True, exist_ok=False)
    for name in ("agent_workbench", "examples"):
        shutil.copytree(
            project / name, source / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    for name in ("launcher.py", "build_windows.py", "requirements.txt", "README.md", "EXECUTION.md"):
        shutil.copy2(project / name, source / name)
    # A separate version prevents the new launcher from silently reusing an old
    # already-running service. The user's current source/version is untouched.
    (source / "agent_workbench/__init__.py").write_text(
        '__version__ = "0.4.0-execution-preview"\n', encoding="utf-8"
    )
    views = source / "agent_workbench/static/views.js"
    content = views.read_text(encoding="utf-8")
    import re
    content = re.sub(
        r"AGENT WORKBENCH · [0-9.]+", "AGENT WORKBENCH · 0.4 执行预览", content
    )
    views.write_text(content, encoding="utf-8")
    launcher = source / "launcher.py"
    content = launcher.read_text(encoding="utf-8")
    content = content.replace(
        'from agent_workbench.server import WorkbenchServer',
        'from agent_workbench.server import WorkbenchServer\nfrom agent_workbench.catalog import Catalog',
    )
    content = content.replace('/ "AgentWorkbench"', '/ "AgentWorkbenchExecutionPreview"')
    content = content.replace(
        'Local\\\\AgentWorkbench.', 'Local\\\\AgentWorkbench.ExecutionPreview.'
    )
    content = content.replace(
        'WorkbenchServer(("127.0.0.1", 47831), token=token)',
        'WorkbenchServer(("127.0.0.1", 47831), catalog=Catalog(data=data), token=token)',
    )
    content = content.replace(
        'WorkbenchServer(("127.0.0.1", 0), token=token)',
        'WorkbenchServer(("127.0.0.1", 0), catalog=Catalog(data=data), token=token)',
    )
    if content.count("catalog=Catalog(data=data)") != 2 or "ExecutionPreview.Service" not in content:
        raise RuntimeError("Preview launcher isolation could not be applied")
    launcher.write_text(content, encoding="utf-8")
    state_file = source / "agent_workbench/static/state.js"
    state_file.write_text(
        state_file.read_text(encoding="utf-8").replace("tab: 'overview'", "tab: 'execution'"),
        encoding="utf-8",
    )
    readme = source / "README.md"
    readme.write_text(
        "# Agent 工坊：执行预览版\n\n"
        "双击 `AgentWorkbench.exe` 后进入执行页。此独立包的个人数据存放在 "
        "`%LOCALAPPDATA%\\AgentWorkbenchExecutionPreview`，可以与原工坊同时打开；"
        "原工坊的装配方案和协作方案不会自动复制。\n\n"
        + readme.read_text(encoding="utf-8").replace(
            "%LOCALAPPDATA%\\AgentWorkbench", "%LOCALAPPDATA%\\AgentWorkbenchExecutionPreview"
        ), encoding="utf-8"
    )
    guide = source / "EXECUTION.md"
    guide.write_text(
        guide.read_text(encoding="utf-8").replace(
            "%LOCALAPPDATA%\\AgentWorkbench", "%LOCALAPPDATA%\\AgentWorkbenchExecutionPreview"
        ), encoding="utf-8",
    )
    manifest = {
        str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source.rglob("*") if p.is_file()
    }
    subprocess.run([sys.executable, str(source / "build_windows.py")], cwd=source, check=True)
    dist = source / "dist/AgentWorkbench"
    shutil.copy2(source / "EXECUTION.md", dist / "EXECUTION.md")
    # Rebuild the archive to include the execution guide.
    archive = release / "AgentWorkbench-Execution-Windows.zip"
    import zipfile
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(dist.rglob("*")):
            if path.is_file():
                bundle.write(path, Path("AgentWorkbench") / path.relative_to(dist))
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise RuntimeError("Execution preview archive failed verification")
    result = {
        "exe": str(dist / "AgentWorkbench.exe"),
        "zip": str(archive),
        "zip_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "source_sha256": manifest,
    }
    (release / "build.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "source_sha256"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
