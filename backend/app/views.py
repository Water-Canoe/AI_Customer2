from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import database


LIBRARY_LABELS = {
    "contents": "内容库",
    "comments": "评论库",
    "competitor_candidates": "竞品账号候选库",
    "competitors": "竞品账号库",
    "lead_customers": "线索客户库",
    "target_customers": "目标客户库",
}


def get_settings() -> dict[str, Any]:
    with database.connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
    result = {row["key"]: row["value"] for row in rows}
    for key in ("auto_analyze_competitors", "auto_analyze_leads", "headless"):
        result[key] = result.get(key, "false") == "true"
    try:
        result["icp_profile"] = json.loads(result.get("icp_profile", "{}"))
    except json.JSONDecodeError:
        result["icp_profile"] = {}
    return result


def update_settings(values: dict[str, Any]) -> dict[str, Any]:
    with database.connect() as conn:
        for key, value in values.items():
            if key == "icp_profile":
                value = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, bool):
                value = "true" if value else "false"
            database.set_setting(conn, key, value)
    return get_settings()


def environment_check() -> dict[str, Any]:
    settings = get_settings()
    media_path = Path(str(settings.get("media_crawler_path", "")).strip().strip('"').strip("'"))
    raw_db = Path(str(settings.get("media_crawler_db_path", "")).strip().strip('"').strip("'"))
    return {
        "project_db": {"path": str(database.get_db_path()), "ok": database.get_db_path().exists()},
        "media_crawler_path": {"path": str(media_path), "ok": media_path.exists()},
        "media_crawler_db": {"path": str(raw_db), "ok": raw_db.exists()},
        "ai_config": {
            "ok": bool(settings.get("ai_base_url") and settings.get("ai_api_key") and settings.get("ai_model")),
            "base_url": settings.get("ai_base_url", ""),
            "model": settings.get("ai_model", ""),
        },
    }


def list_library(library: str, status: str = "", keyword: str = "") -> dict[str, Any]:
    if library == "contents":
        return _library_response(library, _list_contents(keyword))
    if library == "comments":
        return _library_response(library, _list_comments(keyword))
    if library == "competitor_candidates":
        return _library_response(library, _list_competitors(candidate=True, status=status, keyword=keyword))
    if library == "competitors":
        return _library_response(library, _list_competitors(candidate=False, status=status, keyword=keyword))
    if library == "lead_customers":
        return _library_response(library, _list_leads(target=False, status=status, keyword=keyword))
    if library == "target_customers":
        return _library_response(library, _list_leads(target=True, status=status, keyword=keyword))
    raise KeyError(library)


def _library_response(library: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"library": library, "label": LIBRARY_LABELS[library], "rows": rows}


def _keyword_clause(keyword: str, fields: list[str]) -> tuple[str, list[str]]:
    if not keyword:
        return "", []
    clause = " AND (" + " OR ".join([f"{field} LIKE ?" for field in fields]) + ")"
    params = [f"%{keyword}%" for _ in fields]
    return clause, params


def _list_contents(keyword: str) -> list[dict[str, Any]]:
    clause, params = _keyword_clause(keyword, ["c.title", "c.description", "ua.nickname", "c.source_keyword"])
    with database.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT c.*, ua.nickname AS author_nickname, ua.profile_url AS author_url, j.name AS task_name
            FROM contents c
            LEFT JOIN user_accounts ua ON ua.id = c.author_account_id
            LEFT JOIN crawl_jobs j ON j.id = c.task_id
            WHERE 1 = 1 {clause}
            ORDER BY c.updated_at DESC
            LIMIT 500
            """,
            params,
        ).fetchall()
    return database.rows_to_dicts(rows)


def _list_comments(keyword: str) -> list[dict[str, Any]]:
    clause, params = _keyword_clause(keyword, ["cm.body", "ua.nickname", "ct.title"])
    with database.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT cm.*, ua.nickname AS commenter_nickname, ua.profile_url AS commenter_url,
                   ct.title AS content_title, ct.content_url, j.name AS task_name
            FROM comments cm
            LEFT JOIN user_accounts ua ON ua.id = cm.author_account_id
            LEFT JOIN contents ct ON ct.id = cm.content_id
            LEFT JOIN crawl_jobs j ON j.id = cm.task_id
            WHERE 1 = 1 {clause}
            ORDER BY cm.updated_at DESC
            LIMIT 500
            """,
            params,
        ).fetchall()
    return database.rows_to_dicts(rows)


