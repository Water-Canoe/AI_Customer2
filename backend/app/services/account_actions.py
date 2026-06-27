from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import sqlite3
from fastapi import HTTPException

from app import database
from app.services import ai_service, crawler_adapter
from app.services.deletion import RAW_DELETE_ALLOWLIST


ACCOUNT_ANALYSIS_CONTENT_COUNT = 5


def create_account_analysis_task(account_id: int) -> dict[str, object]:
    return crawler_adapter.create_profile_enrichment_task(
        account_id,
        mode="account_analysis",
        name_prefix="账号分析",
        content_count=ACCOUNT_ANALYSIS_CONTENT_COUNT,
    )


def run_account_analysis(account_id: int, task_id: str) -> None:
    crawler_adapter.run_task(task_id)
    task = crawler_adapter.get_task(task_id)
    if not task or task.get("status") != "succeeded":
        with database.connect() as conn:
            crawler_adapter.log_task(conn, task_id, "error", "账号分析已停止：主页和视频采集未成功，未触发 AI 分析")
        return

    try:
        job = ai_service.create_ai_job("competitor", account_id, run_now=True)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, ensure_ascii=False)
        with database.connect() as conn:
            crawler_adapter.log_task(conn, task_id, "error", f"账号资料已导入，但 AI 分析失败：{detail}")
        return
    except Exception as exc:
        with database.connect() as conn:
            crawler_adapter.log_task(conn, task_id, "error", f"账号资料已导入，但 AI 分析异常：{exc}")
        return

    with database.connect() as conn:
        crawler_adapter.log_task(conn, task_id, "info", f"账号 AI 分析完成：{job['id']}")


def delete_keyword_non_competitors(platform: str, keyword: str) -> dict[str, Any]:
    keyword_value = keyword or "未标记关键词"
    with database.connect() as conn:
        account_rows = conn.execute(
            """
            SELECT DISTINCT ua.id
            FROM contents c
            JOIN user_accounts ua ON ua.id = c.author_account_id
            WHERE c.platform = ?
              AND COALESCE(NULLIF(c.source_keyword, ''), '未标记关键词') = ?
              AND ua.competitor_status = '非竞品'
            ORDER BY ua.id
            """,
            (platform, keyword_value),
        ).fetchall()
        account_ids = [int(row["id"]) for row in account_rows]
        if not account_ids:
            return {"ok": True, "deleted": 0, "keyword": keyword_value, "account_ids": []}

        placeholders = ",".join(["?"] * len(account_ids))
        refs = conn.execute(
            f"""
            SELECT *
            FROM raw_source_refs
            WHERE entity_type = 'account' AND entity_id IN ({placeholders})
            ORDER BY entity_id
            """,
            account_ids,
        ).fetchall()
        refs_by_account: dict[int, list[dict[str, Any]]] = {account_id: [] for account_id in account_ids}
        for ref in refs:
            refs_by_account[int(ref["entity_id"])].append(database.row_to_dict(ref))
        missing = [account_id for account_id, items in refs_by_account.items() if not items]
        if missing:
            raise HTTPException(status_code=409, detail=f"以下非竞品账号缺少底层映射，不能批量硬删除：{missing}")
        raw_db = database.get_setting(conn, "media_crawler_db_path", str(database.DEFAULT_MEDIA_CRAWLER_DB)).strip().strip('"').strip("'")

    raw_path = Path(raw_db)
    if not raw_path.exists():
        raise HTTPException(status_code=409, detail=f"MediaCrawler SQLite 不存在：{raw_db}")

    flat_refs = [ref for items in refs_by_account.values() for ref in items]
    for ref in flat_refs:
        raw_table = str(ref["raw_table"])
        if raw_table not in RAW_DELETE_ALLOWLIST:
            raise HTTPException(status_code=409, detail=f"未允许删除的底层表：{raw_table}")

    raw_conn = sqlite3.connect(raw_path)
    try:
        for ref in flat_refs:
            raw_conn.execute(f"DELETE FROM {str(ref['raw_table'])} WHERE rowid = ?", (str(ref["raw_pk"]),))
        raw_conn.commit()
    finally:
        raw_conn.close()

    with database.connect() as conn:
        for account_id in account_ids:
            conn.execute("DELETE FROM user_accounts WHERE id = ?", (account_id,))
        conn.execute(
            "INSERT INTO deletion_audit(entity_type, entity_id, hard_delete, detail) VALUES('keyword_non_competitors', 0, 1, ?)",
            (
                json.dumps(
                    {
                        "platform": platform,
                        "keyword": keyword_value,
                        "account_ids": account_ids,
                        "raw_refs": len(flat_refs),
                    },
                    ensure_ascii=False,
                ),
            ),
        )
    return {"ok": True, "deleted": len(account_ids), "keyword": keyword_value, "account_ids": account_ids, "raw_refs": len(flat_refs)}
