from __future__ import annotations

import json
from datetime import datetime
from math import ceil
from typing import Any

from app import database


TARGET_FOLLOW_STATUSES = ("未私信", "已私信", "未回复", "已回复", "未成交", "已成交")
STATUS_ALIASES = {"待私信": "未私信", "全部": ""}
UNLABELED_KEYWORD = "未标记关键词"


def list_keywords() -> list[dict[str, Any]]:
    with database.connect() as conn:
        reminder_days = _reminder_days(conn)
        customers = _aggregate_customers(_target_source_rows(conn), reminder_days)

    stats: dict[str, dict[str, Any]] = {
        "": _empty_keyword_stat("", "全部"),
    }
    for customer in customers:
        _apply_customer_to_stat(stats[""], customer)
        for keyword in customer["keywords"]:
            if keyword not in stats:
                stats[keyword] = _empty_keyword_stat(keyword, keyword)
            _apply_customer_to_stat(stats[keyword], customer)

    result = list(stats.values())
    return [result[0]] + sorted(
        result[1:],
        key=lambda item: (item["keyword"] == UNLABELED_KEYWORD, -int(item["customer_count"]), item["keyword"]),
    )


def list_customers(
    keyword: str = "",
    status: str = "待私信",
    query: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 20)))
    normalized_keyword = _normalize_keyword_filter(keyword)
    normalized_status = _normalize_status_filter(status)
    normalized_query = str(query or "").strip().lower()

    with database.connect() as conn:
        reminder_days = _reminder_days(conn)
        customers = _aggregate_customers(_target_source_rows(conn), reminder_days)

    filtered = [
        customer for customer in customers
        if _matches_customer(customer, normalized_keyword, normalized_status, normalized_query)
    ]
    filtered.sort(key=lambda item: (not item["overdue"], item["latest_at"] or "", item["updated_at"] or ""), reverse=True)

    total = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size
    rows = filtered[start:end]
    return {
        "rows": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, ceil(total / page_size)) if total else 1,
    }