def _list_competitors(candidate: bool, status: str, keyword: str) -> list[dict[str, Any]]:
    base_status = "ua.competitor_status != '竞品'" if candidate else "ua.competitor_status = '竞品'"
    status_clause = " AND ua.competitor_status = ?" if status else ""
    kw_clause, params = _keyword_clause(keyword, ["ua.nickname", "ua.signature", "ua.platform_user_id"])
    all_params: list[Any] = ([status] if status else []) + params
    with database.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT ua.*, COUNT(DISTINCT ac.content_id) AS content_count,
                   MAX(j.name) AS task_name, MAX(j.id) AS task_id
            FROM user_accounts ua
            LEFT JOIN account_sources ac ON ac.account_id = ua.id AND ac.active = 1
            LEFT JOIN crawl_jobs j ON j.id = ac.task_id
            WHERE {base_status} AND (ua.account_role IN ('competitor_candidate', 'competitor') OR ac.id IS NOT NULL)
            {status_clause} {kw_clause}
            GROUP BY ua.id
            ORDER BY ua.updated_at DESC
            LIMIT 500
            """,
            all_params,
        ).fetchall()
    return database.rows_to_dicts(rows)


def _list_leads(target: bool, status: str, keyword: str) -> list[dict[str, Any]]:
    target_clause = "lua.follow_status NOT IN ('待筛选', '无需跟进') AND lua.hidden = 0" if target else "lua.follow_status IN ('待筛选', '无需跟进') AND lua.hidden = 0"
    status_clause = " AND lua.follow_status = ?" if status else ""
    kw_clause, params = _keyword_clause(keyword, ["ua.nickname", "ua.signature", "lua.reason", "lua.script"])
    all_params: list[Any] = ([status] if status else []) + params
    with database.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT lua.*, ua.platform, ua.nickname, ua.profile_url, ua.platform_user_id,
                   ua.signature, COUNT(DISTINCT ls.id) AS source_count,
                   GROUP_CONCAT(DISTINCT cm.body) AS comment_samples,
                   MAX(j.name) AS task_name, MAX(j.id) AS task_id
            FROM lead_user_accounts lua
            JOIN user_accounts ua ON ua.id = lua.account_id
            LEFT JOIN lead_sources ls ON ls.lead_account_id = lua.id AND ls.active = 1
            LEFT JOIN comments cm ON cm.id = ls.comment_id
            LEFT JOIN crawl_jobs j ON j.id = ls.task_id
            WHERE {target_clause} {status_clause} {kw_clause}
            GROUP BY lua.id
            ORDER BY lua.updated_at DESC
            LIMIT 500
            """,
            all_params,
        ).fetchall()
    return database.rows_to_dicts(rows)


def update_library_row(library: str, row_id: int, values: dict[str, Any]) -> dict[str, Any]:
    allowed = _allowed_update_fields(library)
    updates = {key: value for key, value in values.items() if key in allowed}
    if not updates:
        return {"ok": True, "updated": 0}
    table = _update_table(library)
    assignments = ", ".join([f"{key} = ?" for key in updates])
    params = list(updates.values()) + [row_id]
    with database.connect() as conn:
        conn.execute(
            f"UPDATE {table} SET {assignments}, updated_at = datetime('now', 'localtime') WHERE id = ?",
            params,
        )
    return {"ok": True, "updated": len(updates)}


def _allowed_update_fields(library: str) -> set[str]:
    if library in ("competitor_candidates", "competitors"):
        return {"nickname", "signature", "profile_url", "fans", "competitor_status", "account_role"}
    if library in ("lead_customers", "target_customers"):
        return {"screening_status", "follow_status", "intention", "reason", "pain_points", "suggested_action", "script", "hidden"}
    if library == "contents":
        return {"title", "description", "content_url", "like_count", "comment_count", "source_keyword"}
    if library == "comments":
        return {"body", "like_count", "parent_comment_id"}
    return set()


def _update_table(library: str) -> str:
    if library in ("competitor_candidates", "competitors"):
        return "user_accounts"
    if library in ("lead_customers", "target_customers"):
        return "lead_user_accounts"
    return library


