from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app import database, product_config
from app.services import license_service
from app.version import APP_VERSION


COMPONENT_NAME = "voxcpm2"
PRODUCT_NAME = "AI Customer Component"
ENTRYPOINT_NAME = "VoxCPM_Runtime.exe"
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_EXTRACTED_SIZE = 16 * 1024 * 1024 * 1024
TRUSTED_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA2OKYXun9fsGm/ymzAdD7n9hhzusRKVvPS87myTqkdvg=
-----END PUBLIC KEY-----
"""
ProgressCallback = Callable[[dict[str, Any]], None]
CancelCallback = Callable[[], bool]


def component_root() -> Path:
    return database.get_runtime_root() / COMPONENT_NAME


def status() -> dict[str, Any]:
    root = component_root()
    pointer = _read_current_pointer()
    executable = worker_executable(required=False)
    return {
        "component": COMPONENT_NAME,
        "installed": bool(executable),
        "version": str(pointer.get("version") or "") if pointer else "",
        "path": str(root),
        "executable": str(executable or ""),
    }


def worker_executable(*, required: bool = True) -> Path | None:
    pointer = _read_current_pointer()
    version = str(pointer.get("version") or "") if pointer else ""
    entrypoint = str(pointer.get("entrypoint") or ENTRYPOINT_NAME) if pointer else ENTRYPOINT_NAME
    if not VERSION_PATTERN.fullmatch(version) or PurePosixPath(entrypoint).name != entrypoint:
        if required:
            raise RuntimeError("音色克隆组件未安装，请先在内容设置中安装")
        return None
    executable = component_root() / "versions" / version / entrypoint
    if not executable.is_file():
        if required:
            raise RuntimeError("音色克隆组件文件不完整，请重新安装")
        return None
    return executable


def install(progress: ProgressCallback, cancelled: CancelCallback) -> dict[str, Any]:
    progress(_progress("checking", "正在检查可用组件", 0))
    if cancelled():
        raise RuntimeError("组件安装已取消")
    offer = _request_offer()
    update = _validate_offer(offer)
    if update is None:
        current = status()
        if current.get("installed"):
            result = _progress("installed", "音色克隆组件已是最新版本", 100)
            result.update({"version": current.get("version"), "path": current.get("path")})
            progress(result)
            return result
        raise RuntimeError("服务器暂未发布可安装的音色克隆组件")
    archive = _download(update, progress, cancelled)
    if cancelled():
        raise RuntimeError("组件安装已取消")
    progress(_progress("verifying", "组件已下载，正在校验", 96, update["size"], update["size"]))
    if _sha256(archive) != update["sha256"]:
        raise RuntimeError("音色克隆组件校验失败，请重新下载")
    progress(_progress("extracting", "正在安装音色克隆组件", 98, update["size"], update["size"]))
    target = _extract(archive, update["version"], cancelled)
    _activate(update["version"], target)
    result = _progress("installed", "音色克隆组件安装完成", 100, update["size"], update["size"])
    result.update({"version": update["version"], "path": str(target)})
    progress(result)
    return result


def _request_offer() -> dict[str, Any]:
    identity = license_service.ensure_authorized_for("content")
    license_code = str(identity.get("license_code") or "").strip()
    device_code = str(identity.get("device_code") or "").strip()
    if not license_code or not device_code:
        raise RuntimeError("授权信息不完整，请重新激活授权")
    current = str(status().get("version") or "0.0.0")
    body = {
        "licenseCode": license_code,
        "deviceId": device_code,
        "component": COMPONENT_NAME,
        "currentVersion": current,
        "appVersion": APP_VERSION,
        "platform": "windows",
        "arch": "x64",
    }
    endpoint = f"{product_config.license_endpoint()}/update/component/check"
    try:
        response = httpx.post(endpoint, json=body, timeout=15.0)
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError("无法连接组件下载服务，请检查网络后重试") from exc
    if response.status_code >= 400:
        raise RuntimeError(str(payload.get("message") or "组件下载请求被拒绝") if isinstance(payload, dict) else "组件下载请求被拒绝")
    if not isinstance(payload, dict) or payload.get("code") != 200 or not isinstance(payload.get("data"), dict):
        raise RuntimeError(str(payload.get("message") or "组件下载服务返回无效数据") if isinstance(payload, dict) else "组件下载服务返回无效数据")
    return dict(payload["data"])


def _validate_offer(offer: dict[str, Any]) -> dict[str, Any] | None:
    if not offer.get("available"):
        return None
    if not offer.get("downloadAllowed"):
        raise RuntimeError("当前授权不允许下载音色克隆组件")
    manifest_text = offer.get("manifestText")
    signature_text = offer.get("signature")
    if not isinstance(manifest_text, str) or not isinstance(signature_text, str):
        raise RuntimeError("组件签名信息不完整")
    try:
        signature = base64.b64decode(signature_text, validate=True)
        public_key = serialization.load_pem_public_key(TRUSTED_PUBLIC_KEY_PEM)
        if not isinstance(public_key, Ed25519PublicKey) or len(signature) != 64:
            raise ValueError("invalid key")
        public_key.verify(signature, manifest_text.encode("utf-8"))
        manifest = json.loads(manifest_text)
    except Exception as exc:
        raise RuntimeError("组件签名校验失败") from exc
    version = str(manifest.get("version") or "") if isinstance(manifest, dict) else ""
    minimum_app = str(manifest.get("min_app_version") or "") if isinstance(manifest, dict) else ""
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != 1
        or manifest.get("product") != PRODUCT_NAME
        or manifest.get("component") != COMPONENT_NAME
        or manifest.get("platform") != "windows"
        or manifest.get("arch") != "x64"
        or not VERSION_PATTERN.fullmatch(version)
        or not VERSION_PATTERN.fullmatch(minimum_app)
        or str(offer.get("version") or "") != version
        or _is_newer_version(minimum_app, APP_VERSION)
    ):
        raise RuntimeError("组件版本与当前程序不兼容")
    package = manifest.get("package")
    download_url = str(offer.get("downloadUrl") or "")
    if not isinstance(package, dict):
        raise RuntimeError("组件下载信息无效")
    size = package.get("size")
    sha256 = str(package.get("sha256") or "").lower()
    if not isinstance(size, int) or size <= 0 or not SHA256_PATTERN.fullmatch(sha256) or not download_url.startswith("https://"):
        raise RuntimeError("组件下载信息无效")
    return {"version": version, "size": size, "sha256": sha256, "download_url": download_url}


def _download(update: dict[str, Any], progress: ProgressCallback, cancelled: CancelCallback) -> Path:
    downloads = component_root() / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / f"{update['version']}.zip"
    partial = downloads / f"{update['version']}.zip.part"
    expected = int(update["size"])
    if archive.is_file() and archive.stat().st_size == expected and _sha256(archive) == update["sha256"]:
        return archive
    offset = partial.stat().st_size if partial.is_file() else 0
    if offset > expected or (offset == expected and _sha256(partial) != update["sha256"]):
        offset = 0
    if offset == expected:
        partial.replace(archive)
        return archive
    headers = {"Accept": "application/zip"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    try:
        with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            with client.stream("GET", str(update["download_url"]), headers=headers) as response:
                if response.status_code == 206:
                    content_range = str(response.headers.get("Content-Range") or "")
                    if not content_range.startswith(f"bytes {offset}-"):
                        raise RuntimeError("服务器返回了无效的断点续传范围")
                    mode = "ab"
                elif response.status_code == 200:
                    offset = 0
                    mode = "wb"
                else:
                    response.raise_for_status()
                    raise RuntimeError("组件下载失败")
                received = offset
                with partial.open(mode) as output:
                    for block in response.iter_bytes(1024 * 1024):
                        if cancelled():
                            raise RuntimeError("组件安装已取消")
                        received += len(block)
                        if received > expected:
                            raise RuntimeError("下载的组件大小异常")
                        output.write(block)
                        percent = min(95, int(received * 95 / expected))
                        progress(_progress("downloading", f"正在下载音色克隆组件 {percent}%", percent, received, expected))
    except httpx.HTTPError as exc:
        raise RuntimeError("组件下载中断，可稍后继续下载") from exc
    if partial.stat().st_size != expected:
        raise RuntimeError("组件下载不完整，可稍后继续下载")
    if _sha256(partial) != update["sha256"]:
        raise RuntimeError("音色克隆组件校验失败，请重新下载")
    partial.replace(archive)
    return archive


def _extract(archive: Path, version: str, cancelled: CancelCallback) -> Path:
    versions = component_root() / "versions"
    target = versions / version
    if _valid_component_directory(target, version):
        return target
    if target.exists():
        raise RuntimeError("发现同版本的不完整组件目录，请联系技术支持处理")
    staging = versions / f"{version}.staging-{os.getpid()}"
    if staging.exists():
        raise RuntimeError("发现未完成的组件安装目录，请联系技术支持处理")
    versions.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        files: list[tuple[zipfile.ZipInfo, str]] = []
        names: set[str] = set()
        total_size = 0
        for item in source.infolist():
            if item.is_dir():
                continue
            relative = _safe_path(item.filename)
            unix_mode = (item.external_attr >> 16) & 0o170000
            if unix_mode == 0o120000 or relative in names:
                raise RuntimeError("组件压缩包包含不安全或重复的文件")
            total_size += int(item.file_size)
            if total_size > MAX_EXTRACTED_SIZE:
                raise RuntimeError("组件解压后的体积异常")
            names.add(relative)
            files.append((item, relative))
        if ENTRYPOINT_NAME not in names or "component-info.json" not in names:
            raise RuntimeError("组件压缩包缺少启动文件")
        for item, relative in files:
            if cancelled():
                raise RuntimeError("组件安装已取消")
            destination = staging / Path(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.open(item) as input_file, destination.open("wb") as output_file:
                shutil.copyfileobj(input_file, output_file, 1024 * 1024)
    if not _valid_component_directory(staging, version):
        raise RuntimeError("组件内容校验失败")
    staging.replace(target)
    return target


def _activate(version: str, target: Path) -> None:
    if not _valid_component_directory(target, version):
        raise RuntimeError("组件文件不完整")
    pointer = {"component": COMPONENT_NAME, "version": version, "entrypoint": ENTRYPOINT_NAME}
    temporary = component_root() / "current.next.json"
    temporary.write_text(json.dumps(pointer, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(component_root() / "current.json")


def _valid_component_directory(path: Path, version: str) -> bool:
    try:
        info = json.loads((path / "component-info.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    return bool(
        (path / ENTRYPOINT_NAME).is_file()
        and isinstance(info, dict)
        and info.get("format") == 1
        and info.get("product") == PRODUCT_NAME
        and info.get("component") == COMPONENT_NAME
        and info.get("version") == version
        and info.get("entrypoint") == ENTRYPOINT_NAME
    )


def _read_current_pointer() -> dict[str, Any]:
    try:
        value = json.loads((component_root() / "current.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) and value.get("component") == COMPONENT_NAME else {}


def _safe_path(value: object) -> str:
    path = str(value or "").replace("\\", "/")
    pure = PurePosixPath(path)
    if not path or pure.is_absolute() or "." in pure.parts or ".." in pure.parts:
        raise RuntimeError("组件压缩包包含不安全路径")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_newer_version(candidate: str, current: str) -> bool:
    if not VERSION_PATTERN.fullmatch(candidate) or not VERSION_PATTERN.fullmatch(current):
        raise RuntimeError("版本号格式无效")
    candidate_core, _, candidate_suffix = candidate.partition("-")
    current_core, _, current_suffix = current.partition("-")
    candidate_numbers = tuple(int(part) for part in candidate_core.split("."))
    current_numbers = tuple(int(part) for part in current_core.split("."))
    if candidate_numbers != current_numbers:
        return candidate_numbers > current_numbers
    if not candidate_suffix or not current_suffix:
        return bool(not candidate_suffix and current_suffix)
    return candidate_suffix > current_suffix


def _progress(stage: str, message: str, percent: int, downloaded: int = 0, total: int = 0) -> dict[str, Any]:
    return {
        "stage": stage,
        "message": message,
        "percent": max(0, min(100, int(percent))),
        "downloaded": int(downloaded),
        "total": int(total),
    }
