from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app import device_identity, product_config
from app.semver import RELEASE_VERSION_PATTERN as VERSION_PATTERN, version_key
from app.release_paths import safe_release_path as _safe_release_path


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_NAME = "release-manifest.json"
APPLICATION_ENTRYPOINT = "runtime/application/AI_Customer_App.exe"
UPDATE_TRANSACTION_NAME = "update-transaction.json"
ROLLBACK_DIRECTORY_NAME = "rollback"
PRODUCT_NAME = "AI Customer Desktop"
UPDATER_VERSION = "1.1.0"
PREPARED_UPDATE_NAME = "prepared-update.json"
MAX_EXTRACTED_SIZE = 8 * 1024 * 1024 * 1024
TRUSTED_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA2OKYXun9fsGm/ymzAdD7n9hhzusRKVvPS87myTqkdvg=
-----END PUBLIC KEY-----
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_verified_release(release_root: Path) -> tuple[str, dict[str, dict[str, Any]]]:
    manifest_path = _installed_path(release_root, MANIFEST_NAME)
    if not manifest_path.is_file():
        raise RuntimeError("发布包缺少 release-manifest.json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError("发布包版本信息无效")
    version = str(payload.get("version") or "").strip()
    environment_version = str(payload.get("environment_version") or "").strip()
    schema_version = payload.get("schema_version")
    if (
        payload.get("format") != 1
        or payload.get("product") != PRODUCT_NAME
        or payload.get("entrypoint") != APPLICATION_ENTRYPOINT
        or not VERSION_PATTERN.fullmatch(version)
        or not VERSION_PATTERN.fullmatch(environment_version)
        or type(schema_version) is not int or schema_version < 0
        or not VERSION_PATTERN.fullmatch(str(payload.get("updater_version") or ""))
        or _is_newer_version("1.1.0", str(payload.get("updater_version")))
    ):
        raise RuntimeError("发布包版本信息无效")

    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise RuntimeError("发布包清单为空")

    files: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for item in raw_files:
        if not isinstance(item, dict):
            raise RuntimeError("发布包清单格式无效")
        relative_path = _safe_release_path(item.get("path"))
        if not _is_program_owned_path(relative_path):
            raise RuntimeError(f"发布包包含非程序文件：{relative_path}")
        size = item.get("size")
        sha256 = str(item.get("sha256") or "").lower()
        if relative_path.casefold() in seen or type(size) is not int or size < 0 or not SHA256_PATTERN.fullmatch(sha256):
            raise RuntimeError("发布包清单格式无效")
        seen.add(relative_path.casefold())
        source = _installed_path(release_root, relative_path)
        if not source.is_file():
            raise RuntimeError(f"发布包文件缺失：{relative_path}")
        if source.stat().st_size != size or _sha256(source) != sha256:
            raise RuntimeError(f"发布包校验失败：{relative_path}")
        files[relative_path] = {"source": source}

    if APPLICATION_ENTRYPOINT not in files or "AI_Customer.exe" not in files:
        raise RuntimeError("程序包缺少 AI_Customer.exe 或 AI_Customer_App.exe")
    return version, files


def _is_program_owned_path(relative_path: str) -> bool:
    """程序及其直接依赖一起替换，用户数据与独立组件不进入白名单。"""
    if relative_path in {"AI_Customer.exe", "README.txt", APPLICATION_ENTRYPOINT}:
        return True
    return relative_path.startswith(("runtime/frontend_dist/", "runtime/application/"))


def _is_newer_version(candidate: str, current: str) -> bool:
    try:
        return version_key(candidate) > version_key(current)
    except ValueError as error:
        raise RuntimeError("更新版本信息格式不正确") from error


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
    endpoint = f"{product_config.license_endpoint()}/update/check"
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


def _signed_manifest(offer: dict[str, Any]) -> dict[str, Any]:
    manifest_text = offer.get("manifestText")
    signature_text = offer.get("signature")
    if not isinstance(manifest_text, str) or not isinstance(signature_text, str):
        raise RuntimeError("更新签名信息不完整")
    try:
        signature = base64.b64decode(signature_text, validate=True)
        if len(signature) != 64:
            raise RuntimeError("更新签名长度无效")
        public_key = serialization.load_pem_public_key(TRUSTED_PUBLIC_KEY_PEM)
        if not isinstance(public_key, Ed25519PublicKey):
            raise RuntimeError("更新公钥格式无效")
        public_key.verify(signature, manifest_text.encode("utf-8"))
        manifest = json.loads(manifest_text)
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise RuntimeError("更新签名或清单校验失败，请重新检查更新") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("更新清单格式无效")
    version = str(manifest.get("version") or "")
    minimum = str(manifest.get("min_updater_version") or "")
    if (
        manifest.get("format") != 1
        or manifest.get("product") != PRODUCT_NAME
        or manifest.get("platform") != "windows"
        or manifest.get("arch") != "x64"
        or not VERSION_PATTERN.fullmatch(version)
        or not VERSION_PATTERN.fullmatch(minimum)
        or not VERSION_PATTERN.fullmatch(str(manifest.get("environment_version") or ""))
        or type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] < 0
    ):
        raise RuntimeError("更新清单不适用于当前程序")
    package = manifest.get("package")
    if not isinstance(package, dict):
        raise RuntimeError("更新包信息无效")
    size = package.get("size")
    sha256 = str(package.get("sha256") or "").lower()
    if type(size) is not int or size <= 0 or not SHA256_PATTERN.fullmatch(sha256):
        raise RuntimeError("更新包信息无效")
    if _is_newer_version(minimum, UPDATER_VERSION):
        raise RuntimeError(f"此更新需要更新器 {minimum}，当前为 {UPDATER_VERSION}，请使用新的完整软件包")
    return manifest


