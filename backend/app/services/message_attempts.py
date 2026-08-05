from __future__ import annotations

from typing import Any

from app import database


class MessageQuotaReached(RuntimeError):
    pass


def message_limit_summary(conn: Any | None = None) -> dict[str, Any]:
    if conn is None:
        with database.connect() as own_conn:
            return message_limit_summary(own_conn)
    daily_raw = database.get_setting(conn, "message_daily_limit", "").strip()
    hourly_raw = database.get_setting(conn, "message_hourly_limit", "").strip()
    daily = int(daily_raw) if daily_raw else None
    hourly = int(hourly_raw) if hourly_raw else None
    used_today = int(conn.execute("SELECT COUNT(*) AS c FROM message_send_attempts WHERE date(attempted_at) = date('now', 'localtime')").fetchone()["c"])
    used_hour = int(conn.execute("SELECT COUNT(*) AS c FROM message_send_attempts WHERE attempted_at >= datetime('now', 'localtime', '-1 hour')").fetchone()["c"])
    return {
        "configured": daily is not None and hourly is not None,
        "daily_limit": daily,
        "hourly_limit": hourly,
        "used_today": used_today,
        "used_hour": used_hour,
        "remaining_today": max(0, daily - used_today) if daily is not None else None,
        "remaining_hour": max(0, hourly - used_hour) if hourly is not None else None,
    }


def get_message_limits() -> dict[str, Any]:
    with database.connect() as conn:
        summary = message_limit_summary(conn)
        fill_only = database.get_setting(conn, "auto_dm_fill_only", "false") == "true"
    return {
        **summary,
        "fill_only": fill_only,
        "notice": "仅统计本软件产生的私信，无法感知抖音 App 或其他工具中的手动发送数量。",
    }


def update_message_limits(daily_limit: int, hourly_limit: int) -> dict[str, Any]:
    with database.connect() as conn:
        database.set_setting(conn, "message_daily_limit", str(daily_limit))
        database.set_setting(conn, "message_hourly_limit", str(hourly_limit))
    return get_message_limits()


def reserve_message_attempt(lead_id: int, source: str, source_id: str) -> int:
    with database.connect() as conn:
        # 额度判断和尝试写入放在同一写事务内，避免并发发送突破限额。
        conn.execute("BEGIN IMMEDIATE")
        limits = message_limit_summary(conn)
        if limits["configured"] and int(limits["remaining_today"]) <= 0:
            raise MessageQuotaReached("今日私信额度已用完")
        if limits["configured"] and int(limits["remaining_hour"]) <= 0:
            raise MessageQuotaReached("本小时私信额度已用完")
        attempted = conn.execute(
            "SELECT 1 FROM message_send_attempts WHERE lead_account_id = ? AND date(attempted_at) = date('now', 'localtime') LIMIT 1",
            (lead_id,),
        ).fetchone()
        if attempted:
            raise MessageQuotaReached("该客户今天已尝试私信，不再自动重试")
        cursor = conn.execute(
            "INSERT INTO message_send_attempts(lead_account_id, source, source_id) VALUES(?, ?, ?)",
            (lead_id, source, source_id),
        )
        return int(cursor.lastrowid)


def finish_message_attempt(attempt_id: int, status: str, error: str = "") -> None:
    with database.connect() as conn:
        conn.execute(
            "UPDATE message_send_attempts SET status = ?, error = ?, finished_at = datetime('now', 'localtime') WHERE id = ?",
            (status, error[:2000], attempt_id),
        )


def record_manual_message_attempt(lead_id: int, source_id: str = "") -> int:
    with database.connect() as conn:
        existing = conn.execute(
            "SELECT id FROM message_send_attempts WHERE lead_account_id = ? AND date(attempted_at) = date('now', 'localtime') ORDER BY id LIMIT 1",
            (lead_id,),
        ).fetchone()
        if existing:
            return int(existing["id"])
        cursor = conn.execute(
            "INSERT INTO message_send_attempts(lead_account_id, source, source_id, status, finished_at) VALUES(?, 'manual', ?, 'succeeded', datetime('now', 'localtime'))",
            (lead_id, source_id),
        )
        return int(cursor.lastrowid)
