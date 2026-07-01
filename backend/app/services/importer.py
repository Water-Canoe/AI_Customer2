from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from app import database
from app.services import tombstones


CONTENT_TABLES = {
    "dy": {
        "table": "douyin_aweme",
        "id": "aweme_id",
        "url": "aweme_url",
        "title": "title",
        "desc": "desc",
        "like": "liked_count",
        "comment": "comment_count",
        "author_id": "user_id",
        "sec_uid": "sec_uid",
        "unique": "user_unique_id",
        "nickname": "nickname",
        "signature": "user_signature",
        "avatar": "avatar",
        "keyword": "source_keyword",
        "created": "create_time",
    },
    "xhs": {
        "table": "xhs_note",
        "id": "note_id",
        "url": "note_url",
        "title": "title",
        "desc": "desc",
        "like": "liked_count",
        "comment": "comment_count",
        "author_id": "user_id",
        "sec_uid": "",
        "unique": "",
        "nickname": "nickname",
        "signature": "",
        "avatar": "avatar",
        "keyword": "source_keyword",
        "created": "time",
    },
    "ks": {
        "table": "kuaishou_video",
        "id": "video_id",
        "url": "video_url",
        "title": "title",
        "desc": "desc",
        "like": "liked_count",
        "comment": "",
        "author_id": "user_id",
        "sec_uid": "",
        "unique": "",
        "nickname": "nickname",
        "signature": "",
        "avatar": "avatar",
        "keyword": "source_keyword",
        "created": "create_time",
    },
}

COMMENT_TABLES = {
    "dy": {
        "table": "douyin_aweme_comment",
        "id": "comment_id",
        "content_id": "aweme_id",
        "body": "content",
        "like": "like_count",
        "author_id": "user_id",
        "sec_uid": "sec_uid",
        "unique": "user_unique_id",
        "nickname": "nickname",
        "signature": "user_signature",
        "avatar": "avatar",
        "parent": "parent_comment_id",
        "created": "create_time",
    },
    "xhs": {
        "table": "xhs_note_comment",
        "id": "comment_id",
        "content_id": "note_id",
        "body": "content",
        "like": "like_count",
        "author_id": "user_id",
        "sec_uid": "",
        "unique": "",
        "nickname": "nickname",
        "signature": "",
        "avatar": "avatar",
        "parent": "parent_comment_id",
        "created": "create_time",
    },
    "ks": {
        "table": "kuaishou_video_comment",
        "id": "comment_id",
        "content_id": "video_id",
        "body": "content",
        "like": "",
        "author_id": "user_id",
        "sec_uid": "",
        "unique": "",
        "nickname": "nickname",
        "signature": "",
        "avatar": "avatar",
        "parent": "",
        "created": "create_time",
    },
}

CREATOR_TABLES = {
    "dy": {"table": "dy_creator", "id": "user_id", "nickname": "nickname", "signature": "desc", "avatar": "avatar", "fans": "fans", "content_total": "videos_count"},
    "xhs": {"table": "xhs_creator", "id": "user_id", "nickname": "nickname", "signature": "desc", "avatar": "avatar", "fans": "fans", "content_total": ""},
}


def import_for_task(task_id: str) -> dict[str, int]:
    with database.connect() as conn:
        task = conn.execute("SELECT * FROM crawl_jobs WHERE id = ?", (task_id,)).fetchone()
        if not task:
            raise ValueError(f"任务不存在：{task_id}")
        raw_db = database.get_setting(conn, "media_crawler_db_path", str(database.DEFAULT_MEDIA_CRAWLER_DB))
        raw_db = raw_db.strip().strip('"').strip("'")

    raw_path = Path(raw_db)
    if not raw_path.exists():
        raise FileNotFoundError(f"MediaCrawler SQLite 不存在：{raw_db}")

    raw_conn = sqlite3.connect(raw_path)
    raw_conn.row_factory = sqlite3.Row
    try:
        return _import_with_connections(raw_conn, task_id)
    finally:
        raw_conn.close()


