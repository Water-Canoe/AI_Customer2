from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


DATABASE_FILE = "ai_customer.sqlite3"
MANIFEST_FILE = "manifest.json"
ASSET_DIR = "traffic_images"


def create_backup(
    source_conn: sqlite3.Connection,
    backup_root: Path,
    reason: str,
    schema_version: int,
    assets_root: Path | None = None,
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

    asset_count = _copy_assets(assets_root, backup_dir / ASSET_DIR)
    manifest = {
        "id": backup_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "reason": _clean_reason(reason),
        "schema_version": schema_version,
        "database_file": DATABASE_FILE,
        "database_size": database_path.stat().st_size,
        "database_sha256": _sha256(database_path),
        "asset_dir": ASSET_DIR,
        "asset_count": asset_count,
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
        manifest["available"] = database_path.is_file()
        results.append(manifest)
    return sorted(results, key=lambda item: str(item.get("id") or ""), reverse=True)


def restore_backup(backup_root: Path, backup_id: str, target_db: Path, assets_root: Path) -> dict[str, Any]:
    manifest, backup_dir, source_db = load_backup(backup_root, backup_id)
    expected_hash = str(manifest.get("database_sha256") or "")
    actual_hash = _sha256(source_db)
    if not expected_hash or actual_hash != expected_hash:
        raise ValueError("备份数据库校验失败，已拒绝恢复")

    source = sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True)
    target_db.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(target_db)
    try:
        integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"备份数据库完整性检查失败：{integrity}")
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()

    restored_assets = _copy_assets(backup_dir / ASSET_DIR, assets_root)
    return {
        "id": backup_id,
        "schema_version": int(manifest.get("schema_version") or 0),
        "restored_assets": restored_assets,
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


def _copy_assets(source: Path | None, destination: Path) -> int:
    if source is None or not source.is_dir():
        return 0
    count = 0
    for item in source.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        count += 1
    return count


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_reason(value: str) -> str:
    reason = " ".join(str(value or "manual").strip().split())
    return reason[:120] or "manual"
