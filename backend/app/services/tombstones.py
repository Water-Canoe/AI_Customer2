from __future__ import annotations

import json
import sqlite3
from typing import Any

from app import database


def record_deleted_identity(
    conn: sqlite3.Connection,
    entity_type: str,
    platform: str,
    identifier_type: str,
    identifier_value: Any,
    source: str,
    snapshot: dict[str, Any] | None = None,
) -> int:
    value = str(identifier_value or "").strip()
    if not value:
        return 0
    conn.execute(
        """
        INSERT INTO deleted_identities(
            entity_type, platform, identifier_type, identifier_value, source, snapshot
        )
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(entity_type, platform, identifier_type, identifier_value) DO UPDATE SET
            source = excluded.source,
            snapshot = excluded.snapshot,
            updated_at = datetime('now', 'localtime')
        """,
        (
            entity_type,
            platform,
            identifier_type,
            value,
            source,
            json.dumps(snapshot or {}, ensure_ascii=False, default=str),
        ),
    )
    return 1


def record_author_row(conn: sqlite3.Connection, row: sqlite3.Row | dict[str, Any], source: str) -> int:
    item = _row_to_dict(row)
    platform = str(item.get("platform") or "").strip()
    if not platform:
        return 0
    # Store stable author identities. This only blocks future authored content, not future comments by this account.
    count = 0
    for identifier_type, key in (
        ("platform_user_id", "platform_user_id"),
        ("sec_uid", "sec_uid"),
        ("user_unique_id", "user_unique_id"),
        ("profile_url", "profile_url"),
    ):
        count += record_deleted_identity(conn, "author_account", platform, identifier_type, item.get(key), source, item)
    return count


def record_account_row(conn: sqlite3.Connection, row: sqlite3.Row | dict[str, Any], source: str) -> int:
    return record_author_row(conn, row, source)


def record_content_row(conn: sqlite3.Connection, row: sqlite3.Row | dict[str, Any], source: str) -> int:
    item = _row_to_dict(row)
    return record_deleted_identity(conn, "content", str(item.get("platform") or ""), "native_id", item.get("content_id"), source, item)


def record_comment_row(conn: sqlite3.Connection, row: sqlite3.Row | dict[str, Any], source: str) -> int:
    item = _row_to_dict(row)
    return record_deleted_identity(conn, "comment", str(item.get("platform") or ""), "native_id", item.get("comment_id"), source, item)


def record_scope(conn: sqlite3.Connection, scope: dict[str, Any], source: str) -> dict[str, int]:
    counts = {"accounts": 0, "contents": 0, "comments": 0}
    for row in _rows_by_ids(conn, "user_accounts", scope.get("account_ids", set())):
        counts["accounts"] += record_account_row(conn, row, source)
    for row in _rows_by_ids(conn, "contents", scope.get("content_ids", set())):
        counts["contents"] += record_content_row(conn, row, source)
    for row in _rows_by_ids(conn, "comments", scope.get("comment_ids", set())):
        counts["comments"] += record_comment_row(conn, row, source)
    return counts


def record_task_content_and_comments(conn: sqlite3.Connection, task_id: str, source: str) -> dict[str, int]:
    counts = {"accounts": 0, "contents": 0, "comments": 0}
    content_rows = conn.execute("SELECT * FROM contents WHERE task_id = ?", (task_id,)).fetchall()
    comment_rows = conn.execute("SELECT * FROM comments WHERE task_id = ?", (task_id,)).fetchall()
    for row in content_rows:
        counts["contents"] += record_content_row(conn, row, source)
    for row in comment_rows:
        counts["comments"] += record_comment_row(conn, row, source)
    return counts


def is_deleted_native(conn: sqlite3.Connection, entity_type: str, platform: str, native_id: Any) -> bool:
    value = str(native_id or "").strip()
    if not value:
        return False
    row = conn.execute(
        """
        SELECT 1
        FROM deleted_identities
        WHERE entity_type = ?
          AND platform = ?
          AND identifier_type = 'native_id'
          AND identifier_value = ?
        LIMIT 1
        """,
        (entity_type, platform, value),
    ).fetchone()
    return row is not None


def is_deleted_author(
    conn: sqlite3.Connection,
    platform: str,
    platform_user_id: Any,
    sec_uid: Any = "",
    user_unique_id: Any = "",
    profile_url: Any = "",
) -> bool:
    identifiers = [
        ("platform_user_id", platform_user_id),
        ("sec_uid", sec_uid),
        ("user_unique_id", user_unique_id),
        ("profile_url", profile_url),
    ]
    pairs = [(kind, str(value or "").strip()) for kind, value in identifiers if str(value or "").strip()]
    if not pairs:
        return False
    clauses = " OR ".join(["(identifier_type = ? AND identifier_value = ?)"] * len(pairs))
    params: list[Any] = []
    for kind, value in pairs:
        params.extend([kind, value])
    row = conn.execute(
        f"""
        SELECT 1
        FROM deleted_identities
        WHERE entity_type = 'author_account'
          AND platform = ?
          AND ({clauses})
        LIMIT 1
        """,
        (platform, *params),
    ).fetchone()
    return row is not None


def is_deleted_account(
    conn: sqlite3.Connection,
    platform: str,
    platform_user_id: Any,
    sec_uid: Any = "",
    user_unique_id: Any = "",
    profile_url: Any = "",
) -> bool:
    return is_deleted_author(conn, platform, platform_user_id, sec_uid, user_unique_id, profile_url)


def _rows_by_ids(conn: sqlite3.Connection, table: str, ids: Any) -> list[sqlite3.Row]:
    id_set = {int(item) for item in ids or []}
    if not id_set:
        return []
    placeholders = ",".join(["?"] * len(id_set))
    return conn.execute(f"SELECT * FROM {table} WHERE id IN ({placeholders})", sorted(id_set)).fetchall()


def _row_to_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return database.row_to_dict(row) or {}
    return dict(row)
