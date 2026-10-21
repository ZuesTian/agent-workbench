"""Build a self-contained Windows launcher; never package user configuration."""

import hashlib
import json
import subprocess
import sys
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw
from agent_workbench import __version__

root = Path(__file__).resolve().parent

readme = (root / "README.md").read_text(encoding="utf-8").splitlines()[0]
views = (root / "agent_workbench" / "static" / "views.js").read_text(encoding="utf-8")
if __version__ not in readme or f"Agent 工坊 · {__version__}" not in views:
    raise SystemExit(f"版本号不一致：源码 {__version__}，README 或界面未同步")

# PyInstaller cleans these directories; never allow a redirected output outside the project.
for directory in (root / "build", root / "dist", root / "dist/AgentWorkbench"):
    if root not in directory.resolve().parents:
        raise RuntimeError("Build output must remain inside the project")
assets = root / "assets"
assets.mkdir(exist_ok=True)
im = Image.new("RGBA", (256, 256))
draw = ImageDraw.Draw(im)
draw.rounded_rectangle((0, 0, 255, 255), radius=60, fill="#303030")
for box in ((96, 52, 160, 100), (40, 156, 100, 204), (156, 156, 216, 204)):
    draw.rounded_rectangle(box, radius=12, outline="#ececec", width=13)
draw.line([(128, 100), (128, 128)], fill="#ececec", width=13)
draw.line([(70, 156), (70, 128), (186, 128), (186, 156)], fill="#ececec", width=13)
ico = assets / "app.ico"
im.save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
numbers = tuple(int(part) if part.isdigit() else 0 for part in (__version__.split(".") + ["0", "0", "0", "0"])[:4])
version_file = root / "packaging" / "version-info.txt"
version_file.parent.mkdir(exist_ok=True)
version_file.write_text(
    "\n".join([
        "VSVersionInfo(",
        "  ffi=FixedFileInfo(",
        f"    filevers={numbers},",
        f"    prodvers={numbers},",
        "    mask=0x3f,",
        "    flags=0x0,",
        "    OS=0x40004,",
        "    fileType=0x1,",
        "    subtype=0x0,",
        "    date=(0, 0)",
        "  ),",
        "  kids=[",
        "    StringFileInfo([StringTable('040904B0', [",
        "      StringStruct('CompanyName', 'Agent Workbench'),",
        f"      StringStruct('FileVersion', '{__version__}'),",
        "      StringStruct('ProductName', 'Agent Workbench'),",
        f"      StringStruct('ProductVersion', '{__version__}')",
        "    ])]),",
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])])",
        "  ]",
        ")",
        "",
    ]),
    encoding="utf-8",
)
command = [
    sys.executable,
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--windowed",
    "--name",
    "AgentWorkbench",
    "--distpath",
    str(root / "dist"),
    "--workpath",
    str(root / "build"),
    "--icon",
    str(ico),
    "--version-file",
    str(version_file),
    "--add-data",
    str(root / "agent_workbench/static") + ";agent_workbench/static",
    "--add-data",
    str(ico) + ";assets",
    "--exclude-module",
    "numpy",
    "--exclude-module",
    "pandas",
    "--exclude-module",
    "matplotlib",
    "--exclude-module",
    "torch",
    "--exclude-module",
    "tkinter",
    str(root / "launcher.py"),
]
subprocess.run(command, cwd=root, check=True)
distribution = root / "dist/AgentWorkbench"
shutil.copy2(root / "README.md", distribution / "README.md")
shutil.copy2(root / "EXECUTION.md", distribution / "EXECUTION.md")
shutil.copytree(root / "examples", distribution / "examples")
archive = root / f"AgentWorkbench-{__version__}-Windows.zip"
temporary = archive.with_suffix(".zip.tmp")
try:
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(distribution.rglob("*")):
            if path.is_file():
                bundle.write(
                    path, Path("AgentWorkbench") / path.relative_to(distribution)
                )
    with zipfile.ZipFile(temporary) as bundle:
        if bundle.testzip() is not None:
            raise RuntimeError("Distribution archive failed verification")
    temporary.replace(archive)
finally:
    temporary.unlink(missing_ok=True)

verification = root / "releases" / __version__ / "verification"
verification.mkdir(parents=True, exist_ok=True)
manifest_files = []
for base in (root / "agent_workbench", root / "tests"):
    for path in sorted(base.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            manifest_files.append(path)
for name in (
    "launcher.py", "build_windows.py", "README.md", "EXECUTION.md", "UI-REVIEW.md",
    "requirements.txt", "requirements-dev.txt", "package.json", "package-lock.json",
    "pytest.ini", ".gitignore", ".prettierrc.json", ".github/workflows/ci.yml",
):
    manifest_files.append(root / name)
source_rows = [
    {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    for path in manifest_files
]
release_rows = []
for path in sorted(distribution.rglob("*")):
    if path.is_file():
        release_rows.append(
            {
                "path": path.relative_to(distribution).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
exe = distribution / "AgentWorkbench.exe"
record = {
    "version": __version__,
    "built_at": datetime.now(timezone.utc).isoformat(),
    "exe_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
    "zip_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    "zip": archive.name,
    "source_files": source_rows,
    "distribution_files": release_rows,
    "checks": {
        "entry_located": "not run by the builder",
        "protocol_tests": "not run by the builder",
        "live_client_calls": "not run in this build",
    },
}
if (root / ".git").is_dir():
    record["git_commit"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, encoding="utf-8"
    ).strip()
    record["git_dirty"] = bool(subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=root, text=True, encoding="utf-8"
    ).strip())
(verification / "release.json").write_text(
    json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
)
sums = verification / "SHA256SUMS.txt"
lines = [f"{record['exe_sha256']}  AgentWorkbench.exe", f"{record['zip_sha256']}  {archive.name}"]
lines += [f"{row['sha256']}  {row['path']}" for row in source_rows]
sums.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("Built", exe)
print("Archive", archive)
