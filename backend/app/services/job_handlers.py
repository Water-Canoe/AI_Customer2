from __future__ import annotations

import asyncio
from typing import Any

from app import database


SAFE_RESUME_KINDS = {"ai_job", "ai_batch", "account_customer_intent", "automation_run", "voice_runtime_install"}
JOB_ENTITLEMENTS = {
    "crawl_task": "lead",
    "crawl_batch": "lead",
    "account_analysis": "lead",
    "keyword_account_analysis": "lead",
    "account_customer_intent": "lead",
    "message_batch": "lead",
    "message_single": "lead",
    "ai_job": "lead",
    "ai_batch": "lead",
    "traffic_run": "traffic",
    "video_generation": "content",
    "content_publish": "content",
    "voice_runtime_install": "content",
}


def can_resume(kind: str) -> bool:
    return kind in SAFE_RESUME_KINDS


# 这里只负责任务类型到领域服务的编排；队列核心不再导入业务模块。
def execute_job(job: dict[str, Any]) -> Any:
    from app.services import account_actions, ai_service, automation_workbench, content_publish, content_workbench, crawler_adapter, job_queue, license_service, message_workbench, traffic_workbench, voice_runtime

    kind = str(job["kind"])
    payload = dict(job["payload"])
    if entitlement := JOB_ENTITLEMENTS.get(kind):
        license_service.ensure_authorized_for(entitlement)
    elif kind in {"account_login", "account_check"}:
        license_service.ensure_authorized()
    if kind == "crawl_task":
        return crawler_adapter.run_task(str(payload["task_id"]))
    if kind == "crawl_batch":
        return crawler_adapter.run_tasks_serially([str(value) for value in payload.get("task_ids", [])])
    if kind in {"account_analysis", "keyword_account_analysis"}:
        account_ids = [int(value) for value in payload.get("account_ids", [])]
        task_id = str(payload["task_id"])
        if payload.get("auto_delete") is None:
            prepared = account_actions.prepare_account_analysis_jobs(account_ids, task_id)
        else:
            prepared = account_actions.prepare_account_analysis_jobs(
                account_ids,
                task_id,
                auto_delete=bool(payload["auto_delete"]),
            )
        job_ids = [str(value) for value in prepared.get("job_ids", [])]
        if job_ids:
            prepared["ai_runtime_job"] = job_queue.enqueue_ai_batch(job_ids, entity_id=f"account-analysis:{task_id}")
        return prepared
    if kind == "account_customer_intent":
        return account_actions.run_account_customer_intent_jobs([str(value) for value in payload.get("job_ids", [])])
    if kind == "traffic_run":
        return traffic_workbench.run_traffic_run(str(payload["run_id"]))
    if kind == "message_batch":
        return asyncio.run(message_workbench.run_auto_message_batch(str(payload["batch_id"])))
    if kind == "message_single":
        return asyncio.run(
            message_workbench.auto_message_customer(
                int(payload["lead_id"]),
                dry_run=bool(payload.get("dry_run")),
                timeout_seconds=int(payload.get("timeout_seconds") or 0),
                message_script=str(payload.get("message_script") or ""),
                script_label=str(payload.get("script_label") or "AI话术"),
                account_id=str(payload.get("account_id") or ""),
            )
        )
    if kind == "automation_run":
        return automation_workbench.run_automation_run(str(payload["run_id"]))
    if kind == "ai_job":
        return ai_service.run_ai_job(str(payload["job_id"]))
    if kind == "ai_batch":
        return ai_service.run_ai_jobs_parallel([str(value) for value in payload.get("job_ids", [])])
    if kind == "video_generation":
        return content_workbench.run_video_job(str(payload["video_job_id"]))
    if kind == "account_login":
        job_id = str(job["id"])
        return content_publish.run_account_login(
            str(payload["account_id"]),
            str(payload.get("login_kind") or "creator"),
            cancel_check=lambda: job_queue.cancel_requested(job_id),
        )
    if kind == "account_check":
        job_id = str(job["id"])
        return content_publish.run_account_check(
            str(payload["account_id"]),
            str(payload.get("login_kind") or "creator"),
            cancel_check=lambda: job_queue.cancel_requested(job_id),
        )
    if kind == "content_publish":
        return content_publish.run_publish_task(str(payload["publish_task_id"]))
    if kind == "voice_runtime_install":
        job_id = str(job["id"])
        return voice_runtime.install(
            progress=lambda value: job_queue.update_job_result(job_id, value),
            cancelled=lambda: job_queue.cancel_requested(job_id),
        )
    raise ValueError(f"不支持的运行任务类型：{kind}")


