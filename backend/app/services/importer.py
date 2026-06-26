from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app import database


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
    },
}

CREATOR_TABLES = {
    "dy": {"table": "dy_creator", "id": "user_id", "nickname": "nickname", "signature": "desc", "avatar": "avatar", "fans": "fans"},
    "xhs": {"table": "xhs_creator", "id": "user_id", "nickname": "nickname", "signature": "desc", "avatar": "avatar", "fans": "fans"},
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
    counts = {"accounts": 0, "contents": 0, "comments": 0, "leads": 0}
    with database.connect() as conn:
        task = conn.execute("SELECT * FROM crawl_jobs WHERE id = ?", (task_id,)).fetchone()
        assert task is not None
        platform = task["platform"]
        _import_creators(raw_conn, conn, platform, task_id, counts)
        _import_contents(raw_conn, conn, platform, task, counts)
        if task["collect_comments"]:
            _import_comments(raw_conn, conn, platform, task, counts)
        _enqueue_auto_analysis(conn, task)
    return counts


def _safe_select_all(raw_conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    exists = raw_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone()
    if not exists:
        return []
    return raw_conn.execute(f"SELECT rowid AS __raw_pk, * FROM {table}").fetchall()


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
    raw_payload: dict[str, Any] | None = None,
) -> int:
    if not platform_user_id:
        platform_user_id = f"unknown-{platform}-{nickname or 'user'}"
    raw_json = json.dumps(raw_payload or {}, ensure_ascii=False, default=str)
    conn.execute(
        """
        INSERT INTO user_accounts(
            platform, platform_user_id, sec_uid, user_unique_id, nickname,
            avatar, signature, profile_url, fans, raw_payload
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(platform, platform_user_id) DO UPDATE SET
            sec_uid = COALESCE(NULLIF(excluded.sec_uid, ''), user_accounts.sec_uid),
            user_unique_id = COALESCE(NULLIF(excluded.user_unique_id, ''), user_accounts.user_unique_id),
            nickname = COALESCE(NULLIF(excluded.nickname, ''), user_accounts.nickname),
            avatar = COALESCE(NULLIF(excluded.avatar, ''), user_accounts.avatar),
            signature = COALESCE(NULLIF(excluded.signature, ''), user_accounts.signature),
            profile_url = COALESCE(NULLIF(excluded.profile_url, ''), user_accounts.profile_url),
            fans = COALESCE(excluded.fans, user_accounts.fans),
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
            _profile_url(platform, str(platform_user_id), str(sec_uid or "")),
            fans,
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
    task_id: str,
    counts: dict[str, int],
) -> None:
    mapping = CREATOR_TABLES.get(platform)
    if not mapping:
        return
    for row in _safe_select_all(raw_conn, mapping["table"]):
        account_id = _upsert_account(
            conn,
            platform,
            str(_value(row, mapping["id"])),
            nickname=str(_value(row, mapping["nickname"])),
            avatar=str(_value(row, mapping["avatar"])),
            signature=str(_value(row, mapping["signature"])),
            fans=_int_or_none(_value(row, mapping["fans"])),
            raw_payload=dict(row),
        )
        _add_raw_ref(conn, "account", account_id, platform, mapping["table"], row["__raw_pk"], task_id)
        counts["accounts"] += 1


def _import_contents(
    raw_conn: sqlite3.Connection,
    conn: sqlite3.Connection,
    platform: str,
    task: sqlite3.Row,
    counts: dict[str, int],
) -> None:
    mapping = CONTENT_TABLES[platform]
    for row in _safe_select_all(raw_conn, mapping["table"]):
        content_native_id = str(_value(row, mapping["id"]))
        if not content_native_id:
            continue
        author_nickname = str(_value(row, mapping["nickname"]))
        author_signature = "" if task["mode"] == "profile_enrichment" else str(_value(row, mapping["signature"]))
        account_id = _upsert_account(
            conn,
            platform,
            str(_value(row, mapping["author_id"])),
            nickname=author_nickname,
            sec_uid=str(_value(row, mapping["sec_uid"])),
            unique_id=str(_value(row, mapping["unique"])),
            avatar=str(_value(row, mapping["avatar"])),
            signature=author_signature,
            raw_payload=dict(row),
        )
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
        if task["mode"] in ("competitor_discovery", "demand_content"):
            _handle_keyword_author(conn, task, account_id, content_id, str(_value(row, mapping["keyword"])), effective_nickname, effective_signature)


def _handle_keyword_author(
    conn: sqlite3.Connection,
    task: sqlite3.Row,
    account_id: int,
    content_id: int,
    keyword: str,
    nickname: str,
    signature: str,
) -> None:
    keyword_source = keyword or task["keywords"]
    if task["mode"] == "competitor_discovery":
        if not _matches_keyword([nickname, signature], keyword_source):
            return
        conn.execute("UPDATE user_accounts SET account_role = 'competitor_candidate' WHERE id = ?", (account_id,))
        conn.execute(
            """
            INSERT OR IGNORE INTO account_sources(account_id, content_id, keyword, task_id, source_kind)
            VALUES(?, ?, ?, ?, 'keyword_author')
            """,
            (account_id, content_id, keyword_source, task["id"]),
        )
    elif task["mode"] == "demand_content":
        lead_id = _ensure_lead(conn, account_id)
        conn.execute(
            """
            INSERT OR IGNORE INTO lead_sources(lead_account_id, content_id, keyword, source_type, task_id)
            VALUES(?, ?, ?, 'demand_content', ?)
            """,
            (lead_id, content_id, keyword_source, task["id"]),
        )


def _import_comments(
    raw_conn: sqlite3.Connection,
    conn: sqlite3.Connection,
    platform: str,
    task: sqlite3.Row,
    counts: dict[str, int],
) -> None:
    mapping = COMMENT_TABLES[platform]
    for row in _safe_select_all(raw_conn, mapping["table"]):
        comment_native_id = str(_value(row, mapping["id"]))
        if not comment_native_id:
            continue
        content_native_id = str(_value(row, mapping["content_id"]))
        content_row = conn.execute(
            "SELECT id, author_account_id, source_keyword FROM contents WHERE platform = ? AND content_id = ?",
            (platform, content_native_id),
        ).fetchone()
        account_id = _upsert_account(
            conn,
            platform,
            str(_value(row, mapping["author_id"])),
            nickname=str(_value(row, mapping["nickname"])),
            sec_uid=str(_value(row, mapping["sec_uid"])),
            unique_id=str(_value(row, mapping["unique"])),
            avatar=str(_value(row, mapping["avatar"])),
            signature=str(_value(row, mapping["signature"])),
            raw_payload=dict(row),
        )
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
                    str(content_row["source_keyword"]) if content_row else "",
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
    # The actual network call stays explicit in the AI service; this only creates queue rows.
    auto_competitor = database.get_setting(conn, "auto_analyze_competitors", "false") == "true"
    auto_lead = database.get_setting(conn, "auto_analyze_leads", "false") == "true"
    if auto_competitor and task["mode"] == "competitor_discovery":
        rows = conn.execute("SELECT id FROM user_accounts WHERE account_role = 'competitor_candidate' AND competitor_status = '未分析'").fetchall()
        for row in rows:
            _insert_analysis_job(conn, "competitor", int(row["id"]))
    if auto_lead and task["mode"] in ("competitor_crawl", "own_account", "demand_content"):
        rows = conn.execute("SELECT id FROM lead_user_accounts WHERE follow_status = '待筛选'").fetchall()
        for row in rows:
            _insert_analysis_job(conn, "lead", int(row["id"]))


def _insert_analysis_job(conn: sqlite3.Connection, target_type: str, target_id: int) -> None:
    job_id = f"{target_type}-{target_id}-{int(__import__('time').time() * 1000)}"
    conn.execute(
        "INSERT OR IGNORE INTO analysis_jobs(id, target_type, target_id) VALUES(?, ?, ?)",
        (job_id, target_type, target_id),
    )
