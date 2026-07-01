from __future__ import annotations

from typing import Any

from app import database


def preview_bulk_action(payload: Any) -> dict[str, Any]:
    data = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
    action = str(data.get("action") or "")
    target_type = str(data.get("target_type") or "")
    target_ids = list(data.get("target_ids") or [])
    filters = dict(data.get("filters") or {})
    if action == "delete_non_competitors":
        return _preview_delete_non_competitors(target_ids, filters)
    if action == "delete_non_customers":
        return _preview_delete_non_customers(target_ids, filters)
    if action == "ai_analyze":
        return _preview_ai_analyze(target_type, target_ids)
    if action == "retry_failed_ai":
        return _preview_retry_failed_ai(target_ids)
    if action == "keyword_analyze":
        return _preview_keyword_analyze(filters)
    if action == "keyword_find_customers":
        return _preview_keyword_find_customers(filters)
    return _empty_preview("未知批量动作", ["当前动作没有预览规则，后端不会执行任何预览副作用。"])


def _preview_delete_non_competitors(target_ids: list[Any], filters: dict[str, Any]) -> dict[str, Any]:
    rows = _competitor_rows(target_ids, filters)
    eligible = [row for row in rows if str(row.get("competitor_status") or "") == "非竞品"]
    skipped = [row for row in rows if str(row.get("competitor_status") or "") != "非竞品"]
    tombstones = sum(_account_tombstone_weight(row) for row in eligible)
    return {
        "action": "delete_non_competitors",
        "eligible_count": len(eligible),
        "skipped_count": len(skipped),
        "affected_counts": {"accounts": len(eligible), "contents": 0, "comments": 0, "leads": 0},
        "tombstone_counts": {"author_account": tombstones},
        "warnings": ["删除会写入账号墓碑，后续关键词采集会跳过这些非竞品作者。"] if eligible else ["没有符合“非竞品”的账号。"],
        "sample_rows": _sample_rows(eligible, "nickname", "competitor_status"),
        "confirm_text": f"预计删除 {len(eligible)} 个非竞品账号，跳过 {len(skipped)} 个不符合条件的账号。",
    }


def _preview_delete_non_customers(target_ids: list[Any], filters: dict[str, Any]) -> dict[str, Any]:
    rows = _lead_rows(target_ids, filters)
    eligible = [row for row in rows if _is_non_customer(row)]
    skipped = [row for row in rows if not _is_non_customer(row)]
    lead_ids = [int(row["id"]) for row in eligible]
    comment_count = _active_comment_count_for_leads(lead_ids)
    return {
        "action": "delete_non_customers",
        "eligible_count": len(eligible),
        "skipped_count": len(skipped),
        "affected_counts": {"accounts": 0, "contents": 0, "comments": comment_count, "leads": len(eligible)},
        "tombstone_counts": {"comment": comment_count},
        "warnings": ["删除非客户只应移除线索关系和评论证据，不应误删竞品账号。"] if eligible else ["没有符合“非客户”的线索。"],
        "sample_rows": _sample_rows(eligible, "nickname", "screening_status"),
        "confirm_text": f"预计删除 {len(eligible)} 个非客户线索，跳过 {len(skipped)} 个不符合条件的线索。",
    }


def _preview_ai_analyze(target_type: str, target_ids: list[Any]) -> dict[str, Any]:
    ids = _int_ids(target_ids)
    if target_type not in ("competitor", "lead", "content"):
        return _empty_preview("AI分析对象类型不支持", ["仅支持 competitor、lead、content。"])
    rows = _target_rows(target_type, ids)
    busy_ids = _busy_analysis_ids(target_type, ids)
    eligible = [row for row in rows if int(row["id"]) not in busy_ids]
    skipped = [row for row in rows if int(row["id"]) in busy_ids]
    missing_count = max(0, len(ids) - len(rows))
    if missing_count:
        skipped.extend({"id": 0, "name": "对象不存在", "status": "missing"} for _ in range(missing_count))
    return {
        "action": "ai_analyze",
        "eligible_count": len(eligible),
        "skipped_count": len(skipped),
        "affected_counts": {"analysis_jobs": len(eligible)},
        "tombstone_counts": {},
        "warnings": ["已有排队或运行中的对象会被跳过，避免重复消耗 AI。"] if skipped else [],
        "sample_rows": _sample_rows(eligible, "name", "status"),
        "confirm_text": f"预计创建并运行 {len(eligible)} 个 AI 分析任务，跳过 {len(skipped)} 个对象。",
    }