def _validate_update_offer(offer: dict[str, Any], current: str, environment_version: str) -> dict[str, Any] | None:
    if not offer.get("available") or not offer.get("downloadAllowed"):
        return None
    manifest = _signed_manifest(offer)
    version = str(manifest["version"])
    if str(offer.get("version") or "") != version or not _is_newer_version(version, current):
        raise RuntimeError("更新版本与当前程序不一致")
    if manifest["environment_version"] != environment_version:
        raise RuntimeError(f"此更新需要核心环境 {manifest['environment_version']}，当前为 {environment_version}，请安装新的完整软件包")
    download_url = str(offer.get("downloadUrl") or "")
    if not download_url.startswith("https://"):
        raise RuntimeError("更新包下载地址无效")
    return {
        "version": version,
        "size": manifest["package"]["size"],
        "sha256": str(manifest["package"]["sha256"]).lower(),
        "download_url": download_url,
        "mandatory": bool(offer.get("mandatory")),
        "notes": str(offer.get("notes") or "").strip()[:1000],
    }


def _validate_install_requirements(install_root: Path, manifest: dict[str, Any]) -> None:
    environment_version = validate_environment(install_root)
    if manifest.get("environment_version") != environment_version:
        raise RuntimeError(f"此更新需要核心环境 {manifest.get('environment_version')}，当前为 {environment_version}，请安装新的完整软件包")
    database = install_root / "data" / "ai_customer.sqlite3"
    if database.is_file():
        with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
            schema = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if schema != manifest.get("schema_version"):
            raise RuntimeError(f"此更新要求数据库结构 {manifest.get('schema_version')}，现有数据为 {schema}；请先备份并明确处理不兼容数据，已停止安装")


