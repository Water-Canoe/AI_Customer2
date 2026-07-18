from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


DATABASE_FILE = "ai_customer.sqlite3"
MEDIA_CRAWLER_DATABASE_FILE = "media_crawler.sqlite3"
MANIFEST_FILE = "manifest.json"
DATA_DIR = "files"


def create_backup(
    source_conn: sqlite3.Connection,
    backup_root: Path,
    reason: str,
    schema_version: int,
    data_roots: dict[str, Path] | None = None,
    media_crawler_db: Path | None = None,
) -> dict[str, Any]:
    backup_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir = backup_root / backup_id
    backup_dir.mkdir(parents=True, exist_ok=False)
    database_path = backup_dir / DATABASE_FILE

    destination = sqlite3.connect(database_path)
    try:
        source_conn.backup(destination)
        integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"备份完整性检查失败：{integrity}")
    finally:
        destination.close()

    media_crawler_database: dict[str, Any] | None = None
    if media_crawler_db and media_crawler_db.is_file():
        media_destination = backup_dir / MEDIA_CRAWLER_DATABASE_FILE
        _backup_database(media_crawler_db, media_destination)
        media_crawler_database = {
            "file": MEDIA_CRAWLER_DATABASE_FILE,
            "size": media_destination.stat().st_size,
            "sha256": _sha256(media_destination),
        }

    directories: dict[str, dict[str, Any]] = {}
    for name, source in (data_roots or {}).items():
        safe_name = _safe_directory_name(name)
        directories[safe_name] = _copy_directory(source, backup_dir / DATA_DIR / safe_name)
    manifest = {
        "id": backup_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "reason": _clean_reason(reason),
        "schema_version": schema_version,
        "database_file": DATABASE_FILE,
        "database_size": database_path.stat().st_size,
        "database_sha256": _sha256(database_path),
        "media_crawler_database": media_crawler_database,
        "file_count": sum(item["file_count"] for item in directories.values()),
        "data_size": sum(item["size"] for item in directories.values()),
        "data_directories": directories,
    }
    (backup_dir / MANIFEST_FILE).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def list_backups(backup_root: Path) -> list[dict[str, Any]]:
    if not backup_root.exists():
        return []
    results: list[dict[str, Any]] = []
    for directory in backup_root.iterdir():
        manifest_path = directory / MANIFEST_FILE
        if not directory.is_dir() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        database_path = directory / str(manifest.get("database_file") or DATABASE_FILE)
        media_metadata = manifest.get("media_crawler_database")
        media_path = directory / str(media_metadata.get("file")) if isinstance(media_metadata, dict) else None
        manifest["available"] = database_path.is_file() and (media_path is None or media_path.is_file())
        results.append(manifest)
    return sorted(results, key=lambda item: str(item.get("id") or ""), reverse=True)


def delete_backup(backup_root: Path, backup_id: str) -> dict[str, Any]:
    # 先复用恢复入口的路径与完整性校验，再删除唯一备份目录。
    manifest, backup_dir, _ = load_backup(backup_root, backup_id)
    shutil.rmtree(backup_dir)
    return {"ok": True, "id": str(manifest["id"])}


def restore_backup(
    backup_root: Path,
    backup_id: str,
    target_db: Path,
    data_roots: dict[str, Path],
    media_crawler_target: Path | None = None,
) -> dict[str, Any]:
    manifest, backup_dir, source_db = load_backup(backup_root, backup_id)
    _validate_database(source_db, str(manifest.get("database_sha256") or ""), "业务数据库")

    media_source: Path | None = None
    media_metadata = manifest.get("media_crawler_database")
    if isinstance(media_metadata, dict):
        if media_crawler_target is None:
            raise ValueError("没有可用的 MyCrawler 数据库恢复位置")
        media_source = backup_dir / str(media_metadata.get("file") or MEDIA_CRAWLER_DATABASE_FILE)
        _validate_database(media_source, str(media_metadata.get("sha256") or ""), "MyCrawler 数据库")

    directories = manifest.get("data_directories")
    if not isinstance(directories, dict):
        raise ValueError("备份缺少业务文件清单")
    restore_sources: list[tuple[str, Path, Path]] = []
    for name, metadata in directories.items():
        safe_name = _safe_directory_name(name)
        destination = data_roots.get(safe_name)
        if destination is None:
            raise ValueError(f"备份包含未知业务目录：{safe_name}")
        expected_count = int(metadata.get("file_count") or 0) if isinstance(metadata, dict) else 0
        expected_size = int(metadata.get("size") or 0) if isinstance(metadata, dict) else 0
        expected_hash = str(metadata.get("tree_sha256") or "") if isinstance(metadata, dict) else ""
        source = backup_dir / DATA_DIR / safe_name
        if expected_count and not source.is_dir():
            raise ValueError(f"备份业务目录不完整：{safe_name}")
        snapshot = _directory_snapshot(source)
        if snapshot["file_count"] != expected_count or snapshot["size"] != expected_size:
            raise ValueError(f"备份业务目录数量或大小不符：{safe_name}")
        if expected_hash and snapshot["tree_sha256"] != expected_hash:
            raise ValueError(f"备份业务目录校验失败：{safe_name}")
        restore_sources.append((safe_name, source, destination))

    # 两份源数据库和全部文件先完成校验，再开始修改当前数据。
    _restore_database(source_db, target_db)
    if media_source and media_crawler_target:
        _restore_database(media_source, media_crawler_target)

    restored: dict[str, int] = {}
    for name, source_dir, destination in restore_sources:
        restored[name] = _sync_directory(source_dir, destination)["file_count"]
    return {
        "id": backup_id,
        "schema_version": int(manifest.get("schema_version") or 0),
        "media_crawler_database_restored": media_source is not None,
        "restored_files": restored,
    }


