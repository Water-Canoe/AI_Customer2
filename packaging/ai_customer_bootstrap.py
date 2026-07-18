from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app import device_identity


VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_NAME = "release-manifest.json"
PRODUCT_NAME = "AI Customer Desktop"
UPDATER_VERSION = "1.0.0"
MAX_EXTRACTED_SIZE = 8 * 1024 * 1024 * 1024
UPDATE_CHECK_ENDPOINT = "https://tfwqsfaegbdj.sealosbja.site/ai-customer/update/check"
TRUSTED_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA2OKYXun9fsGm/ymzAdD7n9hhzusRKVvPS87myTqkdvg=
-----END PUBLIC KEY-----
"""


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
    environment_version = str(payload.get("environment_version") or "").strip()
    if (
        payload.get("format") != 1
        or payload.get("product") != PRODUCT_NAME
        or payload.get("entrypoint") != "AI_Customer_App.exe"
        or not VERSION_PATTERN.fullmatch(version)
        or not VERSION_PATTERN.fullmatch(environment_version)
    ):
        raise RuntimeError("发布包版本信息无效")

    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise RuntimeError("发布包清单为空")

    files: dict[str, dict[str, Any]] = {}
    for item in raw_files:
        if not isinstance(item, dict):
            raise RuntimeError("发布包清单格式无效")
        relative_path = _safe_release_path(item.get("path"))
        if not _is_program_owned_path(relative_path):
            raise RuntimeError(f"发布包包含非程序文件：{relative_path}")
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

    if "AI_Customer_App.exe" not in files:
        raise RuntimeError("程序包缺少 AI_Customer_App.exe")
    return version, files


def _is_program_owned_path(relative_path: str) -> bool:
    """Keep remote updates away from customer data and dependency files."""
    if relative_path in {"AI_Customer.exe", "AI_Customer_App.exe", "README.txt"}:
        return True
    return relative_path.startswith(("runtime/frontend_dist/", "runtime/app/"))


def _is_newer_version(candidate: str, current: str) -> bool:
    if not VERSION_PATTERN.fullmatch(candidate) or not VERSION_PATTERN.fullmatch(current):
        raise RuntimeError("更新版本信息格式不正确")
    candidate_core, _, candidate_suffix = candidate.partition("-")
    current_core, _, current_suffix = current.partition("-")
    candidate_numbers = tuple(int(part) for part in candidate_core.split("."))
    current_numbers = tuple(int(part) for part in current_core.split("."))
    if candidate_numbers != current_numbers:
        return candidate_numbers > current_numbers
    if not candidate_suffix or not current_suffix:
        return bool(not candidate_suffix and current_suffix)
    return candidate_suffix > current_suffix


def _read_update_identity(install_root: Path) -> tuple[str, str] | None:
    database_path = install_root / "data" / "ai_customer.sqlite3"
    if not database_path.is_file():
        return None
    with sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT key, value FROM settings WHERE key = ?",
            ("license_code",),
        ).fetchall()
    values = {str(key): str(value).strip() for key, value in rows}
    license_code = values.get("license_code", "")
    device_code = device_identity.get_device_code()
    return (license_code, device_code) if license_code and device_code else None


def _request_update_offer(identity: tuple[str, str], current: str) -> dict[str, Any] | None:
    license_code, device_code = identity
    endpoint = os.environ.get("AI_CUSTOMER_UPDATE_ENDPOINT", UPDATE_CHECK_ENDPOINT).strip()
    request_body = json.dumps(
        {
            "licenseCode": license_code,
            "deviceId": device_code,
            "currentVersion": current,
            "updaterVersion": UPDATER_VERSION,
            "channel": "stable",
            "platform": "windows",
            "arch": "x64",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(endpoint, data=request_body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("code") != 200:
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def _validate_update_offer(offer: dict[str, Any], current: str, environment_version: str) -> dict[str, Any] | None:
    if not offer.get("available") or not offer.get("downloadAllowed"):
        return None
    manifest_text = offer.get("manifestText")
    signature_text = offer.get("signature")
    if not isinstance(manifest_text, str) or not isinstance(signature_text, str):
        raise RuntimeError("更新签名信息不完整")
    signature = base64.b64decode(signature_text, validate=True)
    if len(signature) != 64:
        raise RuntimeError("更新签名长度无效")
    public_key = serialization.load_pem_public_key(TRUSTED_PUBLIC_KEY_PEM)
    if not isinstance(public_key, Ed25519PublicKey):
        raise RuntimeError("更新公钥格式无效")
    public_key.verify(signature, manifest_text.encode("utf-8"))
    manifest = json.loads(manifest_text)
    if not isinstance(manifest, dict):
        raise RuntimeError("更新清单格式无效")
    version = str(manifest.get("version") or "")
    if (
        manifest.get("format") != 1
        or manifest.get("product") != PRODUCT_NAME
        or manifest.get("platform") != "windows"
        or manifest.get("arch") != "x64"
        or str(manifest.get("min_updater_version") or "") != UPDATER_VERSION
        or str(manifest.get("environment_version") or "") != environment_version
        or str(offer.get("version") or "") != version
        or not _is_newer_version(version, current)
    ):
        raise RuntimeError("更新清单不适用于当前程序")
    package = manifest.get("package")
    if not isinstance(package, dict):
        raise RuntimeError("更新包信息无效")
    size = package.get("size")
    sha256 = str(package.get("sha256") or "").lower()
    download_url = str(offer.get("downloadUrl") or "")
    if not isinstance(size, int) or size <= 0 or not SHA256_PATTERN.fullmatch(sha256) or not download_url.startswith("https://"):
        raise RuntimeError("更新包信息无效")
    return {
        "version": version,
        "size": size,
        "sha256": sha256,
        "download_url": download_url,
        "mandatory": bool(offer.get("mandatory")),
        "notes": str(offer.get("notes") or "").strip()[:1000],
    }


def _download_update(
    update: dict[str, Any],
    install_root: Path,
    progress: Callable[[str, int], None] | None = None,
) -> Path:
    updates_root = install_root / "updates"
    updates_root.mkdir(exist_ok=True)
    archive_path = updates_root / f"{str(update.get('version'))}.zip"
    request = urllib.request.Request(str(update["download_url"]), headers={"Accept": "application/zip"})
    digest = hashlib.sha256()
    received = 0
    if progress:
        progress("正在下载程序更新", 0)
    with urllib.request.urlopen(request, timeout=30) as response, archive_path.open("wb") as output:
        for block in iter(lambda: response.read(1024 * 1024), b""):
            received += len(block)
            if received > int(update["size"]):
                raise RuntimeError("下载的更新包大小异常")
            digest.update(block)
            output.write(block)
            if progress:
                progress(f"正在下载程序更新  {received / 1024 / 1024:.1f} / {int(update['size']) / 1024 / 1024:.1f} MB", min(80, int(received * 80 / int(update["size"]))))
    if received != int(update["size"]) or digest.hexdigest() != update["sha256"]:
        raise RuntimeError("下载的更新包校验失败")
    if progress:
        progress("更新包下载完成，正在校验", 82)
    return archive_path


def _extract_update(
    archive_path: Path,
    install_root: Path,
    version: str,
    progress: Callable[[str, int], None] | None = None,
) -> Path:
    release_root = install_root / "updates" / version
    if release_root.is_dir():
        if progress:
            progress("正在检查已下载的程序文件", 95)
        return release_root
    with zipfile.ZipFile(archive_path) as archive:
        names: set[str] = set()
        files: list[tuple[zipfile.ZipInfo, str]] = []
        total_size = 0
        for item in archive.infolist():
            if item.is_dir():
                continue
            relative_path = _safe_release_path(item.filename)
            unix_mode = (item.external_attr >> 16) & 0o170000
            total_size += int(item.file_size)
            if relative_path in names or unix_mode == 0o120000 or total_size > MAX_EXTRACTED_SIZE:
                raise RuntimeError("更新包包含重复链接或解压体积异常")
            names.add(relative_path)
            files.append((item, relative_path))
        extracted = 0
        for item, relative_path in files:
            destination = release_root / Path(*PurePosixPath(relative_path).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
            extracted += int(item.file_size)
            if progress:
                progress("正在解压程序更新", 82 + min(13, int(extracted * 13 / max(total_size, 1))))
    return release_root


def apply_remote_update(
    install_root: Path,
    *,
    confirm: Callable[[dict[str, Any]], bool] | None = None,
    progress: Callable[[str, int], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    check_feedback: Callable[[str], None] | None = None,
) -> bool:
    """Check automatically, then update only after explicit user confirmation."""
    confirmed = False
    update: dict[str, Any] | None = None
    try:
        current = current_version(install_root)
        environment_version = validate_environment(install_root)
        identity = _read_update_identity(install_root)
        if not identity:
            if check_feedback:
                check_feedback("请先完成产品授权，再检查更新。")
            return False
        offer = _request_update_offer(identity, current)
        if not offer:
            if check_feedback:
                check_feedback("更新服务未返回有效结果，请稍后重试。")
            return False
        update = _validate_update_offer(offer, current, environment_version)
        if not update:
            if check_feedback:
                check_feedback("当前已是最新版本。")
            return False
        if confirm and not confirm(update):
            return False
        confirmed = True
        archive_path = _download_update(update, install_root, progress)
        release_root = _extract_update(archive_path, install_root, str(update["version"]), progress)
        apply_release(release_root, install_root, progress)
        return True
    except (InvalidSignature, OSError, RuntimeError, ValueError, sqlite3.Error, urllib.error.URLError, zipfile.BadZipFile) as exc:
        if confirmed and update and update.get("mandatory"):
            raise RuntimeError(f"必须更新失败，无法继续启动。\n\n{exc}") from exc
        if confirmed and on_error:
            on_error(str(exc))
        elif check_feedback:
            check_feedback(f"检查更新失败，请稍后重试。\n\n{exc}")
        return False


def current_version(install_root: Path) -> str:
    manifest_path = install_root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise RuntimeError("程序目录缺少 release-manifest.json，请重新解压完整程序包")
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    version = str(payload.get("version") or "").strip()
    environment_version = str(payload.get("environment_version") or "").strip()
    if (
        payload.get("format") != 1
        or payload.get("product") != PRODUCT_NAME
        or payload.get("entrypoint") != "AI_Customer_App.exe"
        or not VERSION_PATTERN.fullmatch(version)
        or not VERSION_PATTERN.fullmatch(environment_version)
    ):
        raise RuntimeError("当前版本信息格式不正确")
    return version


def application_executable(install_root: Path) -> Path:
    executable = install_root / "AI_Customer_App.exe"
    if not executable.is_file():
        raise RuntimeError("程序目录缺少 AI_Customer_App.exe，请重新解压完整程序包")
    return executable


def apply_release(
    release_root: Path,
    install_root: Path,
    progress: Callable[[str, int], None] | None = None,
) -> str:
    """Replace only program-owned files inside the portable directory."""
    release_root = release_root.resolve()
    version, files = _read_verified_release(release_root)
    install_root.mkdir(parents=True, exist_ok=True)
    (install_root / "data").mkdir(exist_ok=True)
    ordered_files = sorted(files.items(), key=lambda value: value[0] == "AI_Customer_App.exe")
    for index, (relative_path, item) in enumerate(ordered_files, start=1):
        if relative_path == "AI_Customer.exe":
            continue
        destination = install_root / Path(*PurePosixPath(relative_path).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f"{destination.name}.next-{os.getpid()}")
        shutil.copy2(item["source"], temporary)
        temporary.replace(destination)
        if progress:
            progress("正在安装程序更新", 95 + min(4, int(index * 4 / len(ordered_files))))
    manifest_source = release_root / MANIFEST_NAME
    manifest_temporary = install_root / f"{MANIFEST_NAME}.next-{os.getpid()}"
    shutil.copy2(manifest_source, manifest_temporary)
    manifest_temporary.replace(install_root / MANIFEST_NAME)
    if progress:
        progress("更新完成，即将启动", 100)
    return version


def validate_environment(install_root: Path) -> str:
    program = json.loads((install_root / MANIFEST_NAME).read_text(encoding="utf-8-sig"))
    expected = str(program.get("environment_version") or "")
    manifest_path = install_root / "runtime" / "environment-manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("运行环境未安装，请把环境 ZIP 中的 runtime 文件夹解压到程序目录")
    environment = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if environment.get("format") != 1 or environment.get("product") != "AI Customer Environment":
        raise RuntimeError("环境包清单无效，请重新解压完整环境包")
    version = str(environment.get("version") or "")
    if not VERSION_PATTERN.fullmatch(version) or version != expected:
        raise RuntimeError(f"程序需要环境包 {expected}，当前环境包为 {version or '未知版本'}")
    required_paths = environment.get("required_paths")
    if not isinstance(required_paths, list) or not required_paths:
        raise RuntimeError("环境包清单缺少必需文件列表")
    seen: set[str] = set()
    for value in required_paths:
        relative = _safe_release_path(value)
        if relative in seen:
            raise RuntimeError(f"环境包清单包含重复路径：{relative}")
        seen.add(relative)
        if not (install_root / "runtime" / Path(*PurePosixPath(relative).parts)).is_file():
            raise RuntimeError(f"环境包文件不完整：runtime/{relative}")
    return version


def launch_current(install_root: Path) -> Path:
    current_version(install_root)
    executable = application_executable(install_root)
    process = subprocess.Popen(
        [str(executable)],
        cwd=str(install_root),
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    process.wait()
    return executable


def _confirm_update(update: dict[str, Any]) -> bool:
    size_mb = int(update["size"]) / 1024 / 1024
    notes = str(update.get("notes") or "")
    details = f"\n\n更新内容：\n{notes}" if notes else ""
    message = f"发现新版本 {update['version']}（{size_mb:.1f} MB）。{details}\n\n更新完成后会自动启动，更新期间请勿关闭程序。"
    import ctypes

    if update.get("mandatory"):
        ctypes.windll.user32.MessageBoxW(None, message, "必须更新", 0x40)
        return True
    message += "\n\n是否立即更新？"
    return ctypes.windll.user32.MessageBoxW(None, message, "发现新版本", 0x44) == 6


class _UpdateProgressWindow:
    """Small independent window that remains available while app files are replaced."""

    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.root = tk.Tk()
        self.root.title("AI拓客工具更新")
        self.root.geometry("440x150")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)
        self.message = tk.StringVar(value="正在准备更新")
        self.percent = tk.StringVar(value="0%")
        self.value = tk.IntVar(value=0)
        ttk.Label(self.root, textvariable=self.message, anchor="w").pack(fill="x", padx=24, pady=(24, 10))
        ttk.Progressbar(self.root, maximum=100, variable=self.value).pack(fill="x", padx=24)
        ttk.Label(self.root, textvariable=self.percent, anchor="e").pack(fill="x", padx=24, pady=(8, 0))
        self.root.update()

    def update(self, message: str, percent: int) -> None:
        value = max(0, min(100, int(percent)))
        self.message.set(message)
        self.percent.set(f"{value}%")
        self.value.set(value)
        self.root.update_idletasks()
        self.root.update()

    def close(self) -> None:
        self.root.destroy()


def _show_error(message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "AI拓客工具", 0x10)
    except Exception:
        print(message)


def _show_info(message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "AI拓客工具", 0x40)
    except Exception:
        print(message)


def _manual_update_parent_pid(argv: list[str] | None = None) -> int | None:
    args = list(sys.argv if argv is None else argv)
    if len(args) == 1:
        return None
    if len(args) != 3 or args[1] != "--wait-for-pid":
        raise RuntimeError("检查更新启动参数无效")
    try:
        pid = int(args[2])
    except ValueError as exc:
        raise RuntimeError("检查更新进程编号无效") from exc
    if pid <= 0 or pid == os.getpid():
        raise RuntimeError("检查更新进程编号无效")
    return pid


def _wait_for_process_exit(pid: int, timeout_ms: int = 120_000) -> None:
    """Wait for the running workbench to close before replacing its executable."""
    if os.name != "nt":
        raise RuntimeError("手动检查更新仅支持 Windows 打包版")
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.OpenProcess(0x00100000, False, pid)
    if not handle:
        return
    try:
        result = kernel32.WaitForSingleObject(handle, timeout_ms)
    finally:
        kernel32.CloseHandle(handle)
    if result == 0x00000102:
        raise RuntimeError("应用关闭超时，无法开始检查更新")
    if result != 0:
        raise RuntimeError("等待应用关闭失败，无法开始检查更新")


def main() -> None:
    source_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    progress_window: _UpdateProgressWindow | None = None

    def show_progress(message: str, percent: int) -> None:
        nonlocal progress_window
        if progress_window is None:
            progress_window = _UpdateProgressWindow()
        progress_window.update(message, percent)

    try:
        parent_pid = _manual_update_parent_pid()
        if parent_pid is not None:
            _wait_for_process_exit(parent_pid)
        current_version(source_root)
        validate_environment(source_root)
        apply_remote_update(
            source_root,
            confirm=_confirm_update,
            progress=show_progress,
            on_error=lambda message: _show_error(f"程序更新失败，已继续使用当前版本。\n\n{message}"),
            check_feedback=_show_info if parent_pid is not None else None,
        )
        if progress_window is not None:
            progress_window.close()
            progress_window = None
        validate_environment(source_root)
        launch_current(source_root)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        _show_error(str(exc))
        raise SystemExit(1) from exc
    finally:
        if progress_window is not None:
            progress_window.close()


if __name__ == "__main__":
    main()