@contextmanager
def _update_lock(install_root: Path):
    """文件锁随进程退出自动释放，检查、准备和安装不能同时改写更新目录。"""
    directory = _update_path(install_root, "update.lock").parent
    directory.mkdir(parents=True, exist_ok=True)
    with _update_path(install_root, "update.lock").open("a+b") as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("另一项更新操作正在进行，请稍后重试") from exc
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("另一项更新操作正在进行，请稍后重试") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def check_remote_update(install_root: Path) -> dict[str, Any]:
    """运行中查询更新；内部签名 offer 不应返回给前端。"""
    with _update_lock(install_root):
        current = current_version(install_root)
        result = {"current_version": current, "available": False, "can_prepare": False, "reason": ""}
        identity = _read_update_identity(install_root)
        if not identity:
            return {**result, "reason": "请先完成产品授权，再检查更新"}
        offer = _request_update_offer(identity, current)
        if not offer:
            raise RuntimeError("更新服务未返回有效结果，请稍后重试")
        if not offer.get("available"):
            return {**result, "reason": "当前已是最新版本"}
        result.update({
            "available": True, "version": str(offer.get("version") or ""),
            "notes": str(offer.get("notes") or "").strip()[:1000],
            "mandatory": bool(offer.get("mandatory")),
        })
        if not offer.get("downloadAllowed"):
            return {**result, "reason": str(offer.get("reason") or "当前授权不允许下载此更新")}
        try:
            update = _validate_update_offer(offer, current, validate_environment(install_root))
            _validate_install_requirements(install_root, _signed_manifest(offer))
        except RuntimeError as exc:
            return {**result, "reason": str(exc)}
        assert update is not None
        return {**result, "can_prepare": True, "size": update["size"], "_offer": offer}


def _download_update(
    update: dict[str, Any],
    install_root: Path,
    progress: Callable[[str, int], None] | None = None,
) -> Path:
    updates_root = install_root / "updates"
    updates_root.mkdir(exist_ok=True)
    archive_path = _update_path(install_root, f"{str(update.get('version'))}.zip")
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
    release_root = _update_path(install_root, f"{version}.extracting")
    _remove_tree(release_root, install_root.resolve() / "updates")
    with zipfile.ZipFile(archive_path) as archive:
        names: set[str] = set()
        files: list[tuple[zipfile.ZipInfo, str]] = []
        total_size = 0
        for item in archive.infolist():
            if item.is_dir():
                continue
            relative_path = _safe_release_path(item.filename)
            if relative_path != MANIFEST_NAME and not _is_program_owned_path(relative_path):
                raise RuntimeError(f"更新包包含非程序文件：{relative_path}")
            unix_mode = (item.external_attr >> 16) & 0o170000
            total_size += int(item.file_size)
            if relative_path.casefold() in names or unix_mode == 0o120000 or total_size > MAX_EXTRACTED_SIZE:
                raise RuntimeError("更新包包含重复链接或解压体积异常")
            names.add(relative_path.casefold())
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
    _, declared = _read_verified_release(release_root)
    if names != {path.casefold() for path in declared} | {MANIFEST_NAME}:
        raise RuntimeError("更新包包含未声明的程序文件")
    return release_root


