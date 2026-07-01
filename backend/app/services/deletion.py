from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import HTTPException

from app import database
from app.services import tombstones


ENTITY_BY_LIBRARY = {
    "contents": ("content", "contents"),
    "comments": ("comment", "comments"),
    "competitor_candidates": ("account", "user_accounts"),
    "competitors": ("account", "user_accounts"),
    "lead_customers": ("lead", "lead_user_accounts"),
    "target_customers": ("lead", "lead_user_accounts"),
}

def delete_library_row(library: str, row_id: int, hard: bool | None = None) -> dict[str, Any]:
    if library not in ENTITY_BY_LIBRARY:
        raise HTTPException(status_code=404, detail="未知数据表")
    entity_type, table = ENTITY_BY_LIBRARY[library]
    if library == "target_customers" and hard is not True:
        return soft_hide_target(row_id)
    if entity_type == "lead":
        return delete_lead_customer(row_id, source="table_delete")
    return hard_delete(entity_type, table, row_id)


def delete_overview_platform(platform: str) -> dict[str, Any]:
    return _delete_overview_scope(platform=platform, keyword=None)


def delete_overview_keyword(platform: str, keyword: str) -> dict[str, Any]:
    return _delete_overview_scope(platform=platform, keyword=keyword or "未标记关键词")


