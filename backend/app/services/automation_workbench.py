from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from app import database
from app.schemas import (
    AutomationPlanCreate,
    AutomationPlanPatch,
    KeywordLeadPlanConfig,
    MessagePlanConfig,
    TaskCreate,
    TrafficAutomationPlanConfig,
)


ACTIVE_RUN_STATUSES = {"queued", "running"}
TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled", "interrupted"}
SCHEDULER_CATCHUP_MINUTES = 10
_SCHEDULER_STOP = threading.Event()
_SCHEDULER_THREAD: threading.Thread | None = None
_SCHEDULER_LOCK = threading.RLock()
LOGGER = logging.getLogger(__name__)


def start_scheduler() -> None:
    global _SCHEDULER_THREAD
    with _SCHEDULER_LOCK:
        if _SCHEDULER_THREAD and _SCHEDULER_THREAD.is_alive():
            return
        _SCHEDULER_STOP.clear()
        _SCHEDULER_THREAD = threading.Thread(target=_scheduler_loop, name="ai-customer-automation-scheduler", daemon=True)
        _SCHEDULER_THREAD.start()


def stop_scheduler(timeout_seconds: float = 3.0) -> None:
    _SCHEDULER_STOP.set()
    thread = _SCHEDULER_THREAD
    if thread and thread.is_alive():
        thread.join(timeout=max(0.0, timeout_seconds))


def scheduler_running() -> bool:
    return bool(_SCHEDULER_THREAD and _SCHEDULER_THREAD.is_alive())


def _scheduler_loop() -> None:
    # 仅补跑启动前十分钟，覆盖短暂重启且避免重放长时间停机任务。
    last_scan = _scheduler_scan_start(datetime.now())
    while not _SCHEDULER_STOP.wait(10.0):
        now = datetime.now()
        try:
            trigger_due_plans(last_scan, now)
        except Exception:
            # 单次数据库或校验异常不能终止后续时刻的调度检查。
            LOGGER.exception("自动化调度检查失败")
        finally:
            last_scan = now


def trigger_due_plans(start: datetime, end: datetime) -> list[dict[str, Any]]:
    if end <= start:
        return []
    with database.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM automation_plans WHERE enabled = 1 AND archived = 0 ORDER BY run_time, sort_order, id"
        ).fetchall()
    created: list[dict[str, Any]] = []
    for row in rows:
        weekdays = _loads(row["weekdays"], [])
        cursor = start.date()
        while cursor <= end.date():
            if cursor.isoweekday() in weekdays:
                scheduled_at = datetime.fromisoformat(f"{cursor.isoformat()} {row['run_time']}:00")
                if start < scheduled_at <= end:
                    try:
                        _ensure_plan_authorized(str(row["plan_type"]))
                        created.append(create_run(str(row["id"]), "scheduled", scheduled_at))
                    except ValueError as exc:
                        created.append(_record_scheduled_issue(row, scheduled_at, "skipped", str(exc)))
                    except Exception as exc:
                        # 单个计划异常只记录本次失败，后续同一时刻计划继续创建。
                        LOGGER.exception("自动化计划触发失败：%s", row["id"])
                        try:
                            created.append(_record_scheduled_issue(row, scheduled_at, "failed", str(exc)))
                        except Exception:
                            LOGGER.exception("自动化计划失败记录写入失败：%s", row["id"])
            cursor += timedelta(days=1)
    return created


def _scheduler_scan_start(now: datetime) -> datetime:
    return now - timedelta(minutes=SCHEDULER_CATCHUP_MINUTES)


def _record_scheduled_issue(
    plan: Any,
    scheduled_at: datetime,
    status: str,
    reason: str,
) -> dict[str, Any]:
    run_id = uuid4().hex[:12]
    with database.connect() as conn:
        existing = conn.execute(
            "SELECT id FROM automation_runs WHERE plan_id = ? AND scheduled_at = ?",
            (plan["id"], scheduled_at.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchone()
        if existing:
            existing_id = str(existing["id"])
        else:
            conn.execute(
                """
                INSERT INTO automation_runs(
                    id, plan_id, plan_name, plan_type, trigger_type, scheduled_at,
                    config_snapshot, status, current_stage, error, finished_at
                ) VALUES(?, ?, ?, ?, 'scheduled', ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                """,
                (
                    run_id,
                    plan["id"],
                    plan["name"],
                    plan["plan_type"],
                    scheduled_at.strftime("%Y-%m-%d %H:%M:%S"),
                    plan["config"],
                    status,
                    status,
                    reason[:2000],
                ),
            )
            existing_id = run_id
    return get_run(existing_id)


def list_plans() -> dict[str, Any]:
    now = datetime.now()
    with database.connect() as conn:
        rows = conn.execute("SELECT * FROM automation_plans WHERE archived = 0 ORDER BY sort_order, id").fetchall()
        plans = [_format_plan(conn, row, now) for row in rows]
        active = int(conn.execute("SELECT COUNT(*) AS c FROM automation_runs WHERE status IN ('queued', 'running')").fetchone()["c"])
        failed_today = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM automation_runs WHERE status IN ('failed', 'partial') AND date(created_at) = date('now', 'localtime')"
            ).fetchone()["c"]
        )
    next_values = [item["next_run_at"] for item in plans if item["enabled"] and item["next_run_at"]]
    return {
        "items": plans,
        "summary": {
            "enabled": sum(1 for item in plans if item["enabled"]),
            "next_run_at": min(next_values) if next_values else "",
            "running": active,
            "failed_today": failed_today,
        },
    }