def prepare_remote_update(
    install_root: Path, checked: dict[str, Any], progress: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    """在线下载和验签，不替换运行中的程序，也不保存授权码或临时下载地址。"""
    with _update_lock(install_root):
        current = current_version(install_root)
        if not checked.get("can_prepare") or checked.get("current_version") != current:
            raise RuntimeError("请重新检查更新后再下载")
        offer = checked.get("_offer")
        if not isinstance(offer, dict):
            raise RuntimeError("缺少已验证的更新信息，请重新检查更新")
        update = _validate_update_offer(offer, current, validate_environment(install_root))
        if update is None:
            raise RuntimeError("当前更新不可下载")
        manifest = _signed_manifest(offer)
        _validate_install_requirements(install_root, manifest)
        path = _update_path(install_root, PREPARED_UPDATE_NAME)
        path.unlink(missing_ok=True)
        archive_path = _download_update(update, install_root, progress)
        release_root = _extract_update(archive_path, install_root, str(update["version"]), progress)
        release = json.loads((release_root / MANIFEST_NAME).read_text(encoding="utf-8-sig"))
        for key in ("version", "environment_version", "schema_version"):
            if release.get(key) != manifest.get(key):
                raise RuntimeError(f"更新包的 {key} 与签名清单不一致")
        _validate_install_requirements(install_root, manifest)
        if progress:
            progress("下载及校验完成，可以安装", 100)
        temporary = _update_path(install_root, path.with_suffix(".next").name)
        temporary.write_text(json.dumps({
            "format": 1, "current_version": current,
            "manifestText": offer["manifestText"], "signature": offer["signature"],
        }, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
        return prepared_update(install_root)


def _read_prepared(install_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = _update_path(install_root, PREPARED_UPDATE_NAME)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("format") != 1 or payload.get("current_version") != current_version(install_root):
        raise RuntimeError("已准备的更新不适用于当前版本，请重新检查更新")
    manifest = _signed_manifest(payload)
    if not _is_newer_version(manifest["version"], str(payload["current_version"])):
        raise RuntimeError("已准备的更新版本无效")
    _validate_install_requirements(install_root, manifest)
    return payload, manifest


def prepared_update(install_root: Path) -> dict[str, Any]:
    """只提供安全状态元数据，安装前另行校验 ZIP 和全部解压文件。"""
    try:
        if not _update_path(install_root, PREPARED_UPDATE_NAME).is_file():
            return {"ready": False, "reason": "尚未下载更新"}
        _, manifest = _read_prepared(install_root)
        archive = _update_path(install_root, f"{manifest['version']}.zip")
        if not archive.is_file() or archive.stat().st_size != manifest["package"]["size"]:
            raise RuntimeError("已下载的更新包缺失或不完整，请重新下载")
    except (InvalidSignature, RuntimeError, OSError, ValueError, sqlite3.Error) as exc:
        return {"ready": False, "reason": str(exc) or "已准备的更新签名无效"}
    return {"ready": True, "version": manifest["version"], "size": manifest["package"]["size"], "reason": ""}


def _verified_prepared_release(install_root: Path, *, extract: bool = False) -> Path:
    _, manifest = _read_prepared(install_root)
    version = str(manifest["version"])
    archive = _update_path(install_root, f"{version}.zip")
    if not archive.is_file() or archive.stat().st_size != manifest["package"]["size"] or _sha256(archive) != str(manifest["package"]["sha256"]).lower():
        raise RuntimeError("已下载的更新包校验失败，请重新下载")
    release = _update_path(install_root, f"{version}.extracting")
    # 启动新入口前重新解压；新入口启动后只验证，不能删除自身所在目录。
    if extract:
        _extract_update(archive, install_root, version)
    _read_verified_release(release)
    package = json.loads((release / MANIFEST_NAME).read_text(encoding="utf-8-sig"))
    with zipfile.ZipFile(archive) as zipped:
        original = json.loads(zipped.read(MANIFEST_NAME).decode("utf-8-sig"))
    if package != original:
        raise RuntimeError("已解压的更新清单与签名 ZIP 不一致")
    if any(package.get(key) != manifest.get(key) for key in ("version", "schema_version", "environment_version")):
        raise RuntimeError("更新包与签名清单不一致")
    return release


def start_prepared_update(install_root: Path, backend_pid: int) -> subprocess.Popen:
    """由 staging 中的新入口接管安装，避免覆盖正在运行的稳定入口。"""
    if type(backend_pid) is not int or backend_pid <= 0:
        raise RuntimeError("后台进程编号无效")
    with _update_lock(install_root):
        release = _verified_prepared_release(install_root, extract=True)
        arguments = [str(release / "AI_Customer.exe"), "--apply-prepared", "--install-root", str(install_root), "--wait-for-pid", str(backend_pid)]
        bootstrap_pid = os.environ.get("AI_CUSTOMER_BOOTSTRAP_PID", "")
        if bootstrap_pid:
            pid = int(bootstrap_pid)
            if pid <= 0 or pid == backend_pid:
                raise RuntimeError("稳定入口进程编号无效")
            arguments.extend(["--wait-for-launcher-pid", str(pid)])
        return subprocess.Popen(arguments, cwd=str(install_root), close_fds=True, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def current_version(install_root: Path) -> str:
    manifest_path = install_root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise RuntimeError("程序目录缺少 release-manifest.json，请重新解压完整程序包")
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError("当前版本信息格式不正确")
    version = str(payload.get("version") or "").strip()
    environment_version = str(payload.get("environment_version") or "").strip()
    if (
        payload.get("format") != 1
        or payload.get("product") != PRODUCT_NAME
        or payload.get("entrypoint") != APPLICATION_ENTRYPOINT
        or not VERSION_PATTERN.fullmatch(version)
        or not VERSION_PATTERN.fullmatch(environment_version)
        or type(payload.get("schema_version")) is not int
        or payload["schema_version"] < 0
        or not VERSION_PATTERN.fullmatch(str(payload.get("updater_version") or ""))
        or _is_newer_version("1.1.0", str(payload.get("updater_version")))
    ):
        raise RuntimeError("当前版本信息格式不正确")
    return version


def application_executable(install_root: Path) -> Path:
    executable = install_root / Path(*PurePosixPath(APPLICATION_ENTRYPOINT).parts)
    if not executable.is_file():
        raise RuntimeError("程序目录缺少 AI_Customer_App.exe，请重新解压完整程序包")
    return executable


def apply_release(
    release_root: Path,
    install_root: Path,
    progress: Callable[[str, int], None] | None = None,
) -> str:
    """新版进程健康启动后才提交；失败先停止新版，再恢复旧程序并启动。"""
    release_root = release_root.resolve()
    version, files = _read_verified_release(release_root)
    install_root.mkdir(parents=True, exist_ok=True)
    (install_root / "data").mkdir(exist_ok=True)
    _recover_interrupted_update(install_root)
    _, current_files = _read_verified_release(install_root)
    manifest = json.loads((release_root / MANIFEST_NAME).read_text(encoding="utf-8-sig"))
    _validate_install_requirements(install_root, manifest)
    old_paths = set(current_files)
    new_paths = set(files)
    rollback_root = _prepare_update_transaction(install_root, sorted(old_paths), sorted(new_paths))
    process: subprocess.Popen | None = None
    try:
        for relative_path in sorted(old_paths - new_paths):
            _installed_path(install_root, relative_path).unlink(missing_ok=True)
        ordered_files = sorted(
            ((path, files[path]) for path in new_paths),
            key=lambda value: value[0] == APPLICATION_ENTRYPOINT,
        )
        for index, (relative_path, item) in enumerate(ordered_files, start=1):
            _replace_file(item["source"], _installed_path(install_root, relative_path))
            if progress:
                progress("正在安装程序更新", 95 + min(4, int(index * 4 / len(ordered_files))))
        _replace_file(release_root / MANIFEST_NAME, install_root / MANIFEST_NAME)
        process = _start_application(install_root)
        _wait_for_application_health(process, manifest)
        _write_transaction(install_root, "committed", sorted(old_paths), sorted(new_paths))
    except Exception as exc:
        if process is not None:
            _stop_application(process)
        try:
            _recover_interrupted_update(install_root)
        except Exception as rollback_exc:
            raise RuntimeError(f"程序更新失败且旧版本恢复失败：{rollback_exc}") from exc
        _start_application(install_root)
        raise RuntimeError(f"新版未能完成安装和启动，已恢复旧程序：{exc}") from exc
    # ponytail: 清理失败保留已提交事务，下次启动继续清理但不误报更新失败。
    try:
        _cleanup_update_transaction(install_root, rollback_root)
    except OSError:
        pass
    if progress:
        progress("新版已成功启动", 100)
    return version


def _prepare_update_transaction(install_root: Path, old_paths: list[str], new_paths: list[str]) -> Path:
    updates_root = install_root / "updates"
    updates_root.mkdir(exist_ok=True)
    rollback_root = _update_path(install_root, ROLLBACK_DIRECTORY_NAME)
    _remove_tree(rollback_root, install_root.resolve() / "updates")
    for relative_path in old_paths:
        source = _installed_path(install_root, relative_path)
        destination = _installed_path(rollback_root, relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    shutil.copy2(install_root / MANIFEST_NAME, rollback_root / MANIFEST_NAME)
    _read_verified_release(rollback_root)
    _write_transaction(install_root, "prepared", old_paths, new_paths)
    return rollback_root


def _recover_interrupted_update(install_root: Path) -> bool:
    transaction_path = _update_path(install_root, UPDATE_TRANSACTION_NAME)
    if not transaction_path.is_file():
        return False
    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
    if transaction.get("format") != 1 or transaction.get("state") not in {"prepared", "committed", "rolled_back"}:
        raise RuntimeError("更新事务记录无效，已停止启动")
    old_paths = _transaction_paths(transaction.get("old_files"))
    new_paths = _transaction_paths(transaction.get("new_files"))
    rollback_root = _update_path(install_root, ROLLBACK_DIRECTORY_NAME)
    recovered = transaction["state"] == "prepared"
    if recovered:
        _, rollback_files = _read_verified_release(rollback_root)
        if set(rollback_files) != set(old_paths):
            raise RuntimeError("更新回滚文件与事务记录不一致")
        for relative_path in old_paths:
            _replace_file(rollback_files[relative_path]["source"], _installed_path(install_root, relative_path))
        for relative_path in sorted(set(new_paths) - set(old_paths)):
            _installed_path(install_root, relative_path).unlink(missing_ok=True)
        _replace_file(rollback_root / MANIFEST_NAME, install_root / MANIFEST_NAME)
        _write_transaction(install_root, "rolled_back", old_paths, new_paths)
    for relative_path in set(old_paths) | set(new_paths):
        _replacement_path(_installed_path(install_root, relative_path)).unlink(missing_ok=True)
    try:
        _cleanup_update_transaction(install_root, rollback_root)
    except OSError:
        pass
    return recovered


def _write_transaction(install_root: Path, state: str, old_paths: list[str], new_paths: list[str]) -> None:
    path = _update_path(install_root, UPDATE_TRANSACTION_NAME)
    temporary = _update_path(install_root, path.with_suffix(".next").name)
    temporary.write_text(
        json.dumps(
            {"format": 1, "state": state, "old_files": old_paths, "new_files": new_paths},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _transaction_paths(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise RuntimeError("更新事务文件清单无效")
    paths = [_safe_release_path(item) for item in value]
    if len(paths) != len(set(paths)) or any(not _is_program_owned_path(path) for path in paths):
        raise RuntimeError("更新事务文件清单无效")
    return paths


def _replace_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = _replacement_path(destination)
    temporary.unlink(missing_ok=True)
    shutil.copy2(source, temporary)
    temporary.replace(destination)


def _replacement_path(destination: Path) -> Path:
    return destination.with_name(f"{destination.name}.update-next")


def _installed_path(root: Path, relative_path: str) -> Path:
    path = root.resolve() / Path(*PurePosixPath(_safe_release_path(relative_path)).parts)
    if path.is_symlink() or path.resolve() != path:
        raise RuntimeError(f"程序路径存在符号链接或目录重定向：{relative_path}")
    return path


def _update_path(install_root: Path, relative_path: str) -> Path:
    updates = _installed_path(install_root, "updates")
    return _installed_path(updates, relative_path)


def _cleanup_update_transaction(install_root: Path, rollback_root: Path) -> None:
    _remove_tree(rollback_root, install_root.resolve() / "updates")
    _update_path(install_root, UPDATE_TRANSACTION_NAME).unlink(missing_ok=True)
    _update_path(install_root, Path(UPDATE_TRANSACTION_NAME).with_suffix(".next").name).unlink(missing_ok=True)


def _remove_tree(root: Path, updates_root: Path) -> None:
    updates_root = updates_root.absolute()
    root = root.absolute()
    if updates_root.is_symlink() or updates_root.resolve() != updates_root or root == updates_root or not root.is_relative_to(updates_root):
        raise RuntimeError("更新清理目录越界，已停止操作")
    if root.is_symlink() or root.resolve() != root:
        raise RuntimeError("更新目录存在符号链接或目录重定向，已停止清理")
    if not root.exists():
        return
    files: list[Path] = []
    directories: list[Path] = []
    # 先检查所有目录，绝不递归遍历 junction 指向的数据目录。
    for directory, children, names in os.walk(root, followlinks=False):
        for name in [*children, *names]:
            path = Path(directory) / name
            if path.is_symlink() or path.resolve() != path:
                raise RuntimeError("更新目录包含符号链接或目录重定向，已停止清理")
        directories.extend(Path(directory) / name for name in children)
        files.extend(Path(directory) / name for name in names)
    for path in files:
        path.unlink()
    for path in reversed(directories):
        path.rmdir()
    root.rmdir()


def validate_environment(install_root: Path) -> str:
    program = json.loads((install_root / MANIFEST_NAME).read_text(encoding="utf-8-sig"))
    expected = str(program.get("environment_version") or "")
    manifest_path = install_root / "runtime" / "environment-manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("核心运行环境缺失，请重新解压完整 Core 软件包")
    environment = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(environment, dict) or environment.get("format") != 1 or environment.get("product") != "AI Customer Environment":
        raise RuntimeError("核心运行环境清单无效，请重新解压完整 Core 软件包")
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


def _start_application(install_root: Path, *, bootstrap_pid: int | None = None) -> subprocess.Popen:
    current_version(install_root)
    executable = application_executable(install_root)
    env = os.environ.copy()
    env["AI_CUSTOMER_PACKAGED"] = "1"
    env["AI_CUSTOMER_INSTALL_ROOT"] = str(install_root)
    env.pop("AI_CUSTOMER_BOOTSTRAP_PID", None)
    if bootstrap_pid is not None:
        env["AI_CUSTOMER_BOOTSTRAP_PID"] = str(bootstrap_pid)
    env.pop("NUITKA_ONEFILE_PARENT", None)
    return subprocess.Popen(
        [str(executable)],
        cwd=str(install_root),
        env=env,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def launch_current(install_root: Path) -> Path:
    # 只有等待业务进程的稳定入口会保持存活，临时安装入口不留下陈旧 PID。
    process = _start_application(install_root, bootstrap_pid=_bootstrap_process_id())
    process.wait()
    return application_executable(install_root)


def _bootstrap_process_id() -> int:
    return int(os.environ.get("NUITKA_ONEFILE_PARENT") or os.getpid())


def _wait_for_application_health(process: subprocess.Popen, manifest: dict[str, Any], timeout_seconds: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("新版程序在启动完成前退出，请查看 data/logs/app.log")
        for port in [8000, *range(8010, 8030)]:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (OSError, ValueError, urllib.error.URLError):
                continue
            if (
                payload.get("status") == "ok" and payload.get("product") == "ai-customer"
                and payload.get("packaged") is True
                and all(payload.get(key) == manifest[key] for key in ("version", "environment_version", "schema_version", "updater_version"))
                and payload.get("process_id") == process.pid
            ):
                return
        time.sleep(0.25)
    raise RuntimeError("新版程序启动健康检查超时，请查看 data/logs/app.log")


def _stop_application(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


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
        icon_root = Path(__file__).resolve().parent
        icon_path = icon_root / "ai-customer-icon.ico"
        if icon_path.is_file():
            try:
                self.root.iconbitmap(default=str(icon_path))
            except tk.TclError:
                pass
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


def _wait_for_process_exit(pid: int, timeout_ms: int = 120_000) -> None:
    """Wait for the running workbench to close before replacing its executable."""
    if os.name != "nt":
        raise RuntimeError("程序安装仅支持 Windows 打包版")
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
        raise RuntimeError("应用关闭超时，已停止安装")
    if result != 0:
        raise RuntimeError("等待应用关闭失败，已停止安装")


def _running_workbench_url() -> str:
    for port in [8000, *range(8010, 8030)]:
        url = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=0.2) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError):
            continue
        if (
            payload.get("status") == "ok"
            and payload.get("product") == "ai-customer"
            and payload.get("packaged") is True
        ):
            return url
    return ""


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply-prepared", action="store_true")
    mode.add_argument("--recover-install", action="store_true")
    parser.add_argument("--install-root", type=Path)
    parser.add_argument("--wait-for-pid", type=int)
    parser.add_argument("--wait-for-launcher-pid", type=int)
    arguments = parser.parse_args()
    source_root = arguments.install_root or Path(sys.argv[0]).resolve().parent
    source_root = source_root.resolve()
    progress_window: _UpdateProgressWindow | None = None

    def show_progress(message: str, percent: int) -> None:
        nonlocal progress_window
        if progress_window is None:
            progress_window = _UpdateProgressWindow()
        progress_window.update(message, percent)

    try:
        if arguments.apply_prepared or arguments.recover_install:
            if not arguments.install_root or not arguments.wait_for_pid:
                raise RuntimeError("更新安装启动参数无效")
            for pid in (arguments.wait_for_pid, arguments.wait_for_launcher_pid):
                if pid is not None:
                    if pid <= 0 or pid == os.getpid():
                        raise RuntimeError("更新进程编号无效")
                    _wait_for_process_exit(pid)
            with _update_lock(source_root):
                if arguments.recover_install:
                    _recover_interrupted_update(source_root)
                    validate_environment(source_root)
                    _start_application(source_root)
                else:
                    release = _verified_prepared_release(source_root)
                    apply_release(release, source_root, show_progress)
                    _update_path(source_root, PREPARED_UPDATE_NAME).unlink(missing_ok=True)
            return
        if arguments.install_root or arguments.wait_for_pid or arguments.wait_for_launcher_pid:
            raise RuntimeError("稳定入口启动参数无效")
        if running_url := _running_workbench_url():
            webbrowser.open(running_url)
            return
        transaction_path = _update_path(source_root, UPDATE_TRANSACTION_NAME)
        if transaction_path.is_file():
            transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
            if transaction.get("state") == "prepared":
                rollback_root = _update_path(source_root, ROLLBACK_DIRECTORY_NAME)
                _read_verified_release(rollback_root)
                subprocess.Popen([
                    str(rollback_root / "AI_Customer.exe"), "--recover-install", "--install-root", str(source_root),
                    "--wait-for-pid", str(_bootstrap_process_id()),
                ], cwd=str(source_root), close_fds=True, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                return
            with _update_lock(source_root):
                _recover_interrupted_update(source_root)
        current_version(source_root)
        validate_environment(source_root)
        prepared = prepared_update(source_root)
        if prepared.get("ready") and _confirm_update(prepared):
            start_prepared_update(source_root, _bootstrap_process_id())
            return
        # 启动不联网；检查、下载在工作台运行中完成。
        launch_current(source_root)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        _show_error(str(exc))
        raise SystemExit(1) from exc
    finally:
        if progress_window is not None:
            progress_window.close()


if __name__ == "__main__":
    main()
