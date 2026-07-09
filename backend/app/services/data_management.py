from __future__ import annotations

import sqlite3
from typing import Any

from app import data_lifecycle, database, migrations


RESTORE_CONFIRM_TEXT = "恢复备份"
ACTIVE_JOB_QUERIES = (
    ("crawl_jobs", "status IN ('running')"),
    ("analysis_jobs", "status IN ('running')"),
    ("message_batches", "status IN ('running')"),
    ("traffic_runs", "status IN ('queued', 'running')"),
)


def create_backup(reason: str = "manual") -> dict[str, Any]:
    path = database.get_db_path()
    with database.connect(path) as conn:
        return data_lifecycle.create_backup(
            conn,
            database.get_backup_root(path),
            reason=reason,
            schema_version=migrations.current_version(conn),
            assets_root=path.parent / "traffic_images",
        )


def list_backups() -> dict[str, Any]:
    items = data_lifecycle.list_backups(database.get_backup_root())
    return {
        "items": items,
        "total": len(items),
        "schema": database.schema_version(),
    }


def restore_backup(backup_id: str, confirm: str) -> dict[str, Any]:
    if confirm != RESTORE_CONFIRM_TEXT:
        raise ValueError(f"确认文本不正确，请输入：{RESTORE_CONFIRM_TEXT}")
    active = active_jobs()
    if active:
        summary = "、".join(f"{item['table']} {item['count']} 个" for item in active)
        raise ValueError(f"仍有运行中任务，不能恢复备份：{summary}")

    safety_backup = create_backup(reason=f"pre_restore_{backup_id}")
    path = database.get_db_path()
    restored = data_lifecycle.restore_backup(
        database.get_backup_root(path),
        backup_id,
        path,
        path.parent / "traffic_images",
    )
    database.init_db()
    return {
        "ok": True,
        "restored": restored,
        "safety_backup": safety_backup,
        "schema": database.schema_version(),
    }


def active_jobs() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with database.connect() as conn:
        tables = _table_names(conn)
        for table, condition in ACTIVE_JOB_QUERIES:
            if table not in tables:
                continue
            count = int(conn.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE {condition}").fetchone()["c"])
            if count:
                results.append({"table": table, "count": count})
    return results


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row["name"]) for row in rows}