def load_backup(backup_root: Path, backup_id: str) -> tuple[dict[str, Any], Path, Path]:
    safe_id = str(backup_id or "").strip()
    if not safe_id or any(token in safe_id for token in ("/", "\\", "..")):
        raise ValueError("备份标识不合法")
    backup_dir = backup_root / safe_id
    manifest_path = backup_dir / MANIFEST_FILE
    if not manifest_path.is_file():
        raise ValueError("备份不存在或不完整")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_db = backup_dir / str(manifest.get("database_file") or DATABASE_FILE)
    if not source_db.is_file():
        raise ValueError("备份数据库文件不存在")
    return manifest, backup_dir, source_db


def _copy_directory(source: Path | None, destination: Path) -> dict[str, Any]:
    if source is None or not source.is_dir():
        return {"file_count": 0, "size": 0, "tree_sha256": hashlib.sha256().hexdigest()}
    count = 0
    size = 0
    tree_digest = hashlib.sha256()
    for item in sorted(source.rglob("*"), key=lambda value: value.relative_to(source).as_posix()):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        file_size, file_hash = _copy_file(item, target)
        _update_tree_digest(tree_digest, relative, file_size, file_hash)
        count += 1
        size += file_size
    return {"file_count": count, "size": size, "tree_sha256": tree_digest.hexdigest()}


def _sync_directory(source: Path, destination: Path) -> dict[str, Any]:
    expected = {
        item.relative_to(source)
        for item in source.rglob("*")
        if item.is_file()
    } if source.is_dir() else set()
    if destination.is_dir():
        for item in destination.rglob("*"):
            if item.is_file() and item.relative_to(destination) not in expected:
                item.unlink()
    return _copy_directory(source, destination)


def _directory_snapshot(source: Path) -> dict[str, Any]:
    if not source.is_dir():
        return {"file_count": 0, "size": 0, "tree_sha256": hashlib.sha256().hexdigest()}
    count = 0
    size = 0
    tree_digest = hashlib.sha256()
    for item in sorted(source.rglob("*"), key=lambda value: value.relative_to(source).as_posix()):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        file_size = item.stat().st_size
        file_hash = _sha256(item)
        _update_tree_digest(tree_digest, relative, file_size, file_hash)
        count += 1
        size += file_size
    return {"file_count": count, "size": size, "tree_sha256": tree_digest.hexdigest()}


def _copy_file(source: Path, destination: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as input_file, destination.open("wb") as output_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            output_file.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    shutil.copystat(source, destination)
    return size, digest.hexdigest()


def _update_tree_digest(digest: Any, relative: Path, size: int, file_hash: str) -> None:
    digest.update(relative.as_posix().encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(size).encode("ascii"))
    digest.update(b"\0")
    digest.update(file_hash.encode("ascii"))
    digest.update(b"\n")


def _backup_database(source_path: Path, destination_path: Path) -> None:
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
        integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"MyCrawler 备份完整性检查失败：{integrity}")
    finally:
        destination.close()
        source.close()


def _validate_database(path: Path, expected_hash: str, label: str) -> None:
    if not path.is_file() or not expected_hash or _sha256(path) != expected_hash:
        raise ValueError(f"{label}校验失败，已拒绝恢复")
    source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"{label}完整性检查失败：{integrity}")
    finally:
        source.close()


def _restore_database(source_path: Path, target_path: Path) -> None:
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()


def _safe_directory_name(value: str) -> str:
    name = str(value or "").strip()
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError("备份业务目录名称不合法")
    return name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_reason(value: str) -> str:
    reason = " ".join(str(value or "manual").strip().split())
    return reason[:120] or "manual"