def mark_domain_failed(job: dict[str, Any], error: str) -> None:
    from app.services import content_publish, content_workbench

    kind = str(job.get("kind") or "")
    payload = dict(job.get("payload") or {})
    if kind == "video_generation":
        content_workbench.mark_video_job_failed(str(payload.get("video_job_id") or ""), error)
    elif kind == "content_publish":
        task_id = str(payload.get("publish_task_id") or "")
        task = content_publish.get_task(task_id)
        if task["status"] not in content_publish.TERMINAL_TASK_STATUSES:
            content_publish.update_task_state(task_id, "failed", error=error)


def domain_outcome(job: dict[str, Any], result: Any = None) -> dict[str, str]:
    kind = str(job["kind"])
    payload = dict(job["payload"])
    if kind == "crawl_batch":
        return _multi_domain_outcome("crawl_jobs", [str(value) for value in payload.get("task_ids", [])], {"succeeded"})
    if kind in {"ai_batch", "account_customer_intent"}:
        summary = result if isinstance(result, dict) else {}
        failed = int(summary.get("failed") or 0)
        succeeded = int(summary.get("succeeded") or 0)
        # 批量分析允许少量单项失败，明细错误仍保留在批次结果中供人工重试。
        if failed > 0 and succeeded == 0:
            errors = summary.get("errors") if isinstance(summary.get("errors"), list) else []
            detail = "；".join(str(item.get("reason") or "") for item in errors if isinstance(item, dict))
            return {"status": "failed", "error": detail or "AI 任务全部执行失败"}
        return {"status": "succeeded", "error": ""}
    table = ""
    entity_id = ""
    error_column = "error"
    success: set[str] = set()
    cancelled: set[str] = {"cancelled", "stopped"}
    if kind == "crawl_task":
        table, entity_id, success = "crawl_jobs", str(payload["task_id"]), {"succeeded"}
    elif kind in {"account_analysis", "keyword_account_analysis"}:
        table, entity_id, success = "crawl_jobs", str(payload["task_id"]), {"succeeded"}
    elif kind == "traffic_run":
        table, entity_id, success = "traffic_runs", str(payload["run_id"]), {"completed"}
        error_column = "stop_reason"
    elif kind == "message_batch":
        table, entity_id, success = "message_batches", str(payload["batch_id"]), {"succeeded", "quota_reached"}
    elif kind == "automation_run":
        table, entity_id, success = "automation_runs", str(payload["run_id"]), {"completed", "partial"}
    elif kind == "ai_job":
        table, entity_id, success = "analysis_jobs", str(payload["job_id"]), {"succeeded"}
    elif kind == "video_generation":
        table, entity_id, success = "video_jobs", str(payload["video_job_id"]), {"succeeded"}
    elif kind == "content_publish":
        table, entity_id, success = "publish_tasks", str(payload["publish_task_id"]), {"succeeded"}
    if not table:
        return {"status": "succeeded", "error": ""}
    with database.connect() as conn:
        row = conn.execute(
            f"SELECT status, COALESCE({error_column}, '') AS error FROM {table} WHERE id = ?",
            (entity_id,),
        ).fetchone()
    if not row:
        return {"status": "failed", "error": "业务任务记录不存在"}
    status = str(row["status"])
    if status in success:
        return {"status": "succeeded", "error": ""}
    if status in cancelled:
        return {"status": "cancelled", "error": str(row["error"] or "")}
    return {"status": "failed", "error": str(row["error"] or f"业务任务状态为 {status}")}


def _multi_domain_outcome(table: str, entity_ids: list[str], success: set[str]) -> dict[str, str]:
    if not entity_ids:
        return {"status": "failed", "error": "批量任务没有业务记录"}
    placeholders = ",".join("?" for _ in entity_ids)
    with database.connect() as conn:
        rows = conn.execute(
            f"SELECT id, status, COALESCE(error, '') AS error FROM {table} WHERE id IN ({placeholders})",
            entity_ids,
        ).fetchall()
    if len(rows) != len(set(entity_ids)):
        return {"status": "failed", "error": "部分业务任务记录不存在"}
    statuses = {str(row["status"]) for row in rows}
    if statuses.issubset(success):
        return {"status": "succeeded", "error": ""}
    if statuses.issubset(success | {"cancelled", "stopped"}) and statuses & {"cancelled", "stopped"}:
        return {"status": "cancelled", "error": "批量任务已取消"}
    errors = [str(row["error"]) for row in rows if str(row["error"])]
    return {"status": "failed", "error": "；".join(errors) or f"批量任务状态异常：{', '.join(sorted(statuses))}"}