def create_plan(payload: AutomationPlanCreate) -> dict[str, Any]:
    _validate_message_plan_state(payload.plan_type, payload.config, payload.enabled)
    plan_id = uuid4().hex[:12]
    with database.connect() as conn:
        sort_order = int(conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS value FROM automation_plans WHERE archived = 0").fetchone()["value"])
        conn.execute(
            """
            INSERT INTO automation_plans(id, name, plan_type, weekdays, run_time, config, enabled, sort_order)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                payload.name.strip(),
                payload.plan_type,
                json.dumps(payload.weekdays, ensure_ascii=False),
                payload.run_time,
                json.dumps(payload.config.model_dump(), ensure_ascii=False),
                int(payload.enabled),
                sort_order,
            ),
        )
    return get_plan(plan_id)


def reorder_plans(plan_ids: list[str]) -> dict[str, Any]:
    clean_ids = [str(plan_id).strip() for plan_id in plan_ids]
    if any(not plan_id for plan_id in clean_ids) or len(clean_ids) != len(set(clean_ids)):
        raise ValueError("计划顺序包含无效或重复ID")
    with database.connect() as conn:
        current_ids = [str(row["id"]) for row in conn.execute("SELECT id FROM automation_plans WHERE archived = 0").fetchall()]
        if len(clean_ids) != len(current_ids) or set(clean_ids) != set(current_ids):
            raise ValueError("计划列表已变化，请刷新后重新拖动")
        for index, plan_id in enumerate(clean_ids):
            conn.execute("UPDATE automation_plans SET sort_order = ?, updated_at = datetime('now', 'localtime') WHERE id = ?", (index, plan_id))
            conn.execute(
                """
                UPDATE runtime_jobs SET priority = ?, updated_at = datetime('now', 'localtime')
                WHERE kind = 'automation_run' AND status = 'queued'
                  AND entity_id IN (SELECT id FROM automation_runs WHERE plan_id = ?)
                """,
                (-index, plan_id),
            )
    return list_plans()


def update_plan(plan_id: str, payload: AutomationPlanPatch) -> dict[str, Any]:
    current = get_plan(plan_id)
    values = payload.model_dump(exclude_unset=True)
    if "weekdays" in values:
        weekdays = sorted(set(int(value) for value in values["weekdays"]))
        if any(value < 1 or value > 7 for value in weekdays):
            raise ValueError("星期只能是1至7")
        values["weekdays"] = weekdays
    config_value = values.get("config")
    if config_value is not None:
        expected = _config_type(str(current["plan_type"]))
        config_value = expected.model_validate(config_value)
        values["config"] = config_value
    effective_config = config_value or _parse_config(current["plan_type"], current["config"])
    enabled = bool(values.get("enabled", current["enabled"]))
    _validate_message_plan_state(current["plan_type"], effective_config, enabled)

    assignments: list[str] = []
    params: list[Any] = []
    for key in ("name", "run_time", "enabled"):
        if key in values:
            assignments.append(f"{key} = ?")
            params.append(int(values[key]) if key == "enabled" else values[key])
    if "weekdays" in values:
        assignments.append("weekdays = ?")
        params.append(json.dumps(values["weekdays"], ensure_ascii=False))
    if config_value is not None:
        assignments.append("config = ?")
        params.append(json.dumps(config_value.model_dump(), ensure_ascii=False))
    if assignments:
        with database.connect() as conn:
            conn.execute(
                f"UPDATE automation_plans SET {', '.join(assignments)}, updated_at = datetime('now', 'localtime') WHERE id = ?",
                [*params, plan_id],
            )
    return get_plan(plan_id)


def get_plan(plan_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM automation_plans WHERE id = ?", (plan_id,)).fetchone()
        if not row:
            raise ValueError("自动化计划不存在")
        return _format_plan(conn, row, datetime.now())


def archive_plan(plan_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT id FROM automation_plans WHERE id = ?", (plan_id,)).fetchone()
        if not row:
            raise ValueError("自动化计划不存在")
        active = conn.execute(
            "SELECT id FROM automation_runs WHERE plan_id = ? AND status IN ('queued', 'running') LIMIT 1",
            (plan_id,),
        ).fetchone()
        if active:
            raise ValueError("计划正在运行，请先停止后再归档")
        conn.execute(
            "UPDATE automation_plans SET enabled = 0, archived = 1, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (plan_id,),
        )
    return {"ok": True, "id": plan_id}


def create_run(plan_id: str, trigger_type: str = "manual", scheduled_at: datetime | None = None) -> dict[str, Any]:
    plan = get_plan(plan_id)
    config = _parse_config(plan["plan_type"], plan["config"])
    _validate_message_plan_state(plan["plan_type"], config, True)
    run_id = uuid4().hex[:12]
    scheduled_value = scheduled_at.strftime("%Y-%m-%d %H:%M:%S") if scheduled_at else None
    existing_run_id = ""
    skipped = False
    with database.connect() as conn:
        duplicate = conn.execute(
            "SELECT id FROM automation_runs WHERE plan_id = ? AND scheduled_at = ? LIMIT 1",
            (plan_id, scheduled_value),
        ).fetchone() if scheduled_value else None
        if duplicate:
            existing_run_id = str(duplicate["id"])
        active = None if existing_run_id else conn.execute(
                "SELECT id FROM automation_runs WHERE plan_id = ? AND status IN ('queued', 'running') ORDER BY created_at DESC LIMIT 1",
                (plan_id,),
            ).fetchone()
        if active:
            conn.execute(
                """
                INSERT INTO automation_runs(
                    id, plan_id, plan_name, plan_type, trigger_type, scheduled_at,
                    config_snapshot, status, current_stage, error, finished_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, 'skipped', 'skipped', ?, datetime('now', 'localtime'))
                """,
                (run_id, plan_id, plan["name"], plan["plan_type"], trigger_type, scheduled_value, json.dumps(config.model_dump(), ensure_ascii=False), f"计划已有运行实例 {active['id']}")
            )
            skipped = True

        if not skipped and not existing_run_id:
            try:
                conn.execute(
                    """
                    INSERT INTO automation_runs(
                        id, plan_id, plan_name, plan_type, trigger_type, scheduled_at,
                        config_snapshot, status, current_stage
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, 'queued', 'queued')
                    """,
                    (run_id, plan_id, plan["name"], plan["plan_type"], trigger_type, scheduled_value, json.dumps(config.model_dump(), ensure_ascii=False)),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc).upper():
                    row = conn.execute(
                        "SELECT id FROM automation_runs WHERE plan_id = ? AND scheduled_at = ?",
                        (plan_id, scheduled_value),
                    ).fetchone()
                    if row:
                        existing_run_id = str(row["id"])
                    else:
                        active_conflict = conn.execute(
                            "SELECT id FROM automation_runs WHERE plan_id = ? AND status IN ('queued', 'running') ORDER BY created_at DESC LIMIT 1",
                            (plan_id,),
                        ).fetchone()
                        if active_conflict:
                            conn.execute(
                                """
                                INSERT INTO automation_runs(
                                    id, plan_id, plan_name, plan_type, trigger_type, scheduled_at,
                                    config_snapshot, status, current_stage, error, finished_at
                                ) VALUES(?, ?, ?, ?, ?, ?, ?, 'skipped', 'skipped', ?, datetime('now', 'localtime'))
                                """,
                                (run_id, plan_id, plan["name"], plan["plan_type"], trigger_type, scheduled_value, json.dumps(config.model_dump(), ensure_ascii=False), f"计划已有运行实例 {active_conflict['id']}")
                            )
                            skipped = True
                if not existing_run_id:
                    if not skipped:
                        raise

        if not skipped and not existing_run_id and plan["plan_type"] == "keyword_lead":
            selected = _select_keywords(conn, plan_id, config)
            for keyword in selected:
                conn.execute("INSERT INTO automation_run_items(run_id, keyword) VALUES(?, ?)", (run_id, keyword))
            conn.execute("UPDATE automation_runs SET total_count = ? WHERE id = ?", (len(selected), run_id))
        if not skipped and not existing_run_id:
            conn.execute(
                "UPDATE automation_plans SET last_triggered_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime') WHERE id = ?",
                (plan_id,),
            )

    if existing_run_id:
        return get_run(existing_run_id)
    if skipped:
        return get_run(run_id)

    from app.services import job_queue

    runtime_job = job_queue.enqueue_automation_run(run_id)
    with database.connect() as conn:
        conn.execute("UPDATE automation_runs SET runtime_job_id = ? WHERE id = ?", (runtime_job["id"], run_id))
    return get_run(run_id)


def list_runs(page: int = 1, page_size: int = 30) -> dict[str, Any]:
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    with database.connect() as conn:
        total = int(conn.execute("SELECT COUNT(*) AS c FROM automation_runs").fetchone()["c"])
        rows = conn.execute(
            "SELECT * FROM automation_runs ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (page_size, (page - 1) * page_size),
        ).fetchall()
        items = [_format_run(row) for row in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def get_run(run_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise ValueError("自动化运行记录不存在")
        result = _format_run(row)
        result["items"] = [
            _enrich_run_item(conn, item)
            for item in conn.execute("SELECT * FROM automation_run_items WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        ]
        result["traffic_run"] = _traffic_run_summary(str(result.get("traffic_run_id") or ""))
    return result


def cancel_run(run_id: str, *, reason: str = "用户停止") -> dict[str, Any]:
    already_terminal = False
    with database.connect() as conn:
        run = conn.execute("SELECT * FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
        if not run:
            raise ValueError("自动化运行记录不存在")
        if str(run["status"]) not in ACTIVE_RUN_STATUSES:
            already_terminal = True
        elif str(run["status"]) == "queued":
            conn.execute(
                """
                UPDATE automation_runs
                SET status = 'cancelled', current_stage = 'cancelled', stop_requested = 1,
                    error = ?, finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (reason, run_id),
            )
        else:
            conn.execute(
                "UPDATE automation_runs SET stop_requested = 1, error = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
                (reason, run_id),
            )
        batch_id = str(run["message_batch_id"] or "")
        traffic_run_id = str(run["traffic_run_id"] or "")
        traffic_runtime_job_id = str(run["traffic_runtime_job_id"] or "")
    if batch_id and not already_terminal:
        from app.services import message_workbench

        message_workbench.cancel_auto_message_batch(batch_id)
    if traffic_run_id and not already_terminal:
        from app.services import job_queue, traffic_workbench

        if traffic_runtime_job_id:
            try:
                job_queue.request_cancel(traffic_runtime_job_id, reason=reason)
            except (RuntimeError, ValueError):
                traffic_workbench.stop_run(traffic_run_id)
        else:
            traffic_workbench.stop_run(traffic_run_id)
    return get_run(run_id)


def run_automation_run(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    if run["status"] not in ACTIVE_RUN_STATUSES:
        return run
    with database.connect() as conn:
        conn.execute(
            """
            UPDATE automation_runs
            SET status = 'running', current_stage = 'starting', started_at = COALESCE(started_at, datetime('now', 'localtime')),
                error = '', updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (run_id,),
        )
    try:
        _ensure_plan_authorized(str(run["plan_type"]))
        if run["plan_type"] == "keyword_lead":
            _run_keyword_plan(run_id, KeywordLeadPlanConfig.model_validate(run["config_snapshot"]))
        elif run["plan_type"] == "message":
            _run_message_plan(run_id, MessagePlanConfig.model_validate(run["config_snapshot"]))
        else:
            _run_traffic_plan(run_id, TrafficAutomationPlanConfig.model_validate(run["config_snapshot"]))
    except _RunCancelled as exc:
        _finish_run(run_id, "cancelled", str(exc))
    except Exception as exc:
        _finish_run(run_id, "failed", str(exc))
    return get_run(run_id)


def _run_keyword_plan(run_id: str, config: KeywordLeadPlanConfig) -> None:
    with database.connect() as conn:
        items = conn.execute(
            "SELECT * FROM automation_run_items WHERE run_id = ? AND status IN ('pending', 'running') ORDER BY id",
            (run_id,),
        ).fetchall()
    for row in items:
        if _stop_requested(run_id):
            raise _RunCancelled("用户停止")
        try:
            _run_keyword_item(run_id, database.row_to_dict(row) or {}, config)
        except _RunCancelled:
            raise
        except Exception as exc:
            _finish_item(int(row["id"]), "failed", str(exc))
        _refresh_run_counts(run_id)
    with database.connect() as conn:
        counts = conn.execute(
            "SELECT SUM(status = 'succeeded') AS ok, SUM(status = 'failed') AS failed FROM automation_run_items WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    succeeded = int(counts["ok"] or 0)
    failed = int(counts["failed"] or 0)
    status = "completed" if failed == 0 else "partial" if succeeded else "failed"
    _finish_run(run_id, status, "" if status == "completed" else "部分关键词执行失败" if status == "partial" else "全部关键词执行失败")


def _run_keyword_item(run_id: str, item: dict[str, Any], config: KeywordLeadPlanConfig) -> None:
    item_id = int(item["id"])
    keyword = str(item["keyword"])
    context = _loads(item.get("context"), {})
    _set_item_stage(run_id, item_id, "competitor_discovery", context)
    discovery_id = str(context.get("discovery_task_id") or "")
    if not discovery_id:
        discovery = _create_discovery_task(keyword, config)
        discovery_id = str(discovery["id"])
        context["discovery_task_id"] = discovery_id
        _save_item_context(item_id, context)
    _wait_child_job(run_id, item_id, context, "discovery_runtime_job_id", lambda: _enqueue_crawl(discovery_id))

    _set_item_stage(run_id, item_id, "competitor_analysis", context)
    analysis_task_id = str(context.get("analysis_task_id") or "")
    if not analysis_task_id:
        from app.services import account_actions

        prepared = account_actions.create_task_account_analysis_task(
            discovery_id,
            limit=config.competitor_limit,
            automation_managed=True,
        )
        if prepared.get("task_ids"):
            analysis_task_id = str(prepared["task_ids"][0])
            context["analysis_task_id"] = analysis_task_id
            context["analysis_account_ids"] = [int(value["account_id"]) for value in prepared.get("accounts", [])]
            _save_item_context(item_id, context)
    if analysis_task_id:
        result = _wait_child_job(
            run_id,
            item_id,
            context,
            "analysis_runtime_job_id",
            lambda: _enqueue_account_analysis(context["analysis_account_ids"], analysis_task_id),
        )
        context["competitor_ai_job_ids"] = [str(value) for value in (result or {}).get("job_ids", [])]
        _save_item_context(item_id, context)
        ai_runtime = str((result or {}).get("ai_runtime_job", {}).get("id") or "")
        if ai_runtime:
            context["competitor_ai_runtime_job_id"] = ai_runtime
            _save_item_context(item_id, context)
            _wait_existing_child(run_id, ai_runtime)

    _set_item_stage(run_id, item_id, "comment_collection", context)
    task_ids = [str(value) for value in context.get("customer_task_ids", [])]
    if not task_ids:
        from app.services import account_actions

        prepared = account_actions.create_keyword_find_customer_task(
            config.platform,
            keyword,
            limit=config.competitor_limit,
            automation_managed=True,
            content_count=config.competitor_content_count,
            comment_count=config.comment_count,
            collect_sub_comments=config.collect_sub_comments,
        )
        task_ids = [str(value) for value in prepared.get("task_ids", [])]
        context["customer_task_ids"] = task_ids
        _save_item_context(item_id, context)
    if task_ids:
        _wait_child_job(run_id, item_id, context, "customer_runtime_job_id", lambda: _enqueue_crawl_batch(task_ids))

    if config.auto_analyze_leads and task_ids:
        _set_item_stage(run_id, item_id, "lead_analysis", context)
        lead_job_ids = [str(value) for value in context.get("lead_ai_job_ids", [])]
        if not lead_job_ids:
            from app.services import ai_service

            errors: list[dict[str, Any]] = []
            for task_id in task_ids:
                prepared = ai_service.prepare_auto_lead_analysis_jobs(task_id, auto_delete=False)
                lead_job_ids.extend(str(value) for value in prepared.get("job_ids", []))
                errors.extend(prepared.get("errors", []))
            if errors:
                context["lead_ai_prepare_errors"] = errors
            context["lead_ai_job_ids"] = list(dict.fromkeys(lead_job_ids))
            _save_item_context(item_id, context)
        if lead_job_ids:
            _wait_child_job(run_id, item_id, context, "lead_ai_runtime_job_id", lambda: _enqueue_ai(lead_job_ids, run_id, item_id))

    _finish_item(item_id, "succeeded", "")


def _run_message_plan(run_id: str, config: MessagePlanConfig) -> None:
    _validate_message_plan_state("message", config, True)
    with database.connect() as conn:
        run = conn.execute("SELECT * FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
        batch_id = str(run["message_batch_id"] or "") if run else ""
    if not batch_id:
        from app.services import message_workbench

        batch = message_workbench.create_scheduled_message_batch(run_id, config)
        batch_id = str(batch["id"])
        with database.connect() as conn:
            conn.execute(
                "UPDATE automation_runs SET message_batch_id = ?, total_count = ?, current_stage = 'message_sending', updated_at = datetime('now', 'localtime') WHERE id = ?",
                (batch_id, int(batch["total_count"]), run_id),
            )
    from app.services import job_queue

    runtime_job = job_queue.enqueue_message_batch(batch_id)
    _wait_existing_child(run_id, str(runtime_job["id"]))
    with database.connect() as conn:
        batch = conn.execute("SELECT * FROM message_batches WHERE id = ?", (batch_id,)).fetchone()
        if not batch:
            raise RuntimeError("自动私信批次不存在")
        conn.execute(
            """
            UPDATE automation_runs
            SET message_attempted_count = ?, message_success_count = ?, success_count = ?, failed_count = ?, skipped_count = ?
            WHERE id = ?
            """,
            (
                int(batch["success_count"] or 0) + int(batch["failed_count"] or 0),
                int(batch["success_count"] or 0),
                int(batch["success_count"] or 0),
                int(batch["failed_count"] or 0),
                int(batch["skipped_count"] or 0),
                run_id,
            ),
        )
        batch_status = str(batch["status"])
    if batch_status == "failed":
        raise RuntimeError(str(batch["error"] or "自动私信批次失败"))
    _finish_run(run_id, "completed", "" if batch_status == "succeeded" else "私信额度已用完，剩余客户留待下次计划")


def _run_traffic_plan(run_id: str, config: TrafficAutomationPlanConfig) -> None:
    from app.services import job_queue, traffic_workbench

    with database.connect() as conn:
        run = conn.execute("SELECT * FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
        if not run:
            raise RuntimeError("自动化运行记录不存在")
        traffic_run_id = str(run["traffic_run_id"] or "")
        runtime_job_id = str(run["traffic_runtime_job_id"] or "")
        plan_name = str(run["plan_name"] or "自动引流")

    if not traffic_run_id:
        _set_run_stage(run_id, "traffic_creating")
        traffic_run = traffic_workbench.create_automation_run(run_id, plan_name, config)
        traffic_run_id = str(traffic_run["id"])
        with database.connect() as conn:
            conn.execute(
                """
                UPDATE automation_runs
                SET traffic_run_id = ?, total_count = ?, current_stage = 'traffic_waiting',
                    updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (traffic_run_id, config.round_video_limit, run_id),
            )

    traffic_run = traffic_workbench.get_run(traffic_run_id)
    if not traffic_run:
        raise RuntimeError("关联的引流批次不存在")
    if str(traffic_run["status"]) in {"completed", "failed", "stopped"}:
        _sync_traffic_counts(run_id, traffic_run)
        if str(traffic_run["status"]) == "completed":
            _finish_run(run_id, "completed", "")
            return
        if _stop_requested(run_id) or str(traffic_run["status"]) == "stopped":
            raise _RunCancelled(str(traffic_run.get("stop_reason") or "用户停止"))
        raise RuntimeError(str(traffic_run.get("stop_reason") or "自动引流执行失败"))

    if runtime_job_id:
        try:
            job_queue.get_job(runtime_job_id)
        except ValueError:
            runtime_job_id = ""
    if not runtime_job_id:
        runtime_job = job_queue.enqueue_traffic_run(traffic_run_id)
        runtime_job_id = str(runtime_job["id"])
        with database.connect() as conn:
            conn.execute(
                "UPDATE automation_runs SET traffic_runtime_job_id = ?, current_stage = 'traffic_waiting', updated_at = datetime('now', 'localtime') WHERE id = ?",
                (runtime_job_id, run_id),
            )
    try:
        _wait_existing_child(run_id, runtime_job_id, running_stage="traffic_running")
    finally:
        traffic_run = traffic_workbench.get_run(traffic_run_id)
        if traffic_run:
            _sync_traffic_counts(run_id, traffic_run)
    if not traffic_run or str(traffic_run["status"]) != "completed":
        raise RuntimeError(str((traffic_run or {}).get("stop_reason") or "自动引流未正常完成"))
    _finish_run(run_id, "completed", "")


def _create_discovery_task(keyword: str, config: KeywordLeadPlanConfig) -> dict[str, Any]:
    from app.services import crawler_adapter

    with database.connect() as conn:
        login_type = database.get_setting(conn, "login_type", "qrcode")
        headless = database.get_setting(conn, "headless", "false") == "true"
    return crawler_adapter.create_task(
        TaskCreate(
            name=f"自动获客-采集竞品-{keyword}",
            mode="competitor_discovery",
            platform=config.platform,
            login_type=login_type if login_type in {"qrcode", "phone", "cookie"} else "qrcode",
            keywords=keyword,
            content_count=config.discovery_content_count,
            comment_count=0,
            collect_comments=False,
            collect_sub_comments=False,
            max_concurrency=1,
            headless=headless,
        ),
        automation_managed=True,
    )


def _wait_child_job(
    run_id: str,
    item_id: int,
    context: dict[str, Any],
    key: str,
    enqueue_callback: Any,
) -> dict[str, Any]:
    job_id = str(context.get(key) or "")
    if not job_id:
        job = enqueue_callback()
        job_id = str(job["id"])
        context[key] = job_id
        _save_item_context(item_id, context)
    return _wait_existing_child(run_id, job_id)


def _wait_existing_child(run_id: str, job_id: str, *, running_stage: str = "") -> dict[str, Any]:
    from app.services import job_queue

    stage_set = False
    while True:
        if _stop_requested(run_id):
            try:
                job_queue.request_cancel(job_id, reason="自动化计划已停止")
            except (RuntimeError, ValueError):
                pass
            raise _RunCancelled("用户停止")
        job = job_queue.get_job(job_id)
        status = str(job["status"])
        if running_stage and status == "running" and not stage_set:
            _set_run_stage(run_id, running_stage)
            stage_set = True
        if status in TERMINAL_JOB_STATUSES:
            if status != "succeeded":
                raise RuntimeError(str(job.get("error") or f"子任务 {job_id} 执行失败"))
            return dict(job.get("result") or {})
        time.sleep(0.5)


def _enqueue_crawl(task_id: str) -> dict[str, Any]:
    from app.services import job_queue

    return job_queue.enqueue_crawl_task(task_id)


def _enqueue_crawl_batch(task_ids: list[str]) -> dict[str, Any]:
    from app.services import job_queue

    return job_queue.enqueue_crawl_batch(task_ids)


def _enqueue_account_analysis(account_ids: list[int], task_id: str) -> dict[str, Any]:
    from app.services import job_queue

    return job_queue.enqueue_account_analysis(account_ids, task_id, auto_delete=False)


def _enqueue_ai(job_ids: list[str], run_id: str, item_id: int) -> dict[str, Any]:
    from app.services import job_queue

    return job_queue.enqueue_ai_batch(job_ids, entity_id=f"automation:{run_id}:{item_id}:lead-ai")


def _select_keywords(conn: Any, plan_id: str, config: KeywordLeadPlanConfig) -> list[str]:
    rows = conn.execute(
        """
        SELECT ari.keyword, MAX(ar.created_at) AS last_run_at
        FROM automation_run_items ari
        JOIN automation_runs ar ON ar.id = ari.run_id
        WHERE ar.plan_id = ? AND ari.status <> 'pending'
        GROUP BY ari.keyword
        """,
        (plan_id,),
    ).fetchall()
    last_run = {str(row["keyword"]): str(row["last_run_at"] or "") for row in rows}
    ordered = sorted(config.keywords, key=lambda value: (bool(last_run.get(value)), last_run.get(value, ""), config.keywords.index(value)))
    return ordered[: config.keyword_count]


def get_message_limits() -> dict[str, Any]:
    with database.connect() as conn:
        daily_raw = database.get_setting(conn, "message_daily_limit", "").strip()
        hourly_raw = database.get_setting(conn, "message_hourly_limit", "").strip()
        used_today = int(
            conn.execute("SELECT COUNT(*) AS c FROM message_send_attempts WHERE date(attempted_at) = date('now', 'localtime')").fetchone()["c"]
        )
        used_hour = int(
            conn.execute("SELECT COUNT(*) AS c FROM message_send_attempts WHERE attempted_at >= datetime('now', 'localtime', '-1 hour')").fetchone()["c"]
        )
        fill_only = database.get_setting(conn, "auto_dm_fill_only", "false") == "true"
    daily = int(daily_raw) if daily_raw else None
    hourly = int(hourly_raw) if hourly_raw else None
    return {
        "configured": daily is not None and hourly is not None,
        "daily_limit": daily,
        "hourly_limit": hourly,
        "used_today": used_today,
        "used_hour": used_hour,
        "remaining_today": max(0, daily - used_today) if daily is not None else None,
        "remaining_hour": max(0, hourly - used_hour) if hourly is not None else None,
        "fill_only": fill_only,
        "notice": "仅统计本软件产生的私信，无法感知抖音 App 或其他工具中的手动发送数量。",
    }


def update_message_limits(daily_limit: int, hourly_limit: int) -> dict[str, Any]:
    with database.connect() as conn:
        database.set_setting(conn, "message_daily_limit", str(daily_limit))
        database.set_setting(conn, "message_hourly_limit", str(hourly_limit))
    return get_message_limits()


def _validate_message_plan_state(plan_type: str, config: Any, enabled_or_run: bool) -> None:
    if plan_type != "message" or not enabled_or_run:
        return
    MessagePlanConfig.model_validate(config)
    limits = get_message_limits()
    if not limits["configured"]:
        raise ValueError("请先配置自动私信的每小时和每日额度")
    if limits["fill_only"]:
        raise ValueError("“自动私信只填内容不发送”已开启，不能启用或运行自动私信计划")


def _ensure_plan_authorized(plan_type: str) -> None:
    from app.services import license_service

    if plan_type == "traffic":
        license_service.ensure_authorized_for("traffic")
    else:
        license_service.ensure_authorized()


def license_scope(plan_type: str) -> str:
    return "traffic" if plan_type == "traffic" else "lead"


def _traffic_run_summary(traffic_run_id: str) -> dict[str, Any] | None:
    if not traffic_run_id:
        return None
    from app.services import traffic_workbench

    run = traffic_workbench.get_run(traffic_run_id)
    if not run:
        return None
    keys = (
        "id",
        "plan_id",
        "status",
        "total_videos",
        "browsed_count",
        "action_success_count",
        "skipped_count",
        "failed_count",
        "stop_reason",
        "stop_suggestion",
        "started_at",
        "finished_at",
        "created_at",
    )
    return {key: run.get(key) for key in keys}


def _sync_traffic_counts(run_id: str, traffic_run: dict[str, Any]) -> None:
    with database.connect() as conn:
        conn.execute(
            """
            UPDATE automation_runs
            SET total_count = ?, success_count = ?, failed_count = ?, skipped_count = ?,
                updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (
                int(traffic_run.get("total_videos") or 0),
                int(traffic_run.get("action_success_count") or 0),
                int(traffic_run.get("failed_count") or 0),
                int(traffic_run.get("skipped_count") or 0),
                run_id,
            ),
        )


def _format_plan(conn: Any, row: Any, now: datetime) -> dict[str, Any]:
    result = database.row_to_dict(row) or {}
    result["weekdays"] = _loads(result.get("weekdays"), [])
    result["config"] = _loads(result.get("config"), {})
    result["enabled"] = bool(result.get("enabled"))
    result["archived"] = bool(result.get("archived"))
    result["next_run_at"] = _next_run(result["weekdays"], str(result["run_time"]), now) if result["enabled"] else ""
    recent = conn.execute(
        "SELECT status, error, finished_at, created_at FROM automation_runs WHERE plan_id = ? ORDER BY created_at DESC LIMIT 1",
        (result["id"],),
    ).fetchone()
    result["recent_run"] = database.row_to_dict(recent) if recent else None
    return result


def _format_run(row: Any) -> dict[str, Any]:
    result = database.row_to_dict(row) or {}
    result["config_snapshot"] = _loads(result.get("config_snapshot"), {})
    result["stop_requested"] = bool(result.get("stop_requested"))
    return result


def _format_run_item(row: Any) -> dict[str, Any]:
    result = database.row_to_dict(row) or {}
    result["context"] = _loads(result.get("context"), {})
    return result


def _enrich_run_item(conn: Any, row: Any) -> dict[str, Any]:
    result = _format_run_item(row)
    context = result["context"]
    ai_job_ids = list(dict.fromkeys(
        [str(value) for value in context.get("competitor_ai_job_ids", [])]
        + [str(value) for value in context.get("lead_ai_job_ids", [])]
    ))
    if ai_job_ids:
        placeholders = ",".join("?" for _ in ai_job_ids)
        ai_rows = conn.execute(
            f"SELECT id, target_type, target_id, status, output_payload, error FROM analysis_jobs WHERE id IN ({placeholders}) ORDER BY created_at",
            ai_job_ids,
        ).fetchall()
        result["ai_results"] = [
            {
                **(database.row_to_dict(item) or {}),
                "output_payload": _loads(item["output_payload"], {}),
            }
            for item in ai_rows
        ]
    else:
        result["ai_results"] = []
    task_ids = [str(value) for value in context.get("customer_task_ids", [])]
    if task_ids:
        placeholders = ",".join("?" for _ in task_ids)
        result["lead_count"] = int(
            conn.execute(
                f"SELECT COUNT(DISTINCT lead_account_id) AS c FROM lead_sources WHERE task_id IN ({placeholders}) AND active = 1",
                task_ids,
            ).fetchone()["c"]
        )
    else:
        result["lead_count"] = 0
    return result


def _config_type(plan_type: str) -> type[KeywordLeadPlanConfig] | type[MessagePlanConfig] | type[TrafficAutomationPlanConfig]:
    return {
        "keyword_lead": KeywordLeadPlanConfig,
        "message": MessagePlanConfig,
        "traffic": TrafficAutomationPlanConfig,
    }[plan_type]


def _parse_config(plan_type: str, value: Any) -> KeywordLeadPlanConfig | MessagePlanConfig | TrafficAutomationPlanConfig:
    data = _loads(value, {})
    return _config_type(plan_type).model_validate(data)


def _next_run(weekdays: list[int], run_time: str, now: datetime) -> str:
    hour, minute = (int(value) for value in run_time.split(":"))
    for offset in range(8):
        day = now.date() + timedelta(days=offset)
        candidate = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
        if day.isoweekday() in weekdays and candidate > now:
            return candidate.strftime("%Y-%m-%d %H:%M:%S")
    return ""


def _set_run_stage(run_id: str, stage: str) -> None:
    with database.connect() as conn:
        conn.execute(
            "UPDATE automation_runs SET current_stage = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (stage, run_id),
        )


def _set_item_stage(run_id: str, item_id: int, stage: str, context: dict[str, Any]) -> None:
    with database.connect() as conn:
        conn.execute(
            "UPDATE automation_run_items SET status = 'running', current_stage = ?, context = ?, started_at = COALESCE(started_at, datetime('now', 'localtime')), updated_at = datetime('now', 'localtime') WHERE id = ?",
            (stage, json.dumps(context, ensure_ascii=False), item_id),
        )
        conn.execute("UPDATE automation_runs SET current_stage = ?, updated_at = datetime('now', 'localtime') WHERE id = ?", (stage, run_id))


def _save_item_context(item_id: int, context: dict[str, Any]) -> None:
    with database.connect() as conn:
        conn.execute(
            "UPDATE automation_run_items SET context = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (json.dumps(context, ensure_ascii=False), item_id),
        )


def _finish_item(item_id: int, status: str, error: str) -> None:
    with database.connect() as conn:
        conn.execute(
            "UPDATE automation_run_items SET status = ?, current_stage = ?, error = ?, finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime') WHERE id = ?",
            (status, status, error[:2000], item_id),
        )


def _refresh_run_counts(run_id: str) -> None:
    with database.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, SUM(status = 'succeeded') AS ok, SUM(status = 'failed') AS failed, SUM(status = 'skipped') AS skipped FROM automation_run_items WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if not int(row["total"] or 0):
            return
        conn.execute(
            "UPDATE automation_runs SET success_count = ?, failed_count = ?, skipped_count = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (int(row["ok"] or 0), int(row["failed"] or 0), int(row["skipped"] or 0), run_id),
        )


def _finish_run(run_id: str, status: str, error: str) -> None:
    _refresh_run_counts(run_id)
    with database.connect() as conn:
        conn.execute(
            "UPDATE automation_runs SET status = ?, current_stage = ?, error = ?, finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime') WHERE id = ?",
            (status, status, error[:2000], run_id),
        )


def _stop_requested(run_id: str) -> bool:
    with database.connect() as conn:
        row = conn.execute("SELECT stop_requested FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
    return bool(row and int(row["stop_requested"] or 0))


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


class _RunCancelled(RuntimeError):
    pass