def _preview_retry_failed_ai(target_ids: list[Any]) -> dict[str, Any]:
    ids = [str(item) for item in target_ids if str(item).strip()]
    rows = _analysis_job_rows(ids)
    eligible = [row for row in rows if str(row.get("status") or "") == "failed"]
    skipped = [row for row in rows if str(row.get("status") or "") != "failed"]
    return {
        "action": "retry_failed_ai",
        "eligible_count": len(eligible),
        "skipped_count": len(skipped),
        "affected_counts": {"analysis_jobs": len(eligible)},
        "tombstone_counts": {},
        "warnings": ["只会重试 failed 状态的 AI 任务。"],
        "sample_rows": _sample_rows(eligible, "target_name", "error"),
        "confirm_text": f"预计重试 {len(eligible)} 个失败 AI 任务，跳过 {len(skipped)} 个非失败任务。",
    }


def _preview_keyword_analyze(filters: dict[str, Any]) -> dict[str, Any]:
    platform = str(filters.get("platform") or "")
    keyword = str(filters.get("keyword") or "")
    rows = _keyword_accounts(platform, keyword, only_competitors=False)
    eligible = [row for row in rows if str(row.get("competitor_status") or "") not in ("竞品", "非竞品")]
    skipped = [row for row in rows if row not in eligible]
    warnings = []
    if not platform or not keyword:
        warnings.append("缺少平台或关键词，预览结果可能为空。")
    return {
        "action": "keyword_analyze",
        "eligible_count": len(eligible),
        "skipped_count": len(skipped),
        "affected_counts": {"account_analysis_tasks": 1 if eligible else 0, "accounts": len(eligible)},
        "tombstone_counts": {},
        "warnings": warnings,
        "sample_rows": _sample_rows(eligible, "nickname", "competitor_status"),
        "confirm_text": f"预计对关键词下 {len(eligible)} 个未分析账号发起一键竞品分析。",
    }


def _preview_keyword_find_customers(filters: dict[str, Any]) -> dict[str, Any]:
    platform = str(filters.get("platform") or "")
    keyword = str(filters.get("keyword") or "")
    rows = _keyword_accounts(platform, keyword, only_competitors=True)
    warnings = []
    if not platform or not keyword:
        warnings.append("缺少平台或关键词，预览结果可能为空。")
    return {
        "action": "keyword_find_customers",
        "eligible_count": len(rows),
        "skipped_count": 0,
        "affected_counts": {"crawler_tasks": 1 if rows else 0, "competitor_accounts": len(rows)},
        "tombstone_counts": {},
        "warnings": warnings,
        "sample_rows": _sample_rows(rows, "nickname", "competitor_status"),
        "confirm_text": f"预计对关键词下 {len(rows)} 个竞品账号发起找客户任务。",
    }


def _competitor_rows(target_ids: list[Any], filters: dict[str, Any]) -> list[dict[str, Any]]:
    ids = _int_ids(target_ids)
    with database.connect() as conn:
        if ids:
            clause, params = _in_clause("ua.id", ids)
            rows = conn.execute(
                f"SELECT ua.* FROM user_accounts ua WHERE {clause} ORDER BY ua.id DESC",
                params,
            ).fetchall()
        else:
            platform = str(filters.get("platform") or "")
            keyword = str(filters.get("keyword") or "")
            rows = conn.execute(
                """
                SELECT DISTINCT ua.*
                FROM user_accounts ua
                JOIN account_sources src ON src.account_id = ua.id
                WHERE src.active = 1
                  AND (? = '' OR ua.platform = ?)
                  AND (? = '' OR COALESCE(NULLIF(src.keyword, ''), '未标记关键词') = ?)
                ORDER BY ua.updated_at DESC, ua.id DESC
                """,
                (platform, platform, keyword, keyword),
            ).fetchall()
    return database.rows_to_dicts(rows)


def _lead_rows(target_ids: list[Any], filters: dict[str, Any]) -> list[dict[str, Any]]:
    ids = _int_ids(target_ids)
    with database.connect() as conn:
        if ids:
            clause, params = _in_clause("lua.id", ids)
            rows = conn.execute(
                f"""
                SELECT lua.*, ua.nickname, ua.profile_url
                FROM lead_user_accounts lua
                JOIN user_accounts ua ON ua.id = lua.account_id
                WHERE {clause}
                ORDER BY lua.updated_at DESC, lua.id DESC
                """,
                params,
            ).fetchall()
        else:
            platform = str(filters.get("platform") or "")
            keyword = str(filters.get("keyword") or "")
            source_account_id = _positive_int(filters.get("source_account_id"))
            rows = conn.execute(
                """
                SELECT DISTINCT lua.*, ua.nickname, ua.profile_url
                FROM lead_user_accounts lua
                JOIN user_accounts ua ON ua.id = lua.account_id
                JOIN lead_sources src ON src.lead_account_id = lua.id
                WHERE src.active = 1
                  AND lua.hidden = 0
                  AND (? = '' OR ua.platform = ?)
                  AND (? = '' OR COALESCE(NULLIF(src.keyword, ''), '未标记关键词') = ?)
                  AND (? = 0 OR src.source_account_id = ?)
                ORDER BY lua.updated_at DESC, lua.id DESC
                """,
                (platform, platform, keyword, keyword, source_account_id, source_account_id),
            ).fetchall()
    return database.rows_to_dicts(rows)