def cancel_domain_job(job: dict[str, Any], reason: str = "用户取消", *, queued: bool = False) -> None:
    from app.services import account_center, automation_workbench, content_publish, content_workbench, crawler_adapter, message_workbench, traffic_workbench

    kind = str(job["kind"])
    payload = dict(job["payload"])
    try:
        if kind == "crawl_task":
            crawler_adapter.cancel_task(str(payload["task_id"]))
        elif kind == "crawl_batch":
            for task_id in payload.get("task_ids", []):
                crawler_adapter.cancel_task(str(task_id))
        elif kind in {"account_analysis", "keyword_account_analysis"}:
            crawler_adapter.cancel_task(str(payload["task_id"]))
        elif kind == "traffic_run":
            traffic_workbench.stop_run(str(payload["run_id"]))
        elif kind == "message_batch":
            message_workbench.cancel_auto_message_batch(str(payload["batch_id"]))
        elif kind == "automation_run":
            automation_workbench.cancel_run(str(payload["run_id"]), reason=reason)
        elif kind == "video_generation":
            content_workbench.mark_video_job_cancelled(str(payload["video_job_id"]), reason)
        elif kind == "content_publish" and queued:
            content_publish.update_task_state(str(payload["publish_task_id"]), "cancelled", error=reason)
        elif kind in {"account_login", "account_check"}:
            account_center.set_login_status(
                str(payload["account_id"]),
                str(payload.get("login_kind") or "creator"),
                "expired",
                reason,
                checked=True,
            )
        elif queued and kind in SAFE_RESUME_KINDS:
            job_ids = [str(payload["job_id"])] if kind == "ai_job" else [str(value) for value in payload.get("job_ids", [])]
            if job_ids:
                placeholders = ",".join("?" for _ in job_ids)
                with database.connect() as conn:
                    conn.execute(
                        f"UPDATE analysis_jobs SET status = 'failed', error = ?, updated_at = datetime('now', 'localtime') WHERE id IN ({placeholders}) AND status = 'pending'",
                        [reason, *job_ids],
                    )
    except (RuntimeError, ValueError):
        return


