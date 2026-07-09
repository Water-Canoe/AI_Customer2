from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


def current_version(install_root: Path) -> str:
    manifest_path = install_root / "current-version.json"
    if not manifest_path.is_file():
        raise RuntimeError("未找到当前版本信息，请先安装发布包")
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    version = str(payload.get("version") or "").strip()
    if not VERSION_PATTERN.fullmatch(version):
        raise RuntimeError("当前版本信息格式不正确")
    return version


def version_executable(install_root: Path, version: str) -> Path:
    executable = install_root / "versions" / version / "AI_Customer.exe"
    if not executable.is_file():
        raise RuntimeError(f"版本 {version} 的程序文件不完整，请重新安装该版本")
    return executable


def launch_current(install_root: Path) -> Path:
    executable = version_executable(install_root, current_version(install_root))
    subprocess.Popen(
        [str(executable)],
        cwd=str(executable.parent),
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    return executable


def _show_error(message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "AI拓客工具", 0x10)
    except Exception:
        print(message)


def main() -> None:
    install_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    try:
        launch_current(install_root)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        _show_error(str(exc))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