def _target_rows(target_type: str, ids: list[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    table_map = {
        "competitor": ("user_accounts", "nickname", "competitor_status"),
        "lead": ("lead_user_accounts", "follow_status", "screening_status"),
        "content": ("contents", "title", "source_keyword"),
    }
    table, name_col, status_col = table_map[target_type]
    clause, params = _in_clause("id", ids)
    with database.connect() as conn:
        rows = conn.execute(
            f"SELECT id, {name_col} AS name, {status_col} AS status FROM {table} WHERE {clause}",
            params,
        ).fetchall()
    return database.rows_to_dicts(rows)


def _analysis_job_rows(ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    clause, params = _in_clause("id", ids)
    with database.connect() as conn:
        rows = conn.execute(
            f"SELECT id, target_type, target_id, status, error, id AS target_name FROM analysis_jobs WHERE {clause}",
            params,
        ).fetchall()
    return database.rows_to_dicts(rows)


def _keyword_accounts(platform: str, keyword: str, only_competitors: bool) -> list[dict[str, Any]]:
    status_clause = "AND ua.competitor_status = '竞品'" if only_competitors else ""
    with database.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT ua.id, ua.nickname, ua.profile_url, ua.competitor_status
            FROM user_accounts ua
            JOIN account_sources src ON src.account_id = ua.id
            WHERE src.active = 1
              AND (? = '' OR ua.platform = ?)
              AND (? = '' OR COALESCE(NULLIF(src.keyword, ''), '未标记关键词') = ?)
              {status_clause}
            ORDER BY ua.updated_at DESC, ua.id DESC
            """,
            (platform, platform, keyword, keyword),
        ).fetchall()
    return database.rows_to_dicts(rows)


def _busy_analysis_ids(target_type: str, ids: list[int]) -> set[int]:
    if not ids:
        return set()
    clause, params = _in_clause("target_id", ids)
    with database.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT target_id
            FROM analysis_jobs
            WHERE target_type = ?
              AND status IN ('pending', 'running')
              AND {clause}
            """,
            (target_type, *params),
        ).fetchall()
    return {int(row["target_id"]) for row in rows}


def _active_comment_count_for_leads(lead_ids: list[int]) -> int:
    if not lead_ids:
        return 0
    clause, params = _in_clause("lead_account_id", lead_ids)
    with database.connect() as conn:
        row = conn.execute(
            f"SELECT COUNT(DISTINCT comment_id) AS count FROM lead_sources WHERE active = 1 AND comment_id IS NOT NULL AND {clause}",
            params,
        ).fetchone()
    return int(row["count"] or 0) if row else 0


def _is_non_customer(row: dict[str, Any]) -> bool:
    return str(row.get("screening_status") or "") == "非客户" or str(row.get("follow_status") or "") in ("非客户", "无需跟进")


def _account_tombstone_weight(row: dict[str, Any]) -> int:
    return sum(1 for key in ("platform_user_id", "sec_uid", "user_unique_id", "profile_url") if str(row.get(key) or "").strip())


def _sample_rows(rows: list[dict[str, Any]], name_key: str, status_key: str) -> list[dict[str, Any]]:
    return [
        {
            "id": row.get("id") or row.get("target_id") or "",
            "name": row.get(name_key) or row.get("nickname") or row.get("target_name") or "",
            "status": row.get(status_key) or row.get("status") or "",
        }
        for row in rows[:8]
    ]


def _int_ids(values: list[Any]) -> list[int]:
    ids: list[int] = []
    for value in values:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item > 0 and item not in ids:
            ids.append(item)
    return ids


def _positive_int(value: Any) -> int:
    try:
        item = int(value)
    except (TypeError, ValueError):
        return 0
    return item if item > 0 else 0


def _in_clause(column: str, values: list[Any]) -> tuple[str, tuple[Any, ...]]:
    placeholders = ",".join(["?"] * len(values))
    return f"{column} IN ({placeholders})", tuple(values)


def _empty_preview(confirm_text: str, warnings: list[str]) -> dict[str, Any]:
    return {
        "eligible_count": 0,
        "skipped_count": 0,
        "affected_counts": {},
        "tombstone_counts": {},
        "warnings": warnings,
        "sample_rows": [],
        "confirm_text": confirm_text,
    }