def delete_overview_account(account_id: int) -> dict[str, Any]:
    with database.connect() as conn:
        account = conn.execute("SELECT * FROM user_accounts WHERE id = ?", (account_id,)).fetchone()
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        scope = _build_account_scope(conn, account_id)
        refs = _collect_scope_raw_refs(conn, scope)
        tombstone_counts = tombstones.record_scope(conn, scope, "overview_account_delete")
        _delete_project_scope(conn, scope, refs)
        conn.execute(
            "INSERT INTO deletion_audit(entity_type, entity_id, hard_delete, detail) VALUES('overview_account', ?, 1, ?)",
            (
                account_id,
                json.dumps(
                    {
                        "account_id": account_id,
                        "counts": _scope_counts(scope),
                        "raw_refs": len(refs),
                        "tombstones": tombstone_counts,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
    return {
        "ok": True,
        "mode": "overview_account_delete",
        "account_id": account_id,
        "counts": _scope_counts(scope),
        "raw_refs": len(refs),
        "tombstones": tombstone_counts,
    }


def delete_lead_customer(lead_id: int, source_account_id: int | None = None, source: str = "lead_customer_delete") -> dict[str, Any]:
    with database.connect() as conn:
        lead = conn.execute(
            """
            SELECT lua.*, ua.platform, ua.platform_user_id, ua.sec_uid, ua.user_unique_id,
                   ua.nickname, ua.profile_url
            FROM lead_user_accounts lua
            JOIN user_accounts ua ON ua.id = lua.account_id
            WHERE lua.id = ?
            """,
            (lead_id,),
        ).fetchone()
        if not lead:
            raise HTTPException(status_code=404, detail="线索客户不存在")

        account_id = int(lead["account_id"])
        lead_source_ids = _lead_source_ids_for_delete(conn, lead_id, source_account_id)
        comment_ids = set(_select_related_ids(conn, "lead_sources", "comment_id", lead_source_ids))
        comment_ids_to_delete = _filter_deletable_comments(conn, comment_ids, lead_source_ids)
        remaining_sources = _count_remaining(conn, "lead_sources", "lead_account_id", lead_id, lead_source_ids, "active = 1")
        lead_ids_to_delete = {lead_id} if remaining_sources == 0 else set()
        account_ids_to_delete = _filter_deletable_accounts(
            conn,
            {account_id},
            set(),
            comment_ids_to_delete,
            set(),
            lead_source_ids,
            lead_ids_to_delete,
        )

        tombstone_counts = {"accounts": 0, "contents": 0, "comments": 0}
        for comment in _rows_by_ids(conn, "comments", comment_ids_to_delete):
            tombstone_counts["comments"] += tombstones.record_comment_row(conn, comment, source)

        refs = _refs_for_entities(conn, "comment", comment_ids_to_delete)
        refs.extend(_refs_for_entities(conn, "account", account_ids_to_delete))
        _delete_analysis_jobs(conn, "lead", lead_ids_to_delete)
        _delete_by_ids(conn, "lead_sources", lead_source_ids)
        _delete_by_ids(conn, "comments", comment_ids_to_delete)
        _delete_by_ids(conn, "lead_user_accounts", lead_ids_to_delete)
        _delete_by_ids(conn, "user_accounts", account_ids_to_delete)
        _delete_by_ids(conn, "raw_source_refs", {int(ref["id"]) for ref in refs})
        conn.execute(
            "INSERT INTO deletion_audit(entity_type, entity_id, hard_delete, detail) VALUES('lead_customer', ?, 1, ?)",
            (
                lead_id,
                json.dumps(
                    {
                        "lead_id": lead_id,
                        "account_id": account_id,
                        "source_account_id": source_account_id,
                        "counts": {
                            "lead_sources": len(lead_source_ids),
                            "comments": len(comment_ids_to_delete),
                            "leads": len(lead_ids_to_delete),
                            "accounts": len(account_ids_to_delete),
                        },
                        "raw_refs": len(refs),
                        "tombstones": tombstone_counts,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
    return {
        "ok": True,
        "mode": "lead_customer_delete",
        "lead_id": lead_id,
        "account_id": account_id,
        "source_account_id": source_account_id,
        "counts": {
            "lead_sources": len(lead_source_ids),
            "comments": len(comment_ids_to_delete),
            "leads": len(lead_ids_to_delete),
            "accounts": len(account_ids_to_delete),
        },
        "raw_refs": len(refs),
        "tombstones": tombstone_counts,
    }


def soft_hide_target(lead_id: int) -> dict[str, Any]:
    with database.connect() as conn:
        current = conn.execute("SELECT follow_status FROM lead_user_accounts WHERE id = ?", (lead_id,)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="目标客户不存在")
        conn.execute(
            "UPDATE lead_user_accounts SET hidden = 1, follow_status = '已移出', updated_at = datetime('now', 'localtime') WHERE id = ?",
            (lead_id,),
        )
        conn.execute(
            "INSERT INTO lead_status_events(lead_account_id, from_status, to_status, note) VALUES(?, ?, '已移出', '普通删除隐藏目标客户')",
            (lead_id, str(current["follow_status"])),
        )
        conn.execute(
            "INSERT INTO deletion_audit(entity_type, entity_id, hard_delete, detail) VALUES('lead', ?, 0, ?)",
            (lead_id, json.dumps({"action": "soft_hide"}, ensure_ascii=False)),
        )
    return {"ok": True, "mode": "soft_hide"}


def hard_delete(entity_type: str, table: str, entity_id: int) -> dict[str, Any]:
    if entity_type == "lead":
        return delete_lead_customer(entity_id, source="table_delete")
    with database.connect() as conn:
        tombstone_counts = _record_entity_tombstones(conn, entity_type, table, entity_id, "table_delete")
        refs = conn.execute(
            "SELECT * FROM raw_source_refs WHERE entity_type = ? AND entity_id = ?",
            (entity_type if entity_type != "lead" else "account", _lead_account_id(conn, entity_id) if entity_type == "lead" else entity_id),
        ).fetchall()
        if entity_type == "content":
            comment_ids = _select_ids(conn, "SELECT id FROM comments WHERE content_id = ?", (entity_id,))
            if comment_ids:
                placeholders = ",".join(["?"] * len(comment_ids))
                refs = [
                    *refs,
                    *conn.execute(
                        f"SELECT * FROM raw_source_refs WHERE entity_type = 'comment' AND entity_id IN ({placeholders})",
                        comment_ids,
                    ).fetchall(),
                ]
        _delete_project_entity(conn, entity_type, table, entity_id)
        _delete_by_ids(conn, "raw_source_refs", {int(ref["id"]) for ref in refs})
        conn.execute(
            "INSERT INTO deletion_audit(entity_type, entity_id, hard_delete, detail) VALUES(?, ?, 1, ?)",
            (
                entity_type,
                entity_id,
                json.dumps({"raw_refs": len(refs), "tombstones": tombstone_counts}, ensure_ascii=False),
            ),
        )
    return {"ok": True, "mode": "hard_delete", "raw_refs": len(refs), "tombstones": tombstone_counts}


def _delete_overview_scope(platform: str, keyword: str | None) -> dict[str, Any]:
    keyword_value = keyword or ""
    with database.connect() as conn:
        scope = _build_overview_scope(conn, platform, keyword)
        if not any(scope[key] for key in ("content_ids", "comment_ids", "account_ids", "lead_ids")):
            return _empty_scope_result(platform, keyword_value)
        refs = _collect_scope_raw_refs(conn, scope)
        tombstone_counts = tombstones.record_scope(
            conn,
            scope,
            "overview_keyword_delete" if keyword is not None else "overview_platform_delete",
        )
        _delete_project_scope(conn, scope, refs)
        conn.execute(
            "INSERT INTO deletion_audit(entity_type, entity_id, hard_delete, detail) VALUES(?, 0, 1, ?)",
            (
                "overview_keyword" if keyword is not None else "overview_platform",
                json.dumps(
                    {
                        "platform": platform,
                        "keyword": keyword_value,
                        "counts": _scope_counts(scope),
                        "raw_refs": len(refs),
                        "tombstones": tombstone_counts,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
    return {
        "ok": True,
        "mode": "overview_keyword_delete" if keyword is not None else "overview_platform_delete",
        "platform": platform,
        "keyword": keyword_value,
        "counts": _scope_counts(scope),
        "raw_refs": len(refs),
        "tombstones": tombstone_counts,
    }


def _empty_scope_result(platform: str, keyword: str) -> dict[str, Any]:
    return {
        "ok": True,
        "mode": "overview_keyword_delete" if keyword else "overview_platform_delete",
        "platform": platform,
        "keyword": keyword,
        "counts": {"contents": 0, "comments": 0, "accounts": 0, "leads": 0},
        "raw_refs": 0,
        "tombstones": {"accounts": 0, "contents": 0, "comments": 0},
    }


def _build_overview_scope(conn: sqlite3.Connection, platform: str, keyword: str | None) -> dict[str, Any]:
    keyword_value = keyword or "未标记关键词"
    if keyword is None:
        content_ids = _select_ids(conn, "SELECT id FROM contents WHERE platform = ?", (platform,))
        comment_ids = _select_ids(conn, "SELECT id FROM comments WHERE platform = ?", (platform,))
        account_source_ids = _select_ids(
            conn,
            """
            SELECT account_sources.id
            FROM account_sources
            JOIN user_accounts ua ON ua.id = account_sources.account_id
            WHERE ua.platform = ?
            """,
            (platform,),
        )
        lead_source_ids = _select_ids(
            conn,
            """
            SELECT DISTINCT ls.id
            FROM lead_sources ls
            LEFT JOIN contents c ON c.id = ls.content_id
            LEFT JOIN comments cm ON cm.id = ls.comment_id
            LEFT JOIN user_accounts source_ua ON source_ua.id = ls.source_account_id
            LEFT JOIN lead_user_accounts lua ON lua.id = ls.lead_account_id
            LEFT JOIN user_accounts lead_ua ON lead_ua.id = lua.account_id
            WHERE c.platform = ? OR cm.platform = ? OR source_ua.platform = ? OR lead_ua.platform = ?
            """,
            (platform, platform, platform, platform),
        )
        candidate_account_ids = set(_select_ids(conn, "SELECT id FROM user_accounts WHERE platform = ?", (platform,)))
    else:
        content_ids = _select_ids(
            conn,
            """
            SELECT id
            FROM contents
            WHERE platform = ? AND COALESCE(NULLIF(source_keyword, ''), '未标记关键词') = ?
            """,
            (platform, keyword_value),
        )
        comment_ids = _select_ids_where_column_in(conn, "comments", "content_id", content_ids)
        content_clause, content_params = _in_clause("account_sources.content_id", content_ids)
        account_source_ids = _select_ids(
            conn,
            f"""
            SELECT DISTINCT account_sources.id
            FROM account_sources
            JOIN user_accounts ua ON ua.id = account_sources.account_id
            WHERE ({content_clause})
               OR (ua.platform = ? AND COALESCE(NULLIF(account_sources.keyword, ''), '未标记关键词') = ?)
            """,
            (*content_params, platform, keyword_value),
        )
        content_clause, content_params = _in_clause("ls.content_id", content_ids)
        comment_clause, comment_params = _in_clause("ls.comment_id", comment_ids)
        lead_source_ids = _select_ids(
            conn,
            f"""
            SELECT DISTINCT ls.id
            FROM lead_sources ls
            LEFT JOIN lead_user_accounts lua ON lua.id = ls.lead_account_id
            LEFT JOIN user_accounts lead_ua ON lead_ua.id = lua.account_id
            WHERE ({content_clause})
               OR ({comment_clause})
               OR (lead_ua.platform = ? AND COALESCE(NULLIF(ls.keyword, ''), '未标记关键词') = ?)
            """,
            (*content_params, *comment_params, platform, keyword_value),
        )
        candidate_account_ids = set()

    candidate_account_ids.update(_select_related_ids(conn, "contents", "author_account_id", content_ids))
    candidate_account_ids.update(_select_related_ids(conn, "comments", "author_account_id", comment_ids))
    candidate_account_ids.update(_select_related_ids(conn, "account_sources", "account_id", account_source_ids))
    candidate_account_ids.update(_select_related_ids(conn, "lead_sources", "source_account_id", lead_source_ids))

    lead_ids = set(_select_related_ids(conn, "lead_sources", "lead_account_id", lead_source_ids))
    if keyword is None:
        lead_ids.update(
            _select_ids(
                conn,
                """
                SELECT lua.id
                FROM lead_user_accounts lua
                JOIN user_accounts ua ON ua.id = lua.account_id
                WHERE ua.platform = ?
                """,
                (platform,),
            )
        )
    lead_ids_to_delete = _filter_deletable_leads(conn, lead_ids, set(lead_source_ids))
    candidate_account_ids.update(_select_related_ids(conn, "lead_user_accounts", "account_id", list(lead_ids_to_delete)))
    account_ids_to_delete = _filter_deletable_accounts(
        conn,
        candidate_account_ids,
        set(content_ids),
        set(comment_ids),
        set(account_source_ids),
        set(lead_source_ids),
        lead_ids_to_delete,
    )

    return {
        "platform": platform,
        "keyword": keyword,
        "content_ids": set(content_ids),
        "comment_ids": set(comment_ids),
        "account_source_ids": set(account_source_ids),
        "lead_source_ids": set(lead_source_ids),
        "lead_ids": lead_ids_to_delete,
        "account_ids": account_ids_to_delete,
    }


def _build_account_scope(conn: sqlite3.Connection, account_id: int) -> dict[str, Any]:
    content_ids = set(_select_ids(conn, "SELECT id FROM contents WHERE author_account_id = ?", (account_id,)))
    comment_ids = set(_select_ids(conn, "SELECT id FROM comments WHERE author_account_id = ?", (account_id,)))
    comment_ids.update(_select_ids_where_column_in(conn, "comments", "content_id", content_ids))

    account_source_ids = set(_select_ids(conn, "SELECT id FROM account_sources WHERE account_id = ?", (account_id,)))
    account_source_ids.update(_select_ids_where_column_in(conn, "account_sources", "content_id", content_ids))

    direct_lead_ids = set(_select_ids(conn, "SELECT id FROM lead_user_accounts WHERE account_id = ?", (account_id,)))
    lead_source_ids = set(_select_ids(conn, "SELECT id FROM lead_sources WHERE source_account_id = ?", (account_id,)))
    lead_source_ids.update(_select_ids_where_column_in(conn, "lead_sources", "content_id", content_ids))
    lead_source_ids.update(_select_ids_where_column_in(conn, "lead_sources", "comment_id", comment_ids))
    lead_source_ids.update(_select_ids_where_column_in(conn, "lead_sources", "lead_account_id", direct_lead_ids))

    lead_ids = set(_select_related_ids(conn, "lead_sources", "lead_account_id", lead_source_ids))
    lead_ids.update(direct_lead_ids)
    lead_ids_to_delete = _filter_deletable_leads(conn, lead_ids, lead_source_ids)

    candidate_account_ids = {account_id}
    candidate_account_ids.update(_select_related_ids(conn, "contents", "author_account_id", content_ids))
    candidate_account_ids.update(_select_related_ids(conn, "comments", "author_account_id", comment_ids))
    candidate_account_ids.update(_select_related_ids(conn, "account_sources", "account_id", account_source_ids))
    # 账号级删除不能反向删除线索来源账号；删除客户账号时，来源通常就是竞品账号。
    candidate_account_ids.update(_select_related_ids(conn, "lead_user_accounts", "account_id", lead_ids_to_delete))
    account_ids_to_delete = _filter_deletable_accounts(
        conn,
        candidate_account_ids,
        content_ids,
        comment_ids,
        account_source_ids,
        lead_source_ids,
        lead_ids_to_delete,
    )
    account_ids_to_delete.add(account_id)

    return {
        "platform": "",
        "keyword": None,
        "content_ids": content_ids,
        "comment_ids": comment_ids,
        "account_source_ids": account_source_ids,
        "lead_source_ids": lead_source_ids,
        "lead_ids": lead_ids_to_delete,
        "account_ids": account_ids_to_delete,
    }


def _filter_deletable_leads(conn: sqlite3.Connection, lead_ids: set[int], scoped_source_ids: set[int]) -> set[int]:
    result: set[int] = set()
    for lead_id in lead_ids:
        remaining = _count_remaining(conn, "lead_sources", "lead_account_id", lead_id, scoped_source_ids, "active = 1")
        if remaining == 0:
            result.add(lead_id)
    return result


def _filter_deletable_comments(conn: sqlite3.Connection, comment_ids: set[int], scoped_source_ids: set[int]) -> set[int]:
    result: set[int] = set()
    for comment_id in comment_ids:
        remaining = _count_remaining(conn, "lead_sources", "comment_id", comment_id, scoped_source_ids, "active = 1")
        if remaining == 0:
            result.add(comment_id)
    return result


def _filter_deletable_accounts(
    conn: sqlite3.Connection,
    account_ids: set[int],
    scoped_content_ids: set[int],
    scoped_comment_ids: set[int],
    scoped_account_source_ids: set[int],
    scoped_lead_source_ids: set[int],
    scoped_lead_ids: set[int],
) -> set[int]:
    result: set[int] = set()
    for account_id in account_ids:
        remaining = 0
        remaining += _count_remaining(conn, "contents", "author_account_id", account_id, scoped_content_ids)
        remaining += _count_remaining(conn, "comments", "author_account_id", account_id, scoped_comment_ids)
        remaining += _count_remaining(conn, "account_sources", "account_id", account_id, scoped_account_source_ids, "active = 1")
        remaining += _count_remaining(conn, "lead_sources", "source_account_id", account_id, scoped_lead_source_ids, "active = 1")
        remaining += _count_remaining(conn, "lead_user_accounts", "account_id", account_id, scoped_lead_ids)
        if remaining == 0:
            result.add(account_id)
    return result


def _count_remaining(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    value: int,
    scoped_ids: set[int],
    extra_clause: str = "",
) -> int:
    sql = f"SELECT COUNT(*) AS c FROM {table} WHERE {column} = ?"
    params: list[Any] = [value]
    if scoped_ids:
        placeholders = ",".join(["?"] * len(scoped_ids))
        sql += f" AND id NOT IN ({placeholders})"
        params.extend(sorted(scoped_ids))
    if extra_clause:
        sql += f" AND {extra_clause}"
    return int(conn.execute(sql, params).fetchone()["c"])


def _lead_source_ids_for_delete(conn: sqlite3.Connection, lead_id: int, source_account_id: int | None) -> set[int]:
    if source_account_id is None:
        return set(
            _select_ids(
                conn,
                "SELECT id FROM lead_sources WHERE lead_account_id = ? AND active = 1",
                (lead_id,),
            )
        )
    return set(
        _select_ids(
            conn,
            """
            SELECT DISTINCT ls.id
            FROM lead_sources ls
            LEFT JOIN contents c ON c.id = ls.content_id
            WHERE ls.lead_account_id = ?
              AND ls.active = 1
              AND (ls.source_account_id = ? OR c.author_account_id = ?)
            """,
            (lead_id, source_account_id, source_account_id),
        )
    )


def _collect_scope_raw_refs(conn: sqlite3.Connection, scope: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    refs.extend(_refs_for_entities(conn, "content", scope["content_ids"]))
    refs.extend(_refs_for_entities(conn, "comment", scope["comment_ids"]))
    refs.extend(_refs_for_entities(conn, "account", scope["account_ids"]))
    return refs


def _refs_for_entities(conn: sqlite3.Connection, entity_type: str, entity_ids: set[int]) -> list[dict[str, Any]]:
    if not entity_ids:
        return []
    placeholders = ",".join(["?"] * len(entity_ids))
    refs = conn.execute(
        f"""
        SELECT *
        FROM raw_source_refs
        WHERE entity_type = ? AND entity_id IN ({placeholders})
        ORDER BY entity_id
        """,
        (entity_type, *sorted(entity_ids)),
    ).fetchall()
    return [database.row_to_dict(ref) for ref in refs]


def _delete_project_scope(conn: sqlite3.Connection, scope: dict[str, Any], refs: list[dict[str, Any]]) -> None:
    _delete_analysis_jobs(conn, "content", scope["content_ids"])
    _delete_analysis_jobs(conn, "lead", scope["lead_ids"])
    _delete_analysis_jobs(conn, "competitor", scope["account_ids"])
    _delete_by_ids(conn, "lead_sources", scope["lead_source_ids"])
    _delete_by_ids(conn, "account_sources", scope["account_source_ids"])
    _delete_by_ids(conn, "comments", scope["comment_ids"])
    _delete_by_ids(conn, "contents", scope["content_ids"])
    _delete_by_ids(conn, "lead_user_accounts", scope["lead_ids"])
    _delete_by_ids(conn, "user_accounts", scope["account_ids"])
    _delete_by_ids(conn, "raw_source_refs", {int(ref["id"]) for ref in refs})


def _delete_analysis_jobs(conn: sqlite3.Connection, target_type: str, target_ids: set[int]) -> None:
    if not target_ids:
        return
    placeholders = ",".join(["?"] * len(target_ids))
    conn.execute(
        f"DELETE FROM analysis_jobs WHERE target_type = ? AND target_id IN ({placeholders})",
        (target_type, *sorted(target_ids)),
    )


def _delete_by_ids(conn: sqlite3.Connection, table: str, ids: set[int]) -> None:
    if not ids:
        return
    placeholders = ",".join(["?"] * len(ids))
    conn.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", sorted(ids))


def _scope_counts(scope: dict[str, Any]) -> dict[str, int]:
    return {
        "contents": len(scope["content_ids"]),
        "comments": len(scope["comment_ids"]),
        "accounts": len(scope["account_ids"]),
        "leads": len(scope["lead_ids"]),
    }


def _select_ids(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[int]:
    return [int(row["id"]) for row in conn.execute(sql, params).fetchall() if row["id"] is not None]


def _select_related_ids(conn: sqlite3.Connection, table: str, column: str, ids: list[int] | set[int]) -> list[int]:
    if not ids:
        return []
    placeholders = ",".join(["?"] * len(ids))
    rows = conn.execute(
        f"SELECT DISTINCT {column} AS id FROM {table} WHERE id IN ({placeholders}) AND {column} IS NOT NULL",
        sorted(ids),
    ).fetchall()
    return [int(row["id"]) for row in rows if row["id"] is not None]


def _rows_by_ids(conn: sqlite3.Connection, table: str, ids: set[int]) -> list[sqlite3.Row]:
    if not ids:
        return []
    placeholders = ",".join(["?"] * len(ids))
    return conn.execute(f"SELECT * FROM {table} WHERE id IN ({placeholders})", sorted(ids)).fetchall()


def _select_ids_where_column_in(conn: sqlite3.Connection, table: str, column: str, values: list[int] | set[int]) -> list[int]:
    if not values:
        return []
    placeholders = ",".join(["?"] * len(values))
    rows = conn.execute(
        f"SELECT DISTINCT id FROM {table} WHERE {column} IN ({placeholders})",
        sorted(values),
    ).fetchall()
    return [int(row["id"]) for row in rows if row["id"] is not None]


def _in_clause(column: str, ids: list[int] | set[int]) -> tuple[str, list[int]]:
    if not ids:
        return "1 = 0", []
    placeholders = ",".join(["?"] * len(ids))
    return f"{column} IN ({placeholders})", sorted(ids)


def _lead_account_id(conn, lead_id: int) -> int:
    row = conn.execute("SELECT account_id FROM lead_user_accounts WHERE id = ?", (lead_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="客户不存在")
    return int(row["account_id"])


def _record_entity_tombstones(
    conn: sqlite3.Connection,
    entity_type: str,
    table: str,
    entity_id: int,
    source: str,
) -> dict[str, int]:
    counts = {"accounts": 0, "contents": 0, "comments": 0}
    if entity_type == "account":
        row = conn.execute("SELECT * FROM user_accounts WHERE id = ?", (entity_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="账号不存在")
        counts["accounts"] += tombstones.record_account_row(conn, row, source)
    elif entity_type == "content":
        row = conn.execute("SELECT * FROM contents WHERE id = ?", (entity_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="内容不存在")
        counts["contents"] += tombstones.record_content_row(conn, row, source)
        for comment in conn.execute("SELECT * FROM comments WHERE content_id = ?", (entity_id,)).fetchall():
            counts["comments"] += tombstones.record_comment_row(conn, comment, source)
    elif entity_type == "comment":
        row = conn.execute("SELECT * FROM comments WHERE id = ?", (entity_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="评论不存在")
        counts["comments"] += tombstones.record_comment_row(conn, row, source)
    elif entity_type == "lead":
        _lead_account_id(conn, entity_id)
    else:
        row = conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (entity_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="数据不存在")
    return counts


def _delete_project_entity(conn, entity_type: str, table: str, entity_id: int) -> None:
    if entity_type == "lead":
        active_sources = conn.execute(
            "SELECT COUNT(*) AS c FROM lead_sources WHERE lead_account_id = ? AND active = 1",
            (entity_id,),
        ).fetchone()["c"]
        if int(active_sources) > 1:
            conn.execute("UPDATE lead_sources SET active = 0 WHERE lead_account_id = ? LIMIT 1", (entity_id,))
            conn.execute(
                "UPDATE lead_user_accounts SET follow_status = '已移出', updated_at = datetime('now', 'localtime') WHERE id = ?",
                (entity_id,),
            )
            return
        account_id = _lead_account_id(conn, entity_id)
        conn.execute("DELETE FROM lead_user_accounts WHERE id = ?", (entity_id,))
        conn.execute("DELETE FROM user_accounts WHERE id = ?", (account_id,))
        return
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (entity_id,))


def delete_task(task_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        task = conn.execute("SELECT * FROM crawl_jobs WHERE id = ?", (task_id,)).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        if str(task["status"]) == "running":
            raise HTTPException(status_code=409, detail="运行中任务请先取消，再删除任务记录")
        refs = conn.execute("SELECT * FROM raw_source_refs WHERE task_id = ?", (task_id,)).fetchall()
        project_counts = _task_project_counts(conn, task_id)
        if not refs:
            if sum(project_counts.values()) == 0:
                cleanup = _cleanup_deleted_account_analysis_queue(conn, task)
                return _delete_empty_task(conn, task_id, str(task["status"]), project_counts, cleanup)
        tombstone_counts = tombstones.record_task_content_and_comments(conn, task_id, "task_delete")
        cleanup = _cleanup_deleted_account_analysis_queue(conn, task)
        conn.execute("DELETE FROM comments WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM contents WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM account_sources WHERE task_id = ?", (task_id,))
        conn.execute("UPDATE lead_sources SET active = 0 WHERE task_id = ?", (task_id,))
        _delete_by_ids(conn, "raw_source_refs", {int(ref["id"]) for ref in refs})
        conn.execute("DELETE FROM crawl_jobs WHERE id = ?", (task_id,))
        conn.execute(
            "INSERT INTO deletion_audit(entity_type, entity_id, hard_delete, detail) VALUES('task', 0, 1, ?)",
            (
                json.dumps(
                    {
                        "task_id": task_id,
                        "raw_refs": len(refs),
                        "analysis_cleanup": cleanup,
                        "tombstones": tombstone_counts,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
    return {
        "ok": True,
        "mode": "task_hard_delete",
        "raw_refs": len(refs),
        "analysis_cleanup": cleanup,
        "tombstones": tombstone_counts,
    }


def _task_project_counts(conn: sqlite3.Connection, task_id: str) -> dict[str, int]:
    # 没有任何业务数据的任务只删除任务记录和日志，不触碰 MediaCrawler 底层库。
    return {
        "contents": int(conn.execute("SELECT COUNT(*) AS c FROM contents WHERE task_id = ?", (task_id,)).fetchone()["c"]),
        "comments": int(conn.execute("SELECT COUNT(*) AS c FROM comments WHERE task_id = ?", (task_id,)).fetchone()["c"]),
        "account_sources": int(conn.execute("SELECT COUNT(*) AS c FROM account_sources WHERE task_id = ?", (task_id,)).fetchone()["c"]),
        "lead_sources": int(conn.execute("SELECT COUNT(*) AS c FROM lead_sources WHERE task_id = ? AND active = 1", (task_id,)).fetchone()["c"]),
    }


def _delete_empty_task(
    conn: sqlite3.Connection,
    task_id: str,
    status: str,
    counts: dict[str, int],
    analysis_cleanup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    analysis_cleanup = analysis_cleanup or {"account_ids": [], "deleted_jobs": 0}
    conn.execute("DELETE FROM crawl_jobs WHERE id = ?", (task_id,))
    conn.execute(
        "INSERT INTO deletion_audit(entity_type, entity_id, hard_delete, detail) VALUES('task', 0, 0, ?)",
        (
            json.dumps(
                {
                    "task_id": task_id,
                    "status": status,
                    "raw_refs": 0,
                    "project_counts": counts,
                    "analysis_cleanup": analysis_cleanup,
                },
                ensure_ascii=False,
            ),
        ),
    )
    return {
        "ok": True,
        "mode": "task_record_delete",
        "raw_refs": 0,
        "project_counts": counts,
        "analysis_cleanup": analysis_cleanup,
    }


def _cleanup_deleted_account_analysis_queue(conn: sqlite3.Connection, task: sqlite3.Row) -> dict[str, Any]:
    # 失败/取消/待执行的账号分析任务删除后，需要释放这些账号的临时队列态。
    if str(task["mode"]) != "account_analysis" or str(task["status"]) == "running":
        return {"account_ids": [], "deleted_jobs": 0}
    creator_items = [item.strip() for item in str(task["creator_id"] or "").split(",") if item.strip()]
    if not creator_items:
        return {"account_ids": [], "deleted_jobs": 0}

    account_ids: list[int] = []
    seen: set[int] = set()
    for creator_id in creator_items:
        row = conn.execute(
            """
            SELECT id
            FROM user_accounts
            WHERE platform = ?
              AND (
                profile_url = ?
                OR platform_user_id = ?
                OR sec_uid = ?
              )
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (task["platform"], creator_id, creator_id, creator_id),
        ).fetchone()
        if row and int(row["id"]) not in seen:
            account_ids.append(int(row["id"]))
            seen.add(int(row["id"]))

    deleted_jobs = 0
    if account_ids:
        placeholders = ",".join(["?"] * len(account_ids))
        cursor = conn.execute(
            f"""
            DELETE FROM analysis_jobs
            WHERE target_type = 'competitor'
              AND status IN ('pending', 'running')
              AND target_id IN ({placeholders})
            """,
            account_ids,
        )
        deleted_jobs = int(cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else 0)
    return {"account_ids": account_ids, "deleted_jobs": deleted_jobs}
