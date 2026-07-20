from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import zipfile
from pathlib import PurePosixPath


VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_PROGRAM_SIZE = 8 * 1024 * 1024 * 1024
APPLICATION_ENTRYPOINT = "runtime/application/AI_Customer_App.exe"


def _safe_path(value: object) -> str:
    path = str(value or "").replace("\\", "/")
    pure = PurePosixPath(path)
    if not path or pure.is_absolute() or "." in pure.parts or ".." in pure.parts or "" in pure.parts:
        raise RuntimeError(f"程序 ZIP 包含不安全路径：{path}")
    return path


def _program_owned(path: str) -> bool:
    if path in {"AI_Customer.exe", "README.txt", APPLICATION_ENTRYPOINT}:
        return True
    return path.startswith(("runtime/frontend_dist/", "runtime/application/app/"))


def verify(path: str, expected_version: str = "") -> dict[str, object]:
    """Verify the exact ZIP that will be signed and uploaded."""
    archive_hash = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            archive_hash.update(block)
    with zipfile.ZipFile(path) as archive:
        entries: dict[str, zipfile.ZipInfo] = {}
        total_size = 0
        for item in archive.infolist():
            if item.is_dir():
                continue
            relative = _safe_path(item.filename)
            unix_mode = (item.external_attr >> 16) & 0o170000
            if relative in entries or unix_mode == 0o120000:
                raise RuntimeError("程序 ZIP 包含重复文件或符号链接")
            total_size += item.file_size
            if total_size > MAX_PROGRAM_SIZE:
                raise RuntimeError("程序 ZIP 解压体积异常")
            entries[relative] = item
        if "release-manifest.json" not in entries:
            raise RuntimeError("程序 ZIP 缺少 release-manifest.json")
        manifest = json.loads(archive.read(entries["release-manifest.json"]).decode("utf-8-sig"))
        version = str(manifest.get("version") or "") if isinstance(manifest, dict) else ""
        environment_version = str(manifest.get("environment_version") or "") if isinstance(manifest, dict) else ""
        schema_version = manifest.get("schema_version") if isinstance(manifest, dict) else None
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != 1
            or manifest.get("product") != "AI Customer Desktop"
            or manifest.get("entrypoint") != APPLICATION_ENTRYPOINT
            or not VERSION_PATTERN.fullmatch(version)
            or not VERSION_PATTERN.fullmatch(environment_version)
            or not isinstance(schema_version, int)
            or schema_version < 0
            or (expected_version and version != expected_version)
        ):
            raise RuntimeError("程序 ZIP 的发布清单无效")
        declared: set[str] = set()
        raw_files = manifest.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise RuntimeError("程序 ZIP 的文件清单为空")
        for item in raw_files:
            if not isinstance(item, dict):
                raise RuntimeError("程序 ZIP 的文件清单无效")
            relative = _safe_path(item.get("path"))
            expected_size = item.get("size")
            expected_hash = str(item.get("sha256") or "").lower()
            if relative in declared or relative == "AI_Customer.exe" or not _program_owned(relative):
                raise RuntimeError(f"程序 ZIP 包含非程序文件：{relative}")
            if not isinstance(expected_size, int) or expected_size < 0 or not SHA256_PATTERN.fullmatch(expected_hash):
                raise RuntimeError(f"程序 ZIP 文件记录无效：{relative}")
            entry = entries.get(relative)
            if entry is None or entry.file_size != expected_size:
                raise RuntimeError(f"程序 ZIP 文件缺失或大小不符：{relative}")
            digest = hashlib.sha256()
            with archive.open(entry) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected_hash:
                raise RuntimeError(f"程序 ZIP 文件校验失败：{relative}")
            declared.add(relative)
        if (
            declared != set(entries) - {"release-manifest.json", "AI_Customer.exe"}
            or "AI_Customer.exe" not in entries
            or APPLICATION_ENTRYPOINT not in declared
        ):
            raise RuntimeError("程序 ZIP 实际文件与发布清单不一致")
    return {
        "version": version,
        "environment_version": environment_version,
        "schema_version": schema_version,
        "size": os.path.getsize(path),
        "sha256": archive_hash.hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify an AI Customer Program ZIP")
    parser.add_argument("zip_path")
    parser.add_argument("--version", default="")
    args = parser.parse_args()
    print(json.dumps(verify(args.zip_path, args.version), ensure_ascii=False))


if __name__ == "__main__":
    main()