def overview_tree() -> list[dict[str, Any]]:
    with database.connect() as conn:
        platforms = conn.execute(
            """
            SELECT c.platform AS platform,
                   COUNT(DISTINCT CASE WHEN ua.competitor_status = '竞品' THEN ua.id END) AS competitors,
                   COUNT(DISTINCT lua.id) AS customers,
                   COUNT(DISTINCT c.id) AS contents,
                   COUNT(DISTINCT cm.id) AS comments,
                   MAX(c.updated_at) AS latest
            FROM contents c
            LEFT JOIN user_accounts ua ON ua.id = c.author_account_id
            LEFT JOIN comments cm ON cm.content_id = c.id
            LEFT JOIN lead_sources ls ON ls.content_id = c.id
            LEFT JOIN lead_user_accounts lua ON lua.id = ls.lead_account_id
            GROUP BY c.platform
            """,
        ).fetchall()
        result: list[dict[str, Any]] = []
        for platform in platforms:
            platform_key = platform["platform"]
            keywords = conn.execute(
                """
                SELECT COALESCE(NULLIF(c.source_keyword, ''), '未标记关键词') AS keyword,
                       COUNT(DISTINCT CASE WHEN ua.competitor_status = '竞品' THEN ua.id END) AS competitors,
                       COUNT(DISTINCT c.id) AS contents,
                       COUNT(DISTINCT cm.id) AS comments,
                       COUNT(DISTINCT lua.id) AS customers,
                       MAX(c.updated_at) AS latest
                FROM contents c
                LEFT JOIN user_accounts ua ON ua.id = c.author_account_id
                LEFT JOIN comments cm ON cm.content_id = c.id
                LEFT JOIN lead_sources ls ON ls.content_id = c.id
                LEFT JOIN lead_user_accounts lua ON lua.id = ls.lead_account_id
                WHERE c.platform = ?
                GROUP BY COALESCE(NULLIF(c.source_keyword, ''), '未标记关键词')
                """,
                (platform_key,),
            ).fetchall()
            result.append(
                {
                    "id": f"platform:{platform_key}",
                    "label": platform_key,
                    "kind": "platform",
                    "metrics": database.row_to_dict(platform),
                    "children": [_keyword_node(conn, platform_key, row["keyword"], database.row_to_dict(row)) for row in keywords],
                }
            )
    return result


def _keyword_node(conn, platform: str, keyword: str, metrics: dict[str, Any]) -> dict[str, Any]:
    accounts = conn.execute(
        """
        SELECT ua.id, ua.nickname, ua.profile_url, ua.fans, ua.signature,
               ua.account_role, ua.competitor_status,
               COUNT(DISTINCT c.id) AS content_count,
               COUNT(DISTINCT cm.id) AS comment_count,
               COUNT(DISTINCT lua.id) AS customer_count,
               MAX(c.updated_at) AS latest
        FROM contents c
        JOIN user_accounts ua ON ua.id = c.author_account_id
        LEFT JOIN comments cm ON cm.content_id = c.id
        LEFT JOIN lead_sources ls ON ls.content_id = c.id
        LEFT JOIN lead_user_accounts lua ON lua.id = ls.lead_account_id
        WHERE c.platform = ? AND COALESCE(NULLIF(c.source_keyword, ''), '未标记关键词') = ?
        GROUP BY ua.id
        LIMIT 100
        """,
        (platform, keyword),
    ).fetchall()
    return {
        "id": f"keyword:{platform}:{keyword}",
        "label": keyword,
        "kind": "keyword",
        "metrics": metrics,
        "children": [
            {
                "id": f"account:{row['id']}",
                "label": row["nickname"] or f"账号 {row['id']}",
                "kind": "account",
                "metrics": database.row_to_dict(row),
                "children": [],
            }
            for row in accounts
        ],
    }


def overview_node(node_id: str) -> dict[str, Any]:
    kind, _, raw_id = node_id.partition(":")
    with database.connect() as conn:
        if kind == "account":
            row = conn.execute("SELECT * FROM user_accounts WHERE id = ?", (raw_id,)).fetchone()
            contents = conn.execute("SELECT * FROM contents WHERE author_account_id = ? LIMIT 20", (raw_id,)).fetchall()
            return {"node": database.row_to_dict(row), "contents": database.rows_to_dicts(contents)}
        if kind == "platform":
            return {"node": {"platform": raw_id}, "tables": list_library("contents", keyword="")}
    return {"node": {"id": node_id}}