def _import_with_connections(raw_conn: sqlite3.Connection, task_id: str) -> dict[str, int]:
    counts = {"accounts": 0, "contents": 0, "comments": 0, "leads": 0, "competitor_candidates": 0}
    with database.connect() as conn:
        task = conn.execute("SELECT * FROM crawl_jobs WHERE id = ?", (task_id,)).fetchone()
        assert task is not None
        platform = task["platform"]
        _import_creators(raw_conn, conn, platform, task, counts)
        if task["mode"] != "profile_enrichment":
            _import_contents(raw_conn, conn, platform, task, counts)
        if task["mode"] != "profile_enrichment" and task["collect_comments"]:
            _import_comments(raw_conn, conn, platform, task, counts)
        counts["competitor_candidates"] += _refresh_competitor_candidates_from_profiles(conn)
        _enqueue_auto_analysis(conn, task)
    return counts


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _table_columns(raw_conn: sqlite3.Connection, table: str) -> set[str]:
    rows = raw_conn.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
    return {str(row["name"]) for row in rows}


def _raw_time_clause(raw_conn: sqlite3.Connection, table: str, task: sqlite3.Row | None) -> tuple[str, list[int]]:
    if not task or not int(task["execute_crawler"]):
        return "", []
    raw_started_ts_ms = int(task["raw_started_ts_ms"] or 0)
    if raw_started_ts_ms <= 0:
        return "", []
    columns = _table_columns(raw_conn, table)
    timestamp_columns = [column for column in ("last_modify_ts", "add_ts") if column in columns]
    if not timestamp_columns:
        return "", []
    clause = " WHERE " + " OR ".join(f"CAST({_quote_identifier(column)} AS INTEGER) >= ?" for column in timestamp_columns)
    return clause, [raw_started_ts_ms] * len(timestamp_columns)


def _safe_select_rows(raw_conn: sqlite3.Connection, table: str, task: sqlite3.Row | None = None, limit: int | None = None) -> list[sqlite3.Row]:
    exists = raw_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone()
    if not exists:
        return []
    where_clause, params = _raw_time_clause(raw_conn, table, task)
    limit_clause = ""
    if limit is not None:
        limit_clause = " ORDER BY rowid DESC LIMIT ?"
        params = [*params, max(1, int(limit))]
    return raw_conn.execute(f"SELECT rowid AS __raw_pk, * FROM {_quote_identifier(table)}{where_clause}{limit_clause}", params).fetchall()


def _value(row: sqlite3.Row, column: str, default: Any = "") -> Any:
    if not column:
        return default
    try:
        value = row[column]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _int_or_none(value: Any) -> int | None:
    try:
        if value in ("", None):
            return None
        return int(float(str(value).replace(",", "")))
    except ValueError:
        return None