def customer_detail(lead_id: int) -> dict[str, Any]:
    with database.connect() as conn:
        reminder_days = _reminder_days(conn)
        rows = _target_source_rows(conn, lead_id=lead_id)
        customers = _aggregate_customers(rows, reminder_days)
        if not customers:
            raise ValueError("客户不存在或没有有效跟进来源")
        events = database.rows_to_dicts(
            conn.execute(
                """
                SELECT id, from_status, to_status, note, created_at
                FROM lead_status_events
                WHERE lead_account_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (lead_id,),
            ).fetchall()
        )
    return {
        "customer": customers[0],
        "sources": customers[0]["sources"],
        "events": events,
    }


def _target_source_rows(conn, lead_id: int | None = None) -> list[Any]:
    target_placeholders = ",".join(["?"] * len(TARGET_FOLLOW_STATUSES))
    params: list[Any] = list(TARGET_FOLLOW_STATUSES)
    lead_clause = ""
    if lead_id is not None:
        lead_clause = " AND lua.id = ?"
        params.append(lead_id)

    return conn.execute(
        f"""
        SELECT
            lua.id AS lead_id,
            lua.account_id,
            lua.screening_status,
            lua.follow_status,
            lua.intention,
            lua.reason,
            lua.suggested_action,
            lua.script,
            lua.hidden,
            lua.created_at AS lead_created_at,
            lua.updated_at,
            ua.platform,
            ua.nickname,
            ua.profile_url,
            ua.signature,
            ls.id AS source_id,
            COALESCE(
                NULLIF(ls.keyword, ''),
                NULLIF(ct.source_keyword, ''),
                (
                    SELECT NULLIF(acs.keyword, '')
                    FROM account_sources acs
                    WHERE acs.account_id = COALESCE(ls.source_account_id, ct.author_account_id)
                      AND acs.active = 1
                      AND NULLIF(acs.keyword, '') IS NOT NULL
                    ORDER BY acs.created_at DESC, acs.id DESC
                    LIMIT 1
                ),
                NULLIF(j.keywords, ''),
                ?
            ) AS keyword,
            ls.source_type,
            ls.created_at AS source_created_at,
            cm.id AS comment_row_id,
            cm.body AS comment_body,
            cm.created_at AS comment_created_at,
            cm.raw_payload AS comment_raw_payload,
            ct.id AS content_row_id,
            ct.title AS content_title,
            ct.description AS content_description,
            ct.content_url,
            ct.created_at AS content_created_at,
            src.id AS source_account_id,
            src.nickname AS source_account_name,
            src.profile_url AS source_account_url,
            (
                SELECT MIN(e.created_at)
                FROM lead_status_events e
                WHERE e.lead_account_id = lua.id AND e.to_status = '已私信'
            ) AS private_message_at,
            (
                SELECT MIN(e.created_at)
                FROM lead_status_events e
                WHERE e.lead_account_id = lua.id AND e.to_status = '已回复'
            ) AS reply_at,
            (
                SELECT MAX(e.created_at)
                FROM lead_status_events e
                WHERE e.lead_account_id = lua.id
            ) AS last_follow_at
        FROM lead_sources ls
        JOIN lead_user_accounts lua ON lua.id = ls.lead_account_id
        JOIN user_accounts ua ON ua.id = lua.account_id
        LEFT JOIN comments cm ON cm.id = ls.comment_id
        LEFT JOIN contents ct ON ct.id = ls.content_id
        LEFT JOIN user_accounts src ON src.id = ls.source_account_id
        LEFT JOIN crawl_jobs j ON j.id = ls.task_id
        WHERE ls.active = 1
          AND lua.hidden = 0
          AND (
            lua.screening_status = '目标客户'
            OR lua.follow_status IN ({target_placeholders})
          )
          AND lua.follow_status NOT IN ('非客户', '无需跟进', '已移出', '隐藏')
          {lead_clause}
        ORDER BY lua.updated_at DESC, ls.created_at DESC, ls.id DESC
        """,
        [UNLABELED_KEYWORD, *params],
    ).fetchall()


def _aggregate_customers(rows: list[Any], reminder_days: int) -> list[dict[str, Any]]:
    customers: dict[int, dict[str, Any]] = {}
    for row in rows:
        lead_id = int(row["lead_id"])
        customer = customers.get(lead_id)
        if customer is None:
            customer = _customer_base(row, reminder_days)
            customers[lead_id] = customer

        keyword = str(row["keyword"] or UNLABELED_KEYWORD)
        if keyword not in customer["keywords"]:
            customer["keywords"].append(keyword)

        source = _source_item(row)
        customer["sources"].append(source)
        if not customer.get("comment_text") and source["comment_text"]:
            customer["comment_text"] = source["comment_text"]
            customer["comment_at"] = source["comment_at"]
        if not customer.get("video_text") and source["video_text"]:
            customer["video_text"] = source["video_text"]
            customer["content_url"] = source["content_url"]
        if not customer.get("source_account_name") and source["source_account_name"]:
            customer["source_account_name"] = source["source_account_name"]
            customer["source_account_url"] = source["source_account_url"]
        latest = source["source_created_at"] or source["comment_at"] or source["content_created_at"] or ""
        if latest > str(customer.get("latest_at") or ""):
            customer["latest_at"] = latest

    for customer in customers.values():
        customer["source_count"] = len(customer["sources"])
        customer["keyword_text"] = " / ".join(customer["keywords"])
    return list(customers.values())


def _customer_base(row: Any, reminder_days: int) -> dict[str, Any]:
    private_message_at = str(row["private_message_at"] or "")
    follow_status = str(row["follow_status"] or "")
    overdue = _is_overdue(follow_status, private_message_at, reminder_days)
    return {
        "id": int(row["lead_id"]),
        "lead_id": int(row["lead_id"]),
        "account_id": int(row["account_id"]),
        "platform": row["platform"],
        "nickname": row["nickname"] or f"客户 {row['lead_id']}",
        "profile_url": row["profile_url"] or "",
        "signature": row["signature"] or "",
        "screening_status": row["screening_status"] or "",
        "follow_status": follow_status,
        "intention": row["intention"] or "",
        "reason": row["reason"] or "",
        "suggested_action": row["suggested_action"] or "",
        "script": row["script"] or "",
        "updated_at": row["updated_at"] or "",
        "private_message_at": private_message_at,
        "reply_at": row["reply_at"] or "",
        "last_follow_at": row["last_follow_at"] or "",
        "overdue": overdue,
        "overdue_days": _overdue_days(private_message_at) if overdue else 0,
        "keywords": [],
        "keyword_text": "",
        "comment_text": "",
        "comment_at": "",
        "video_text": "",
        "content_url": "",
        "source_account_name": "",
        "source_account_url": "",
        "latest_at": row["updated_at"] or "",
        "source_count": 0,
        "sources": [],
    }


def _source_item(row: Any) -> dict[str, Any]:
    content_text = _join_text(row["content_title"], row["content_description"])
    fallback_comment_at = row["comment_created_at"] or row["source_created_at"] or ""
    comment_at = _raw_timestamp(row["comment_raw_payload"], fallback_comment_at)
    return {
        "source_id": int(row["source_id"]),
        "keyword": row["keyword"] or UNLABELED_KEYWORD,
        "source_type": row["source_type"] or "",
        "comment_id": row["comment_row_id"],
        "comment_text": row["comment_body"] or "",
        "comment_at": comment_at,
        "content_id": row["content_row_id"],
        "video_text": content_text,
        "content_url": row["content_url"] or "",
        "content_created_at": row["content_created_at"] or "",
        "source_account_id": row["source_account_id"],
        "source_account_name": row["source_account_name"] or "",
        "source_account_url": row["source_account_url"] or "",
        "source_created_at": row["source_created_at"] or "",
    }


def _empty_keyword_stat(keyword: str, label: str) -> dict[str, Any]:
    return {
        "keyword": keyword,
        "label": label,
        "customer_count": 0,
        "unmessaged_count": 0,
        "messaged_count": 0,
        "waiting_reply_count": 0,
        "overdue_count": 0,
    }


def _apply_customer_to_stat(stat: dict[str, Any], customer: dict[str, Any]) -> None:
    stat["customer_count"] += 1
    status = customer["follow_status"]
    if status == "未私信":
        stat["unmessaged_count"] += 1
    if status == "已私信":
        stat["messaged_count"] += 1
    if status == "未回复":
        stat["waiting_reply_count"] += 1
    if customer["overdue"]:
        stat["overdue_count"] += 1


def _matches_customer(customer: dict[str, Any], keyword: str, status: str, query: str) -> bool:
    if keyword and keyword not in customer["keywords"]:
        return False
    if status and customer["follow_status"] != status:
        return False
    if query:
        haystack = "\n".join(
            str(customer.get(key, ""))
            for key in (
                "nickname",
                "signature",
                "keyword_text",
                "comment_text",
                "video_text",
                "source_account_name",
                "reason",
                "script",
            )
        ).lower()
        if query not in haystack:
            return False
    return True


def _normalize_keyword_filter(keyword: str) -> str:
    value = str(keyword or "").strip()
    return "" if value == "全部" else value


def _normalize_status_filter(status: str) -> str:
    value = str(status or "").strip()
    return STATUS_ALIASES.get(value, value)


def _reminder_days(conn) -> int:
    try:
        return max(0, int(database.get_setting(conn, "unreplied_reminder_days", "3") or 0))
    except (TypeError, ValueError):
        return 3


def _is_overdue(status: str, private_message_at: str, reminder_days: int) -> bool:
    if reminder_days <= 0 or status not in {"已私信", "未回复"} or not private_message_at:
        return False
    private_at = _parse_datetime(private_message_at)
    if private_at is None:
        return False
    return (datetime.now() - private_at).days >= reminder_days


def _overdue_days(private_message_at: str) -> int:
    private_at = _parse_datetime(private_message_at)
    if private_at is None:
        return 0
    return max(0, (datetime.now() - private_at).days)


def _parse_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is not None:
                parsed = parsed.replace(tzinfo=None)
            return parsed
        except ValueError:
            pass
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _raw_timestamp(raw_payload: Any, fallback: str) -> str:
    payload = raw_payload
    if isinstance(payload, str):
        try:
            payload = json.loads(payload or "{}")
        except json.JSONDecodeError:
            payload = {}
    if not isinstance(payload, dict):
        return fallback
    for key in ("create_time", "time", "created_at"):
        value = payload.get(key)
        if value in ("", None):
            continue
        try:
            timestamp = float(str(value))
        except ValueError:
            continue
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        if timestamp > 1_000_000_000:
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    return fallback


def _join_text(*parts: Any) -> str:
    values: list[str] = []
    for part in parts:
        text = str(part or "").strip()
        if text and text not in values:
            values.append(text)
    return " ".join(values)
