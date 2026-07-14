from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app import data_lifecycle, database, migrations


RESTORE_CONFIRM_TEXT = "恢复备份"
ACTIVE_JOB_QUERIES = (
    ("runtime_jobs", "status IN ('queued', 'running')"),
    ("automation_runs", "status IN ('queued', 'running')"),
    ("crawl_jobs", "status IN ('pending', 'running')"),
    ("analysis_jobs", "status IN ('pending', 'running')"),
    ("message_batches", "status IN ('pending', 'running')"),
    ("traffic_runs", "status IN ('queued', 'running')"),
    ("video_jobs", "status IN ('queued', 'running')"),
    ("publish_tasks", "status IN ('queued', 'running')"),
)
_MAINTENANCE_LOCK = threading.RLock()
_MAINTENANCE_DEPTH = 0


def create_backup(reason: str = "manual") -> dict[str, Any]:
    with maintenance_window("创建备份"):
        path = database.get_db_path()
        with database.connect(path) as conn:
            return data_lifecycle.create_backup(
                conn,
                database.get_backup_root(path),
                reason=reason,
                schema_version=migrations.current_version(conn),
                data_roots=database.get_backup_data_roots(),
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
    with maintenance_window("恢复备份"):
        safety_backup = create_backup(reason=f"pre_restore_{backup_id}")
        path = database.get_db_path()
        restored = data_lifecycle.restore_backup(
            database.get_backup_root(path),
            backup_id,
            path,
            database.get_backup_data_roots(),
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


def ensure_idle(action: str) -> None:
    active = active_jobs()
    if not active:
        return
    summary = "、".join(f"{item['table']} {item['count']} 个" for item in active)
    raise ValueError(f"仍有排队或运行中的任务，不能{action}：{summary}")


def maintenance_active() -> bool:
    return _MAINTENANCE_DEPTH > 0


@contextmanager
def maintenance_window(action: str) -> Iterator[None]:
    global _MAINTENANCE_DEPTH
    with _MAINTENANCE_LOCK:
        outermost = _MAINTENANCE_DEPTH == 0
        scheduler_was_running = False
        _MAINTENANCE_DEPTH += 1
        try:
            if outermost:
                from app.services import automation_workbench

                scheduler_was_running = automation_workbench.scheduler_running()
                if scheduler_was_running:
                    automation_workbench.stop_scheduler()
                ensure_idle(action)
            yield
        finally:
            _MAINTENANCE_DEPTH -= 1
            if outermost and scheduler_was_running:
                from app.services import automation_workbench

                automation_workbench.start_scheduler()


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row["name"]) for row in rows}