def _safe_int_setting(conn: sqlite3.Connection, key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(database.get_setting(conn, key, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _cutoff_ts_seconds(conn: sqlite3.Connection, setting_key: str) -> int | None:
    days = _safe_int_setting(conn, setting_key, 0, 0, 3650)
    if days <= 0:
        return None
    return int(time.time()) - days * 86400


def _raw_ts_seconds(row: sqlite3.Row, column: str) -> int | None:
    value = _int_or_none(_value(row, column))
    if value is None or value <= 0:
        return None
    # MediaCrawler platforms mix second and millisecond timestamps.
    return value // 1000 if value > 10_000_000_000 else value


def _content_passes_cutoff(row: sqlite3.Row, mapping: dict[str, str], cutoff_ts: int | None) -> bool:
    if cutoff_ts is None:
        return True
    content_ts = _raw_ts_seconds(row, mapping.get("created", ""))
    return content_ts is None or content_ts >= cutoff_ts


def _comment_passes_cutoff(row: sqlite3.Row, mapping: dict[str, str], cutoff_ts: int | None) -> bool:
    if cutoff_ts is None:
        return True
    comment_ts = _raw_ts_seconds(row, mapping.get("created", ""))
    return comment_ts is None or comment_ts >= cutoff_ts


def _content_native_passes_cutoff(
    raw_conn: sqlite3.Connection,
    platform: str,
    content_native_id: str,
    cutoff_ts: int | None,
    cache: dict[str, bool],
) -> bool:
    if cutoff_ts is None or not content_native_id:
        return True
    if content_native_id in cache:
        return cache[content_native_id]
    mapping = CONTENT_TABLES[platform]
    table = mapping["table"]
    id_column = mapping["id"]
    row = raw_conn.execute(
        f"SELECT * FROM {_quote_identifier(table)} WHERE {_quote_identifier(id_column)} = ? LIMIT 1",
        (content_native_id,),
    ).fetchone()
    if row is None:
        cache[content_native_id] = True
        return True
    result = _content_passes_cutoff(row, mapping, cutoff_ts)
    cache[content_native_id] = result
    return result


def _keyword_terms(keyword_source: str) -> list[str]:
    normalized = str(keyword_source or "")
    for separator in ("，", ",", "；", ";", "\n", "\t"):
        normalized = normalized.replace(separator, " ")
    return [term.lower() for term in normalized.split() if term]


def _matches_keyword(texts: list[str], keyword_source: str) -> bool:
    terms = _keyword_terms(keyword_source)
    if not terms:
        return True
    haystack = "\n".join(str(text or "") for text in texts).lower()
    return any(term in haystack for term in terms)


def _profile_url(platform: str, platform_user_id: str, sec_uid: str = "") -> str:
    if platform == "dy" and sec_uid:
        return f"https://www.douyin.com/user/{sec_uid}"
    if platform == "xhs":
        return f"https://www.xiaohongshu.com/user/profile/{platform_user_id}"
    if platform == "ks":
        return f"https://www.kuaishou.com/profile/{platform_user_id}"
    return ""


def _looks_like_douyin_sec_uid(value: str) -> bool:
    return value.startswith("MS4w")


def _upsert_account(
    conn: sqlite3.Connection,
    platform: str,
    platform_user_id: str,
    nickname: str = "",
    sec_uid: str = "",
    unique_id: str = "",
    avatar: str = "",
    signature: str = "",
    fans: int | None = None,
    content_total_count: int | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> int | None:
    if not platform_user_id:
        platform_user_id = f"unknown-{platform}-{nickname or 'user'}"
    platform_user_id = str(platform_user_id)
    sec_uid = str(sec_uid or "")
    if platform == "dy" and not sec_uid and _looks_like_douyin_sec_uid(platform_user_id):
        sec_uid = platform_user_id
    profile_url = _profile_url(platform, platform_user_id, sec_uid)
    raw_json = json.dumps(raw_payload or {}, ensure_ascii=False, default=str)
    existing = conn.execute(
        """
        SELECT id
        FROM user_accounts
        WHERE platform = ?
          AND (
            platform_user_id = ?
            OR (? <> '' AND sec_uid = ?)
            OR (? <> '' AND profile_url = ?)
          )
        ORDER BY
          CASE
            WHEN ? <> '' AND sec_uid = ? THEN 0
            WHEN ? <> '' AND profile_url = ? THEN 1
            ELSE 2
          END,
          id
        LIMIT 1
        """,
        (
            platform,
            platform_user_id,
            sec_uid,
            sec_uid,
            profile_url,
            profile_url,
            sec_uid,
            sec_uid,
            profile_url,
            profile_url,
        ),
    ).fetchone()
    if existing:
        # creator 表常用 sec_uid；优先回写到已有账号，避免总览树挂在旧账号上。
        conn.execute(
            """
            UPDATE user_accounts
            SET sec_uid = COALESCE(NULLIF(?, ''), sec_uid),
                user_unique_id = COALESCE(NULLIF(?, ''), user_unique_id),
                nickname = COALESCE(NULLIF(?, ''), nickname),
                avatar = COALESCE(NULLIF(?, ''), avatar),
                signature = COALESCE(NULLIF(?, ''), signature),
                profile_url = COALESCE(NULLIF(?, ''), profile_url),
                fans = COALESCE(?, fans),
                content_total_count = COALESCE(?, content_total_count),
                raw_payload = ?,
                updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (
                sec_uid,
                str(unique_id or ""),
                str(nickname or ""),
                str(avatar or ""),
                str(signature or ""),
                profile_url,
                fans,
                content_total_count,
                raw_json,
                int(existing["id"]),
            ),
        )
        return int(existing["id"])
    conn.execute(
        """
        INSERT INTO user_accounts(
            platform, platform_user_id, sec_uid, user_unique_id, nickname,
            avatar, signature, profile_url, fans, content_total_count, raw_payload
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(platform, platform_user_id) DO UPDATE SET
            sec_uid = COALESCE(NULLIF(excluded.sec_uid, ''), user_accounts.sec_uid),
            user_unique_id = COALESCE(NULLIF(excluded.user_unique_id, ''), user_accounts.user_unique_id),
            nickname = COALESCE(NULLIF(excluded.nickname, ''), user_accounts.nickname),
            avatar = COALESCE(NULLIF(excluded.avatar, ''), user_accounts.avatar),
            signature = COALESCE(NULLIF(excluded.signature, ''), user_accounts.signature),
            profile_url = COALESCE(NULLIF(excluded.profile_url, ''), user_accounts.profile_url),
            fans = COALESCE(excluded.fans, user_accounts.fans),
            content_total_count = COALESCE(excluded.content_total_count, user_accounts.content_total_count),
            raw_payload = excluded.raw_payload,
            updated_at = datetime('now', 'localtime')
        """,
        (
            platform,
            str(platform_user_id),
            str(sec_uid or ""),
            str(unique_id or ""),
            str(nickname or ""),
            str(avatar or ""),
            str(signature or ""),
            profile_url,
            fans,
            content_total_count,
            raw_json,
        ),
    )
    row = conn.execute(
        "SELECT id FROM user_accounts WHERE platform = ? AND platform_user_id = ?",
        (platform, str(platform_user_id)),
    ).fetchone()
    return int(row["id"])


def _add_raw_ref(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: int,
    platform: str,
    raw_table: str,
    raw_pk: Any,
    task_id: str,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO raw_source_refs(entity_type, entity_id, platform, raw_table, raw_pk, task_id)
        VALUES(?, ?, ?, ?, ?, ?)
        """,
        (entity_type, entity_id, platform, raw_table, str(raw_pk), task_id),
    )


def _import_creators(
    raw_conn: sqlite3.Connection,
    conn: sqlite3.Connection,
    platform: str,
    task: sqlite3.Row,
    counts: dict[str, int],
) -> None:
    mapping = CREATOR_TABLES.get(platform)
    if not mapping:
        return
    for row in _safe_select_rows(raw_conn, mapping["table"], task):
        platform_user_id = str(_value(row, mapping["id"]))
        if tombstones.is_deleted_author(conn, platform, platform_user_id):
            continue
        account_id = _upsert_account(
            conn,
            platform,
            platform_user_id,
            nickname=str(_value(row, mapping["nickname"])),
            avatar=str(_value(row, mapping["avatar"])),
            signature=str(_value(row, mapping["signature"])),
            fans=_int_or_none(_value(row, mapping["fans"])),
            content_total_count=_int_or_none(_value(row, mapping.get("content_total", ""))),
            raw_payload=dict(row),
        )
        if account_id is None:
            continue
        _add_raw_ref(conn, "account", account_id, platform, mapping["table"], row["__raw_pk"], task["id"])
        counts["accounts"] += 1


def _import_contents(
    raw_conn: sqlite3.Connection,
    conn: sqlite3.Connection,
    platform: str,
    task: sqlite3.Row,
    counts: dict[str, int],
) -> None:
    mapping = CONTENT_TABLES[platform]
    content_limit = int(task["content_count"] or 5) if task["mode"] == "account_analysis" else None
    search_limit = int(task["content_count"] or 20) if task["crawler_type"] == "search" else None
    content_cutoff_ts = _cutoff_ts_seconds(conn, "content_cutoff_days")
    rows = _safe_select_rows(raw_conn, mapping["table"], task, limit=None)
    per_account_counts: dict[str, int] = {}
    per_keyword_counts: dict[str, int] = {}
    if content_limit is not None:
        rows = sorted(rows, key=lambda item: int(item["__raw_pk"]), reverse=True)
    for row in rows:
        if not _content_passes_cutoff(row, mapping, content_cutoff_ts):
            continue
        content_native_id = str(_value(row, mapping["id"]))
        if not content_native_id:
            continue
        if tombstones.is_deleted_native(conn, "content", platform, content_native_id):
            continue
        author_platform_user_id = str(_value(row, mapping["author_id"]))
        author_sec_uid = str(_value(row, mapping["sec_uid"]))
        author_unique_id = str(_value(row, mapping["unique"]))
        author_profile_url = _profile_url(platform, author_platform_user_id, author_sec_uid)
        if tombstones.is_deleted_author(conn, platform, author_platform_user_id, author_sec_uid, author_unique_id, author_profile_url):
            continue
        author_key = str(_value(row, mapping["author_id"])) or str(_value(row, mapping["sec_uid"]))
        if content_limit is not None:
            imported_for_account = per_account_counts.get(author_key, 0)
            if imported_for_account >= content_limit:
                continue
        keyword_key = str(_value(row, mapping["keyword"])) or str(task["keywords"] or "")
        if search_limit is not None:
            imported_for_keyword = per_keyword_counts.get(keyword_key, 0)
            if imported_for_keyword >= search_limit:
                continue
        author_nickname = str(_value(row, mapping["nickname"]))
        author_signature = "" if task["mode"] in ("profile_enrichment", "account_analysis") else str(_value(row, mapping["signature"]))
        account_id = _upsert_account(
            conn,
            platform,
            author_platform_user_id,
            nickname=author_nickname,
            sec_uid=author_sec_uid,
            unique_id=author_unique_id,
            avatar=str(_value(row, mapping["avatar"])),
            signature=author_signature,
            raw_payload=dict(row),
        )
        if account_id is None:
            continue
        account_row = conn.execute("SELECT nickname, signature FROM user_accounts WHERE id = ?", (account_id,)).fetchone()
        effective_nickname = str(account_row["nickname"] or author_nickname) if account_row else author_nickname
        effective_signature = str(account_row["signature"] or author_signature) if account_row else author_signature
        conn.execute(
            """
            INSERT INTO contents(
                platform, content_id, author_account_id, title, description,
                content_url, like_count, comment_count, source_keyword, task_id, raw_payload
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, content_id) DO UPDATE SET
                author_account_id = excluded.author_account_id,
                title = excluded.title,
                description = excluded.description,
                content_url = excluded.content_url,
                like_count = excluded.like_count,
                comment_count = excluded.comment_count,
                source_keyword = COALESCE(NULLIF(excluded.source_keyword, ''), contents.source_keyword),
                task_id = excluded.task_id,
                raw_payload = excluded.raw_payload,
                updated_at = datetime('now', 'localtime')
            """,
            (
                platform,
                content_native_id,
                account_id,
                str(_value(row, mapping["title"])),
                str(_value(row, mapping["desc"])),
                str(_value(row, mapping["url"])),
                _int_or_none(_value(row, mapping["like"])),
                _int_or_none(_value(row, mapping["comment"])),
                str(_value(row, mapping["keyword"])),
                task["id"],
                json.dumps(dict(row), ensure_ascii=False, default=str),
            ),
        )
        content_id = int(conn.execute("SELECT id FROM contents WHERE platform = ? AND content_id = ?", (platform, content_native_id)).fetchone()["id"])
        _add_raw_ref(conn, "content", content_id, platform, mapping["table"], row["__raw_pk"], task["id"])
        counts["contents"] += 1
        if content_limit is not None:
            per_account_counts[author_key] = per_account_counts.get(author_key, 0) + 1
        if search_limit is not None:
            per_keyword_counts[keyword_key] = per_keyword_counts.get(keyword_key, 0) + 1
        if task["mode"] in ("competitor_discovery", "demand_content"):
            if _handle_keyword_author(conn, task, account_id, content_id, str(_value(row, mapping["keyword"])), effective_nickname, effective_signature):
                counts["competitor_candidates"] += 1


def _handle_keyword_author(
    conn: sqlite3.Connection,
    task: sqlite3.Row,
    account_id: int,
    content_id: int,
    keyword: str,
    nickname: str,
    signature: str,
) -> bool:
    keyword_source = keyword or task["keywords"]
    if task["mode"] == "competitor_discovery":
        existing = conn.execute(
            """
            SELECT 1 FROM account_sources
            WHERE account_id = ? AND content_id = ? AND keyword = ? AND task_id = ? AND source_kind = 'keyword_author'
            """,
            (account_id, content_id, keyword_source, task["id"]),
        ).fetchone()
        conn.execute(
            """
            UPDATE user_accounts
            SET account_role = CASE
                WHEN account_role = 'competitor' THEN account_role
                ELSE 'competitor_candidate'
            END,
            updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (account_id,),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO account_sources(account_id, content_id, keyword, task_id, source_kind)
            VALUES(?, ?, ?, ?, 'keyword_author')
            """,
            (account_id, content_id, keyword_source, task["id"]),
        )
        return existing is None
    elif task["mode"] == "demand_content":
        lead_id = _ensure_lead(conn, account_id)
        conn.execute(
            """
            INSERT OR IGNORE INTO lead_sources(lead_account_id, content_id, keyword, source_type, task_id)
            VALUES(?, ?, ?, 'demand_content', ?)
            """,
            (lead_id, content_id, keyword_source, task["id"]),
        )
    return False


def _refresh_competitor_candidates_from_profiles(conn: sqlite3.Connection) -> int:
    """Promote keyword authors after profile enrichment fills homepage signatures."""
    rows = conn.execute(
        """
        SELECT c.id AS content_id, c.source_keyword, j.id AS task_id, j.keywords,
               ua.id AS account_id, ua.nickname, ua.signature
        FROM contents c
        JOIN crawl_jobs j ON j.id = c.task_id
        JOIN user_accounts ua ON ua.id = c.author_account_id
        WHERE j.mode = 'competitor_discovery'
          AND COALESCE(ua.signature, '') <> ''
        """
    ).fetchall()
    promoted = 0
    for row in rows:
        keyword_source = str(row["source_keyword"] or row["keywords"] or "")
        if not _matches_keyword([str(row["nickname"] or ""), str(row["signature"] or "")], keyword_source):
            continue
        exists = conn.execute(
            """
            SELECT 1 FROM account_sources
            WHERE account_id = ? AND content_id = ? AND keyword = ? AND task_id = ? AND source_kind = 'keyword_author'
            """,
            (row["account_id"], row["content_id"], keyword_source, row["task_id"]),
        ).fetchone()
        conn.execute(
            """
            UPDATE user_accounts
            SET account_role = CASE
                WHEN account_role = 'competitor' THEN account_role
                ELSE 'competitor_candidate'
            END,
            updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (row["account_id"],),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO account_sources(account_id, content_id, keyword, task_id, source_kind)
            VALUES(?, ?, ?, ?, 'keyword_author')
            """,
            (row["account_id"], row["content_id"], keyword_source, row["task_id"]),
        )
        if not exists:
            promoted += 1
    return promoted


def _import_comments(
    raw_conn: sqlite3.Connection,
    conn: sqlite3.Connection,
    platform: str,
    task: sqlite3.Row,
    counts: dict[str, int],
) -> None:
    mapping = COMMENT_TABLES[platform]
    comment_cutoff_ts = _cutoff_ts_seconds(conn, "comment_cutoff_days")
    content_cutoff_ts = _cutoff_ts_seconds(conn, "content_cutoff_days")
    content_cutoff_cache: dict[str, bool] = {}
    for row in _safe_select_rows(raw_conn, mapping["table"], task):
        if not _comment_passes_cutoff(row, mapping, comment_cutoff_ts):
            continue
        comment_native_id = str(_value(row, mapping["id"]))
        if not comment_native_id:
            continue
        if tombstones.is_deleted_native(conn, "comment", platform, comment_native_id):
            continue
        content_native_id = str(_value(row, mapping["content_id"]))
        if tombstones.is_deleted_native(conn, "content", platform, content_native_id):
            continue
        if not _content_native_passes_cutoff(raw_conn, platform, content_native_id, content_cutoff_ts, content_cutoff_cache):
            continue
        author_platform_user_id = str(_value(row, mapping["author_id"]))
        author_sec_uid = str(_value(row, mapping["sec_uid"]))
        author_unique_id = str(_value(row, mapping["unique"]))
        content_row = conn.execute(
            "SELECT id, author_account_id, source_keyword FROM contents WHERE platform = ? AND content_id = ?",
            (platform, content_native_id),
        ).fetchone()
        if content_row is None:
            continue
        account_id = _upsert_account(
            conn,
            platform,
            author_platform_user_id,
            nickname=str(_value(row, mapping["nickname"])),
            sec_uid=author_sec_uid,
            unique_id=author_unique_id,
            avatar=str(_value(row, mapping["avatar"])),
            signature=str(_value(row, mapping["signature"])),
            raw_payload=dict(row),
        )
        if account_id is None:
            continue
        conn.execute(
            """
            INSERT INTO comments(
                platform, comment_id, content_id, author_account_id, parent_comment_id,
                body, like_count, task_id, raw_payload
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, comment_id) DO UPDATE SET
                content_id = excluded.content_id,
                author_account_id = excluded.author_account_id,
                parent_comment_id = excluded.parent_comment_id,
                body = excluded.body,
                like_count = excluded.like_count,
                task_id = excluded.task_id,
                raw_payload = excluded.raw_payload,
                updated_at = datetime('now', 'localtime')
            """,
            (
                platform,
                comment_native_id,
                int(content_row["id"]) if content_row else None,
                account_id,
                str(_value(row, mapping["parent"])),
                str(_value(row, mapping["body"])),
                _int_or_none(_value(row, mapping["like"])),
                task["id"],
                json.dumps(dict(row), ensure_ascii=False, default=str),
            ),
        )
        comment_id = int(conn.execute("SELECT id FROM comments WHERE platform = ? AND comment_id = ?", (platform, comment_native_id)).fetchone()["id"])
        _add_raw_ref(conn, "comment", comment_id, platform, mapping["table"], row["__raw_pk"], task["id"])
        counts["comments"] += 1
        if task["mode"] in ("competitor_crawl", "own_account"):
            lead_id = _ensure_lead(conn, account_id)
            source_account_id = int(content_row["author_account_id"]) if content_row and content_row["author_account_id"] else None
            source_keyword = str(content_row["source_keyword"] if content_row else "") or str(task["keywords"] or "")
            conn.execute(
                """
                INSERT OR IGNORE INTO lead_sources(
                    lead_account_id, source_account_id, content_id, comment_id, keyword, source_type, task_id
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lead_id,
                    source_account_id,
                    int(content_row["id"]) if content_row else None,
                    comment_id,
                    source_keyword,
                    task["mode"],
                    task["id"],
                ),
            )
            counts["leads"] += 1


def _ensure_lead(conn: sqlite3.Connection, account_id: int) -> int:
    conn.execute(
        """
        INSERT OR IGNORE INTO lead_user_accounts(account_id, screening_status, follow_status)
        VALUES(?, '待筛选', '待筛选')
        """,
        (account_id,),
    )
    row = conn.execute("SELECT id FROM lead_user_accounts WHERE account_id = ?", (account_id,)).fetchone()
    return int(row["id"])


def _enqueue_auto_analysis(conn: sqlite3.Connection, task: sqlite3.Row) -> None:
    # 线索自动分析仍是纯 AI 队列；竞品自动分析需要先跑 creator 补资料，由 crawler_adapter 接管。
    auto_lead = database.get_setting(conn, "auto_analyze_leads", "false") == "true"
    if auto_lead and task["mode"] in ("competitor_crawl", "own_account", "demand_content"):
        rows = conn.execute("SELECT id FROM lead_user_accounts WHERE follow_status = '待筛选'").fetchall()
        for row in rows:
            _insert_analysis_job(conn, "lead", int(row["id"]))


def _insert_analysis_job(conn: sqlite3.Connection, target_type: str, target_id: int) -> None:
    existing = conn.execute(
        """
        SELECT 1
        FROM analysis_jobs
        WHERE target_type = ?
          AND target_id = ?
          AND status IN ('pending', 'running')
        LIMIT 1
        """,
        (target_type, target_id),
    ).fetchone()
    if existing:
        return
    job_id = f"{target_type}-{target_id}-{int(__import__('time').time() * 1000)}"
    conn.execute(
        "INSERT OR IGNORE INTO analysis_jobs(id, target_type, target_id) VALUES(?, ?, ?)",
        (job_id, target_type, target_id),
    )
