from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_NAME = "release-manifest.json"
PRODUCT_NAME = "AI Customer Desktop"


def _safe_release_path(value: object) -> str:
    path = str(value or "").replace("\\", "/")
    pure_path = PurePosixPath(path)
    if not path or pure_path.is_absolute() or ".." in pure_path.parts or "." in pure_path.parts:
        raise RuntimeError(f"发布包包含不安全的文件路径：{path}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_verified_release(release_root: Path) -> tuple[str, dict[str, dict[str, Any]]]:
    manifest_path = release_root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise RuntimeError("发布包缺少 release-manifest.json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    version = str(payload.get("version") or "").strip()
    if payload.get("format") != 1 or payload.get("product") != PRODUCT_NAME or not VERSION_PATTERN.fullmatch(version):
        raise RuntimeError("发布包版本信息无效")

    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise RuntimeError("发布包清单为空")

    files: dict[str, dict[str, Any]] = {}
    for item in raw_files:
        if not isinstance(item, dict):
            raise RuntimeError("发布包清单格式无效")
        relative_path = _safe_release_path(item.get("path"))
        size = item.get("size")
        sha256 = str(item.get("sha256") or "").lower()
        if relative_path in files or not isinstance(size, int) or size < 0 or not SHA256_PATTERN.fullmatch(sha256):
            raise RuntimeError("发布包清单格式无效")
        source = release_root / Path(*PurePosixPath(relative_path).parts)
        if not source.is_file():
            raise RuntimeError(f"发布包文件缺失：{relative_path}")
        if source.stat().st_size != size or _sha256(source) != sha256:
            raise RuntimeError(f"发布包校验失败：{relative_path}")
        files[relative_path] = {"source": source}

    if "AI_Customer.exe" not in files or "app/AI_Customer.exe" not in files:
        raise RuntimeError("发布包缺少启动程序")
    return version, files


def current_version(install_root: Path) -> str:
    manifest_path = install_root / "current-version.json"
    if not manifest_path.is_file():
        raise RuntimeError("未找到当前版本信息，请双击发布包根目录的 AI_Customer.exe 安装")
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


def default_install_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("未找到 Windows 本地应用数据目录")
    return Path(local_app_data) / "AI_Customer"


def install_release(release_root: Path, install_root: Path) -> str:
    """Install only manifest-declared payload files and retain persistent data."""
    release_root = release_root.resolve()
    version, files = _read_verified_release(release_root)
    versions_root = install_root / "versions"
    target_version = versions_root / version
    install_root.mkdir(parents=True, exist_ok=True)
    (install_root / "data").mkdir(exist_ok=True)
    versions_root.mkdir(exist_ok=True)

    if target_version.exists():
        version_executable(install_root, version)
    else:
        staging_version = versions_root / f"{version}.staging-{os.getpid()}"
        if staging_version.exists():
            raise RuntimeError("发现未完成的安装目录，请关闭程序后重试")
        for relative_path, item in files.items():
            if not relative_path.startswith("app/"):
                continue
            destination = staging_version / Path(*PurePosixPath(relative_path).parts[1:])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item["source"], destination)
        if not (staging_version / "AI_Customer.exe").is_file():
            raise RuntimeError("发布包缺少应用程序")
        staging_version.replace(target_version)

    # Keep the stable launcher separate from versioned application files.
    temporary_launcher = install_root / "AI_Customer.next.exe"
    shutil.copy2(files["AI_Customer.exe"]["source"], temporary_launcher)
    temporary_launcher.replace(install_root / "AI_Customer.exe")
    current = {
        "version": version,
        "switched_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    temporary_current = install_root / "current-version.next.json"
    temporary_current.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_current.replace(install_root / "current-version.json")
    return version


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
    source_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    try:
        if (source_root / MANIFEST_NAME).is_file():
            install_release(source_root, default_install_root())
            launch_current(default_install_root())
        else:
            launch_current(source_root)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        _show_error(str(exc))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
