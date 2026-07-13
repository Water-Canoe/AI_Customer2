from __future__ import annotations

import asyncio
import json
import random
import sys
import time
from datetime import datetime
from math import ceil
from typing import Any
from uuid import uuid4

from app import database
from app.services import browser_queue


TARGET_FOLLOW_STATUSES = ("未私信", "已私信", "未回复", "已回复", "未成交", "已成交")
STATUS_ALIASES = {"待私信": "未私信", "全部": ""}
UNLABELED_KEYWORD = "未标记关键词"
_AUTO_DM_LOCKS: dict[int, asyncio.Lock] = {}
ACTIVE_BATCH_STATUSES = {"pending", "running"}


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
            key = f"{customer['platform']}::{keyword}"
            if key not in stats:
                stats[key] = _empty_keyword_stat(keyword, keyword, customer["platform"])
            _apply_customer_to_stat(stats[key], customer)

    result = list(stats.values())
    return [result[0]] + sorted(
        result[1:],
        key=lambda item: (item["keyword"] == UNLABELED_KEYWORD, -int(item["customer_count"]), item["keyword"]),
    )


def list_customers(
    keyword: str = "",
    platform: str = "",
    status: str = "待私信",
    query: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 20)))
    normalized_keyword = _normalize_keyword_filter(keyword)
    normalized_platform = str(platform or "").strip()
    normalized_status = _normalize_status_filter(status)
    normalized_query = str(query or "").strip().lower()

    with database.connect() as conn:
        reminder_days = _reminder_days(conn)
        customers = _aggregate_customers(_target_source_rows(conn), reminder_days)

    filtered = [
        customer for customer in customers
        if _matches_customer(customer, normalized_keyword, normalized_platform, normalized_status, normalized_query)
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


def create_auto_message_batch(
    platform: str,
    keyword: str,
    count: int,
    interval_min_seconds: int,
    interval_max_seconds: int,
    run_now: bool = True,
) -> dict[str, Any]:
    platform = str(platform or "").strip()
    keyword = _normalize_keyword_filter(keyword)
    if platform != "dy":
        raise ValueError("AI一键私信当前只支持抖音")
    if not keyword:
        raise ValueError("请先选择一个具体关键词，不能对“全部”一键私信")
    count = _bounded_int(count, 10, 1, 200)
    interval_min_seconds = _bounded_int(interval_min_seconds, 30, 0, 3600)
    interval_max_seconds = _bounded_int(interval_max_seconds, 60, 0, 3600)
    if interval_max_seconds < interval_min_seconds:
        raise ValueError("最大时间间隔不能小于最小时间间隔")

    with database.connect() as conn:
        active = conn.execute(
            "SELECT id FROM message_batches WHERE status IN ('pending', 'running') ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if active:
            raise ValueError(f"已有自动私信批次 {active['id']} 正在执行，请先等待结束或取消")
        fill_only = database.get_setting(conn, "auto_dm_fill_only", "false") == "true"
        timeout_seconds = _bounded_int(database.get_setting(conn, "auto_dm_timeout_seconds", "300"), 300, 0, 3600)
        customers = _with_selected_scripts(conn, _batch_candidates(conn, platform, keyword))[:count]
        if not customers:
            raise ValueError("当前关键词下没有可自动私信的未私信目标客户或可发送话术")

        batch_id = uuid4().hex[:8]
        conn.execute(
            """
            INSERT INTO message_batches(
                id, platform, keyword, requested_count, interval_min_seconds,
                interval_max_seconds, fill_only, timeout_seconds, status, total_count
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (batch_id, platform, keyword, count, interval_min_seconds, interval_max_seconds, int(fill_only), timeout_seconds, len(customers)),
        )
        for customer in customers:
            conn.execute(
                """
                INSERT INTO message_batch_items(batch_id, lead_account_id, nickname, profile_url, script)
                VALUES(?, ?, ?, ?, ?)
                """,
                (batch_id, customer["lead_id"], customer["nickname"], customer["profile_url"], customer["selected_script"]),
            )

    return get_auto_message_batch(batch_id)


def list_auto_message_batches(batch_id: str = "") -> dict[str, Any]:
    with database.connect() as conn:
        batches = database.rows_to_dicts(
            conn.execute(
                """
                SELECT *
                FROM message_batches
                ORDER BY created_at DESC
                LIMIT 20
                """
            ).fetchall()
        )
        active = next((batch for batch in batches if batch["status"] in ACTIVE_BATCH_STATUSES), None)
        selected_id = str(batch_id or (active or (batches[0] if batches else {})).get("id") or "")
        items = _batch_items(conn, selected_id) if selected_id else []
    return {"batches": batches, "active": active, "selected_batch_id": selected_id, "items": items}


def get_auto_message_batch(batch_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        batch = database.row_to_dict(conn.execute("SELECT * FROM message_batches WHERE id = ?", (batch_id,)).fetchone())
        if not batch:
            raise ValueError("自动私信批次不存在")
        batch["items"] = _batch_items(conn, batch_id)
    return batch


def cancel_auto_message_batch(batch_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        batch = conn.execute("SELECT * FROM message_batches WHERE id = ?", (batch_id,)).fetchone()
        if not batch:
            raise ValueError("自动私信批次不存在")
        if batch["status"] == "pending":
            conn.execute(
                """
                UPDATE message_batches
                SET status = 'cancelled', stop_requested = 1, finished_at = datetime('now', 'localtime'),
                    updated_at = datetime('now', 'localtime'), error = '用户取消'
                WHERE id = ?
                """,
                (batch_id,),
            )
            conn.execute(
                "UPDATE message_batch_items SET status = 'skipped', error = '用户取消', updated_at = datetime('now', 'localtime') WHERE batch_id = ? AND status = 'pending'",
                (batch_id,),
            )
        elif batch["status"] == "running":
            conn.execute(
                "UPDATE message_batches SET stop_requested = 1, error = '用户请求取消', updated_at = datetime('now', 'localtime') WHERE id = ?",
                (batch_id,),
            )
        _refresh_batch_counts(conn, batch_id)
    return get_auto_message_batch(batch_id)


def delete_auto_message_batch(batch_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        batch = conn.execute("SELECT * FROM message_batches WHERE id = ?", (batch_id,)).fetchone()
        if not batch:
            raise ValueError("自动私信批次不存在")
        if batch["status"] in ACTIVE_BATCH_STATUSES:
            raise ValueError("运行中的自动私信批次不能删除，请先取消")
        conn.execute("DELETE FROM message_batches WHERE id = ?", (batch_id,))
    return {"ok": True, "deleted_id": batch_id}


def retry_auto_message_batch(batch_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        active = conn.execute(
            "SELECT id FROM message_batches WHERE status IN ('pending', 'running') ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if active:
            raise ValueError(f"已有自动私信批次 {active['id']} 正在执行，请先等待结束或取消")
        batch = conn.execute("SELECT * FROM message_batches WHERE id = ?", (batch_id,)).fetchone()
        if not batch:
            raise ValueError("自动私信批次不存在")
        if batch["status"] in ACTIVE_BATCH_STATUSES:
            raise ValueError("运行中的自动私信批次不能重试")
        rows = database.rows_to_dicts(
            conn.execute(
                """
                SELECT mbi.*
                FROM message_batch_items mbi
                JOIN lead_user_accounts lua ON lua.id = mbi.lead_account_id
                WHERE mbi.batch_id = ?
                  AND mbi.status IN ('failed', 'skipped', 'pending')
                  AND COALESCE(lua.follow_status, '') IN ('', '待筛选', '未分析', '目标客户', '未私信')
                ORDER BY mbi.id ASC
                """,
                (batch_id,),
            ).fetchall()
        )
        if not rows:
            raise ValueError("当前历史批次没有可重试的未私信客户")
        new_batch_id = uuid4().hex[:8]
        conn.execute(
            """
            INSERT INTO message_batches(
                id, platform, keyword, requested_count, interval_min_seconds,
                interval_max_seconds, fill_only, timeout_seconds, status, total_count
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                new_batch_id,
                batch["platform"],
                batch["keyword"],
                len(rows),
                batch["interval_min_seconds"],
                batch["interval_max_seconds"],
                batch["fill_only"],
                batch["timeout_seconds"],
                len(rows),
            ),
        )
        for row in rows:
            conn.execute(
                """
                INSERT INTO message_batch_items(batch_id, lead_account_id, nickname, profile_url, script)
                VALUES(?, ?, ?, ?, ?)
                """,
                (new_batch_id, row["lead_account_id"], row["nickname"], row["profile_url"], row["script"]),
            )
    return get_auto_message_batch(new_batch_id)


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


async def auto_message_customer(
    lead_id: int,
    dry_run: bool = False,
    timeout_seconds: int = 0,
    message_script: str = "",
    script_label: str = "",
) -> dict[str, Any]:
    detail = customer_detail(lead_id)
    customer = detail["customer"]
    if customer["platform"] != "dy":
        raise ValueError("自动私信当前只支持抖音客户")
    if not customer["profile_url"]:
        raise ValueError("当前客户缺少主页链接，无法自动私信")
    with database.connect() as conn:
        fill_only = database.get_setting(conn, "auto_dm_fill_only", "false") == "true"
        configured_timeout = _bounded_int(database.get_setting(conn, "auto_dm_timeout_seconds", "300"), 300, 0, 3600)
        # 单次自动私信以前端当前选中的话术为准，避免设置同步延迟时误用 AI 话术。
        selected_script = str(message_script or "").strip()
        selected_label = str(script_label or "").strip() or "私信话术"
        if not selected_script:
            selected_script, selected_label = _selected_message_script(conn, customer["script"])
    # 设置页的“只填不发”优先，避免前端旧请求误触发送。
    effective_dry_run = bool(dry_run or fill_only)
    effective_timeout = _bounded_int(timeout_seconds, configured_timeout, 0, 3600) if timeout_seconds else configured_timeout

    sender = _load_douyin_dm_sender()
    async with _auto_dm_lock():
        async with browser_queue.async_browser_slot(f"message_customer:{lead_id}"):
            result = await sender(
                customer["profile_url"],
                selected_script,
                profile_dir=database.get_douyin_cloak_profile_dir(),
                dry_run=effective_dry_run,
                manual_send_timeout_seconds=effective_timeout,
            )

    follow_update: dict[str, Any] | None = None
    current_status = customer["follow_status"] or customer["screening_status"]
    if not effective_dry_run and current_status in {"待筛选", "未分析", "目标客户", "未私信"}:
        from app.services import account_actions

        follow_update = account_actions.update_customer_follow_status(
            lead_id,
            "已私信",
            f"私信工作台：自动发送{selected_label}",
        )
    return {"ok": True, "dm": result, "follow_update": follow_update}


async def run_auto_message_batch(batch_id: str) -> None:
    async with _auto_dm_lock():
        with database.connect() as conn:
            batch = conn.execute("SELECT * FROM message_batches WHERE id = ?", (batch_id,)).fetchone()
            if not batch or batch["status"] not in ACTIVE_BATCH_STATUSES:
                return
            conn.execute(
                """
                UPDATE message_batches
                SET status = 'running', started_at = COALESCE(started_at, datetime('now', 'localtime')),
                    updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (batch_id,),
            )

        # 批量私信只打开一次 CloakBrowser 上下文，避免每个客户重复启动浏览器。
        module = _load_douyin_dm_module()
        context = None
        page = None
        try:
            async with browser_queue.async_browser_slot(
                f"message_batch:{batch_id}",
                should_stop=lambda: _message_batch_stop_requested(batch_id),
            ):
                context = await module.open_douyin_context(profile_dir=database.get_douyin_cloak_profile_dir())
                page = context.pages[0] if context.pages else await context.new_page()
                while True:
                    with database.connect() as conn:
                        batch = conn.execute("SELECT * FROM message_batches WHERE id = ?", (batch_id,)).fetchone()
                        if not batch or int(batch["stop_requested"] or 0):
                            _cancel_pending_items(conn, batch_id)
                            _finish_batch(conn, batch_id, "cancelled", "用户取消")
                            return
                        item = conn.execute(
                            """
                            SELECT *
                            FROM message_batch_items
                            WHERE batch_id = ? AND status = 'pending'
                            ORDER BY id ASC
                            LIMIT 1
                            """,
                            (batch_id,),
                        ).fetchone()
                        if not item:
                            _finish_batch(conn, batch_id, "succeeded", "")
                            return
                        conn.execute(
                            """
                            UPDATE message_batch_items
                            SET status = 'running', started_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
                            WHERE id = ?
                            """,
                            (item["id"],),
                        )
                        conn.execute(
                            "UPDATE message_batches SET current_lead_id = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
                            (item["lead_account_id"], batch_id),
                        )

                    if page.is_closed():
                        page = await context.new_page()
                    await _run_batch_item(page, database.row_to_dict(item) or {}, database.row_to_dict(batch) or {}, module)
                    await _sleep_between_batch_items(batch_id, int(batch["interval_min_seconds"] or 0), int(batch["interval_max_seconds"] or 0))
        except browser_queue.BrowserQueueCancelled:
            with database.connect() as conn:
                _cancel_pending_items(conn, batch_id)
                _finish_batch(conn, batch_id, "cancelled", "用户取消")
        except Exception as exc:
            with database.connect() as conn:
                _fail_pending_items(conn, batch_id, str(exc))
                _finish_batch(conn, batch_id, "failed", str(exc))
        finally:
            if context is not None:
                await context.close()


def _auto_dm_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = id(loop)
    if key not in _AUTO_DM_LOCKS:
        # 后台线程可能创建独立事件循环，锁按 loop 隔离避免跨 loop 复用失败。
        _AUTO_DM_LOCKS[key] = asyncio.Lock()
    return _AUTO_DM_LOCKS[key]


async def _run_batch_item(page: Any, item: dict[str, Any], batch: dict[str, Any], module: Any) -> None:
    try:
        if not str(item.get("profile_url") or "").strip():
            _mark_batch_item(item["id"], "skipped", "缺少客户主页链接")
            return
        if not str(item.get("script") or "").strip():
            _mark_batch_item(item["id"], "skipped", "缺少私信话术")
            return
        fill_only = bool(batch.get("fill_only"))
        await module.send_douyin_dm_on_page(
            page,
            item["profile_url"],
            item["script"],
            login_wait_seconds=300,
            dry_run=fill_only,
            manual_send_timeout_seconds=int(batch.get("timeout_seconds") or 0) if fill_only else 0,
        )
        if fill_only:
            _mark_batch_item(item["id"], "skipped", "只填内容不发送，未自动标记已私信")
            return

        from app.services import account_actions

        account_actions.update_customer_follow_status(
            int(item["lead_account_id"]),
            "已私信",
            f"AI一键私信批次 {batch['id']} 自动发送",
        )
        _mark_batch_item(item["id"], "succeeded", "")
    except Exception as exc:
        _mark_batch_item(item["id"], "failed", str(exc))


async def _sleep_between_batch_items(batch_id: str, minimum: int, maximum: int) -> None:
    with database.connect() as conn:
        pending = conn.execute(
            "SELECT 1 FROM message_batch_items WHERE batch_id = ? AND status = 'pending' LIMIT 1",
            (batch_id,),
        ).fetchone()
        batch = conn.execute("SELECT stop_requested FROM message_batches WHERE id = ?", (batch_id,)).fetchone()
    if not pending or (batch and int(batch["stop_requested"] or 0)):
        return
    delay = random.randint(minimum, maximum) if maximum > minimum else minimum
    deadline = time.monotonic() + max(0, delay)
    while time.monotonic() < deadline:
        if _message_batch_stop_requested(batch_id):
            return
        await asyncio.sleep(min(1.0, deadline - time.monotonic()))


def _message_batch_stop_requested(batch_id: str) -> bool:
    with database.connect() as conn:
        row = conn.execute("SELECT stop_requested FROM message_batches WHERE id = ?", (batch_id,)).fetchone()
    return bool(row and int(row["stop_requested"] or 0))


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, minimum), maximum)


def _load_douyin_dm_sender() -> Any:
    return _load_douyin_dm_module().send_douyin_dm


def _load_douyin_dm_module() -> Any:
    workspace_root = str(database.WORKSPACE_ROOT)
    if workspace_root not in sys.path:
        # 开发服务通常从 backend 启动，需要显式暴露根目录下的 tools 包。
        sys.path.insert(0, workspace_root)
    try:
        from tools.douyin_dm_automation import automation
    except ImportError as exc:
        raise ValueError(f"自动私信组件加载失败：{exc}") from exc
    return automation


def _batch_candidates(conn, platform: str, keyword: str) -> list[dict[str, Any]]:
    # 一键私信只处理当前关键词下仍处于“未私信”的目标客户。
    customers = _aggregate_customers(_target_source_rows(conn), _reminder_days(conn))
    rows = [
        customer for customer in customers
        if _matches_customer(customer, keyword, platform, "未私信", "")
    ]
    rows.sort(key=lambda item: (item["comment_at"] or item["latest_at"] or item["updated_at"] or ""), reverse=True)
    return rows


def _with_selected_scripts(conn, customers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for customer in customers:
        try:
            script, _ = _selected_message_script(conn, customer.get("script"))
        except ValueError:
            if _dm_script_mode(conn) == "fixed":
                raise
            continue
        selected.append({**customer, "selected_script": script})
    return selected


def _selected_message_script(conn, ai_script: Any) -> tuple[str, str]:
    if _dm_script_mode(conn) == "fixed":
        script = str(database.get_setting(conn, "fixed_dm_script", "") or "").strip()
        if not script:
            raise ValueError("固定话术为空，请先到设置页填写固定话术")
        return script, "固定话术"
    script = str(ai_script or "").strip()
    if not script:
        raise ValueError("当前客户暂无AI话术，请先做意向分析")
    return script, "AI话术"


def _dm_script_mode(conn) -> str:
    return "fixed" if database.get_setting(conn, "dm_script_mode", "ai") == "fixed" else "ai"


def _batch_items(conn, batch_id: str) -> list[dict[str, Any]]:
    if not batch_id:
        return []
    return database.rows_to_dicts(
        conn.execute(
            """
            SELECT *
            FROM message_batch_items
            WHERE batch_id = ?
            ORDER BY id ASC
            """,
            (batch_id,),
        ).fetchall()
    )


def _mark_batch_item(item_id: int, status: str, error: str) -> None:
    with database.connect() as conn:
        row = conn.execute("SELECT batch_id FROM message_batch_items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return
        conn.execute(
            """
            UPDATE message_batch_items
            SET status = ?, error = ?, finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (status, error, item_id),
        )
        _refresh_batch_counts(conn, row["batch_id"])


def _refresh_batch_counts(conn, batch_id: str) -> None:
    counts = conn.execute(
        """
        SELECT
            SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS success_count,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
            SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) AS skipped_count
        FROM message_batch_items
        WHERE batch_id = ?
        """,
        (batch_id,),
    ).fetchone()
    conn.execute(
        """
        UPDATE message_batches
        SET success_count = ?, failed_count = ?, skipped_count = ?, updated_at = datetime('now', 'localtime')
        WHERE id = ?
        """,
        (counts["success_count"] or 0, counts["failed_count"] or 0, counts["skipped_count"] or 0, batch_id),
    )


def _cancel_pending_items(conn, batch_id: str) -> None:
    conn.execute(
        """
        UPDATE message_batch_items
        SET status = 'skipped', error = '用户取消', finished_at = datetime('now', 'localtime'),
            updated_at = datetime('now', 'localtime')
        WHERE batch_id = ? AND status = 'pending'
        """,
        (batch_id,),
    )
    _refresh_batch_counts(conn, batch_id)


def _fail_pending_items(conn, batch_id: str, error: str) -> None:
    # 浏览器启动等批次级失败会影响所有未开始客户，统一标为失败便于排查。
    conn.execute(
        """
        UPDATE message_batch_items
        SET status = 'failed', error = ?, finished_at = datetime('now', 'localtime'),
            updated_at = datetime('now', 'localtime')
        WHERE batch_id = ? AND status IN ('pending', 'running')
        """,
        (error, batch_id),
    )
    _refresh_batch_counts(conn, batch_id)


def _finish_batch(conn, batch_id: str, status: str, error: str) -> None:
    _refresh_batch_counts(conn, batch_id)
    conn.execute(
        """
        UPDATE message_batches
        SET status = ?, error = ?, current_lead_id = NULL,
            finished_at = COALESCE(finished_at, datetime('now', 'localtime')),
            updated_at = datetime('now', 'localtime')
        WHERE id = ?
        """,
        (status, error, batch_id),
    )


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


def _empty_keyword_stat(keyword: str, label: str, platform: str = "") -> dict[str, Any]:
    return {
        "keyword": keyword,
        "label": label,
        "platform": platform,
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


def _matches_customer(customer: dict[str, Any], keyword: str, platform: str, status: str, query: str) -> bool:
    if platform and customer["platform"] != platform:
        return False
    if keyword and keyword not in customer["keywords"]:
        return False
    # “已超时”复用提醒计算，不写入新的跟进状态。
    if status == "已超时" and not customer["overdue"]:
        return False
    if status and status != "已超时" and customer["follow_status"] != status:
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