def prepare_domain_retry(job: dict[str, Any]) -> None:
    kind = str(job["kind"])
    payload = dict(job["payload"])
    with database.connect() as conn:
        if kind == "crawl_task":
            _reset_crawl_task(conn, str(payload["task_id"]))
        elif kind == "crawl_batch":
            for task_id in payload.get("task_ids", []):
                _reset_crawl_task(conn, str(task_id))
        elif kind in {"account_analysis", "keyword_account_analysis"}:
            _reset_crawl_task(conn, str(payload["task_id"]))
        elif kind == "traffic_run":
            conn.execute(
                """
                UPDATE traffic_runs
                SET status = 'queued', stop_requested = 0, stop_reason = '', stop_suggestion = '',
                    started_at = NULL, finished_at = NULL, updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (str(payload["run_id"]),),
            )
        elif kind == "ai_job":
            conn.execute("UPDATE analysis_jobs SET status = 'pending', error = '', updated_at = datetime('now', 'localtime') WHERE id = ?", (str(payload["job_id"]),))
        elif kind in {"ai_batch", "account_customer_intent"}:
            job_ids = [str(value) for value in payload.get("job_ids", [])]
            if job_ids:
                placeholders = ",".join("?" for _ in job_ids)
                conn.execute(f"UPDATE analysis_jobs SET status = 'pending', error = '', updated_at = datetime('now', 'localtime') WHERE id IN ({placeholders})", job_ids)
        elif kind == "video_generation":
            from app.services import content_workbench

            content_workbench.reset_video_job_for_retry(conn, str(payload["video_job_id"]))
        elif kind == "content_publish":
            conn.execute(
                "UPDATE publish_tasks SET status = 'queued', current_stage = 'queued', progress = 0, error = '', result = '{}', started_at = NULL, finished_at = NULL, published_at = NULL, updated_at = datetime('now', 'localtime') WHERE id = ?",
                (str(payload["publish_task_id"]),),
            )


def _reset_crawl_task(conn: Any, task_id: str) -> None:
    conn.execute(
        """
        UPDATE crawl_jobs
        SET status = 'pending', process_id = NULL, error = '', started_at = NULL, finished_at = NULL,
            updated_at = datetime('now', 'localtime')
        WHERE id = ?
        """,
        (task_id,),
    )


def normalize_resumable_domain(conn: Any, job: dict[str, Any]) -> None:
    payload = dict(job["payload"])
    if job["kind"] == "automation_run":
        conn.execute(
            "UPDATE automation_runs SET status = 'queued', error = '本地服务关闭，等待恢复', updated_at = datetime('now', 'localtime') WHERE id = ? AND status = 'running'",
            (str(payload["run_id"]),),
        )
        return
    job_ids = [str(payload["job_id"])] if job["kind"] == "ai_job" else [str(value) for value in payload.get("job_ids", [])]
    if not job_ids:
        return
    placeholders = ",".join("?" for _ in job_ids)
    conn.execute(
        f"UPDATE analysis_jobs SET status = 'pending', error = '本地服务关闭，等待恢复', updated_at = datetime('now', 'localtime') WHERE id IN ({placeholders}) AND status = 'running'",
        job_ids,
    )


def normalize_interrupted_domain(conn: Any, job: dict[str, Any], reason: str) -> None:
    kind = str(job["kind"])
    payload = dict(job["payload"])
    if kind == "crawl_task":
        task_ids = [str(payload["task_id"])]
    elif kind == "crawl_batch":
        task_ids = [str(value) for value in payload.get("task_ids", [])]
    elif kind in {"account_analysis", "keyword_account_analysis"}:
        task_ids = [str(payload["task_id"])]
    else:
        task_ids = []
    if task_ids:
        placeholders = ",".join("?" for _ in task_ids)
        conn.execute(
            f"UPDATE crawl_jobs SET status = 'failed', process_id = NULL, error = ?, finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime') WHERE id IN ({placeholders}) AND status IN ('pending', 'running')",
            [reason, *task_ids],
        )
    if kind == "traffic_run":
        conn.execute(
            "UPDATE traffic_runs SET status = 'stopped', stop_requested = 0, stop_reason = ?, stop_suggestion = '确认登录状态后可重新启动计划。', finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime') WHERE id = ? AND status = 'running'",
            (reason, str(payload["run_id"])),
        )
    if kind == "message_batch":
        batch_id = str(payload["batch_id"])
        conn.execute(
            "UPDATE message_batches SET status = 'failed', stop_requested = 0, error = ?, finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime') WHERE id = ? AND status = 'running'",
            (reason, batch_id),
        )
        conn.execute(
            "UPDATE message_batch_items SET status = 'failed', error = ?, finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime') WHERE batch_id = ? AND status = 'running'",
            (reason, batch_id),
        )
    if kind == "video_generation":
        conn.execute(
            """
            UPDATE video_jobs
            SET status = 'interrupted', current_stage = 'interrupted', error = ?,
                finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE id = ? AND status = 'running'
            """,
            (reason, str(payload["video_job_id"])),
        )
    if kind == "content_publish":
        conn.execute(
            """
            UPDATE publish_tasks
            SET status = CASE WHEN current_stage = 'publishing' THEN 'review_required' ELSE 'failed' END,
                current_stage = CASE WHEN current_stage = 'publishing' THEN 'review_required' ELSE 'failed' END,
                error = ?, finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE id = ? AND status = 'running'
            """,
            (reason, str(payload["publish_task_id"])),
        )
    if kind in {"account_login", "account_check"}:
        login_kind = str(payload.get("login_kind") or "creator")
        if login_kind == "creator":
            conn.execute(
                "UPDATE publish_accounts SET status = 'error', last_error = ?, qrcode_relative_path = '', updated_at = datetime('now', 'localtime') WHERE id = ? AND status = 'checking'",
                (reason, str(payload["account_id"])),
            )
        features = ("publish",) if login_kind == "creator" else ("acquisition", "message", "traffic")
        placeholders = ",".join("?" for _ in features)
        conn.execute(
            f"UPDATE account_feature_bindings SET status = 'error', last_error = ?, last_checked_at = datetime('now', 'localtime') WHERE account_id = ? AND feature IN ({placeholders}) AND status = 'checking'",
            (reason, str(payload["account_id"]), *features),
        )
