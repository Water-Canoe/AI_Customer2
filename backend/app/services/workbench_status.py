from __future__ import annotations

import sqlite3
from typing import Any

from app import database


SCOPES = {"lead", "traffic", "content", "automation", "runtime"}


def get_status(scope: str) -> dict[str, Any]:
    scope = str(scope or "lead").strip()
    if scope not in SCOPES:
        raise ValueError("未知工作台")

    # 顶部栏只读取聚合计数，避免轮询业务明细列表。
    with database.connect() as conn:
        active = _count(conn, "SELECT COUNT(*) FROM runtime_jobs WHERE status IN ('queued', 'running')") > 0
        metrics = {
            "lead": _lead_metrics,
            "traffic": _traffic_metrics,
            "content": _content_metrics,
            "automation": _automation_metrics,
            "runtime": _runtime_metrics,
        }[scope](conn)
    return {"scope": scope, "active": active, "metrics": metrics}


def _lead_metrics(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "active_tasks": _count(conn, "SELECT COUNT(*) FROM crawl_jobs WHERE status IN ('pending', 'running') AND archived = 0"),
        "failed_tasks": _count(conn, "SELECT COUNT(*) FROM crawl_jobs WHERE status = 'failed' AND archived = 0"),
        "ai_pending": _count(
            conn,
            """
            SELECT
                (SELECT COUNT(*) FROM user_accounts WHERE account_role = 'competitor_candidate' AND competitor_status = '未分析')
              + (SELECT COUNT(*) FROM lead_user_accounts WHERE hidden = 0 AND follow_status = '待筛选')
            """,
        ),
        "ai_failed": _count(conn, "SELECT COUNT(*) FROM analysis_jobs WHERE status = 'failed'"),
        "message_pending": _count(
            conn,
            """
            SELECT COUNT(*)
            FROM lead_user_accounts lua
            WHERE lua.hidden = 0 AND lua.follow_status = '未私信'
              AND EXISTS (SELECT 1 FROM lead_sources ls WHERE ls.lead_account_id = lua.id AND ls.active = 1)
            """,
        ),
    }


def _traffic_metrics(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "active_runs": _count(conn, "SELECT COUNT(*) FROM traffic_runs WHERE status IN ('queued', 'running') AND archived = 0"),
        "successful_actions": _count(conn, "SELECT COALESCE(SUM(action_success_count), 0) FROM traffic_runs WHERE archived = 0"),
        "failed_actions": _count(conn, "SELECT COALESCE(SUM(failed_count + skipped_count), 0) FROM traffic_runs WHERE archived = 0"),
    }


def _content_metrics(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "assets": _count(conn, "SELECT COUNT(*) FROM content_assets WHERE deleted_at IS NULL"),
        "active_jobs": _count(conn, "SELECT COUNT(*) FROM video_jobs WHERE status IN ('queued', 'running') AND archived = 0"),
        "succeeded_jobs": _count(conn, "SELECT COUNT(*) FROM video_jobs WHERE status = 'succeeded' AND archived = 0"),
        "failed_jobs": _count(conn, "SELECT COUNT(*) FROM video_jobs WHERE status = 'failed' AND archived = 0"),
    }


def _automation_metrics(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "enabled_plans": _count(conn, "SELECT COUNT(*) FROM automation_plans WHERE enabled = 1 AND archived = 0"),
        "active_runs": _count(conn, "SELECT COUNT(*) FROM automation_runs WHERE status IN ('queued', 'running')"),
        "failed_runs": _count(conn, "SELECT COUNT(*) FROM automation_runs WHERE status = 'failed'"),
    }


def _runtime_metrics(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        status: _count(conn, "SELECT COUNT(*) FROM runtime_jobs WHERE status = ?", (status,))
        for status in ("queued", "running", "failed", "interrupted")
    }


def _count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0
