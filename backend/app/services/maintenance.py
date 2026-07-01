from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app import database


CONFIRM_TEXT = "清空所有数据"

PROJECT_DATA_TABLES = [
    "deletion_audit",
    "analysis_jobs",
    "deleted_identities",
    "raw_source_refs",
    "lead_status_events",
    "lead_sources",
    "lead_user_accounts",
    "account_sources",
    "comments",
    "contents",
    "user_accounts",
    "task_logs",
    "crawl_jobs",
]


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) AS c FROM {_quote_identifier(table)}").fetchone()["c"])


def _list_user_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [str(row["name"]) for row in rows]


def _reset_sequences(conn: sqlite3.Connection, tables: list[str]) -> None:
    has_sequence = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'"
    ).fetchone()
    if has_sequence and tables:
        conn.execute(
            f"DELETE FROM sqlite_sequence WHERE name IN ({','.join(['?'] * len(tables))})",
            tables,
        )


def clear_all_data(confirm: str) -> dict[str, Any]:
    if confirm != CONFIRM_TEXT:
        raise ValueError(f"确认文本不正确，请输入：{CONFIRM_TEXT}")

    with database.connect() as conn:
        # 接受用户从设置页粘贴的带引号路径，但不放宽确认文本。
        raw_db_value = str(database.get_setting(conn, "media_crawler_db_path", "")).strip().strip('"').strip("'")
        raw_db_path = Path(raw_db_value).expanduser()
        if not raw_db_path.exists():
            raise ValueError(f"MediaCrawler SQLite 不存在：{raw_db_path}")
        project_result = _clear_project_database(conn)

    raw_result = _clear_media_crawler_database(raw_db_path)
    return {
        "ok": True,
        "project": project_result,
        "media_crawler": raw_result,
    }


def _clear_project_database(conn: sqlite3.Connection) -> dict[str, Any]:
    deleted: dict[str, int] = {}
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for table in PROJECT_DATA_TABLES:
            deleted[table] = _table_count(conn, table)
            conn.execute(f"DELETE FROM {_quote_identifier(table)}")
        _reset_sequences(conn, PROJECT_DATA_TABLES)
        database.set_setting(conn, "next_task_number", "1")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    return {"tables": deleted, "rows": sum(deleted.values())}


def _clear_media_crawler_database(raw_db_path: Path) -> dict[str, Any]:
    raw_conn = sqlite3.connect(raw_db_path)
    raw_conn.row_factory = sqlite3.Row
    try:
        raw_conn.execute("PRAGMA foreign_keys = OFF")
        tables = _list_user_tables(raw_conn)
        deleted: dict[str, int] = {}
        for table in tables:
            deleted[table] = _table_count(raw_conn, table)
            raw_conn.execute(f"DELETE FROM {_quote_identifier(table)}")
        _reset_sequences(raw_conn, tables)
        raw_conn.commit()
    except Exception:
        raw_conn.rollback()
        raise
    finally:
        raw_conn.close()
    return {"path": str(raw_db_path), "tables": deleted, "rows": sum(deleted.values())}
