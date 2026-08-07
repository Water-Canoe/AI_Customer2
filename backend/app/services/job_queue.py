from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from app import database


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "interrupted"}
RESOURCE_LIMITS = {"browser": 1, "ai": 1, "video": 1, "automation": 1, "default": 2}
HEARTBEAT_SECONDS = 3.0
POLL_SECONDS = 0.5


@dataclass
class ActiveJob:
    job_id: str
    resource: str
    thread: threading.Thread


_STATE_LOCK = threading.RLock()
_WAKE = threading.Event()
_STOP = threading.Event()
_DISPATCHER: threading.Thread | None = None
_ACTIVE: dict[str, ActiveJob] = {}
_LAST_HEARTBEAT = 0.0
_HANDLERS: Any = None


def configure_handlers(handlers: Any) -> None:
    global _HANDLERS
    _HANDLERS = handlers


def _handlers() -> Any:
    if _HANDLERS is None:
        raise RuntimeError("运行队列尚未配置业务处理器")
    return _HANDLERS


def start() -> dict[str, int]:
    global _DISPATCHER
    _handlers()
    with _STATE_LOCK:
        if _DISPATCHER and _DISPATCHER.is_alive():
            return {"recovered": 0, "resumed": 0}
        recovery = recover_interrupted_jobs()
        _STOP.clear()
        _WAKE.clear()
        _DISPATCHER = threading.Thread(target=_dispatch_loop, name="ai-customer-job-dispatcher", daemon=True)
        _DISPATCHER.start()
        return recovery


def shutdown(timeout_seconds: float = 12.0) -> dict[str, int]:
    _STOP.set()
    _WAKE.set()
    with _STATE_LOCK:
        active_ids = list(_ACTIVE)
    active_jobs = []
    for job_id in active_ids:
        try:
            job = get_job(job_id)
        except ValueError:
            continue
        active_jobs.append(job)
        if not _can_resume(str(job["kind"])):
            try:
                request_cancel(job_id, reason="本地服务正在关闭")
            except (RuntimeError, ValueError):
                continue

    deadline = time.monotonic() + max(0.0, timeout_seconds)
    dispatcher = _DISPATCHER
    if dispatcher and dispatcher.is_alive():
        dispatcher.join(timeout=max(0.0, min(2.0, deadline - time.monotonic())))
    while time.monotonic() < deadline:
        with _STATE_LOCK:
            active = list(_ACTIVE.values())
        if not active:
            break
        for item in active:
            item.thread.join(timeout=0.2)

    with _STATE_LOCK:
        remaining_ids = list(_ACTIVE)
    interrupted = 0
    resumed = 0
    if remaining_ids:
        with database.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM runtime_jobs WHERE id IN ({','.join('?' for _ in remaining_ids)}) AND status = 'running'",
                remaining_ids,
            ).fetchall()
            for row in rows:
                job = _format_job(row)
                if _can_resume(str(job["kind"])) and int(job["attempt"]) < int(job["max_attempts"]):
                    conn.execute(
                        """
                        UPDATE runtime_jobs
                        SET status = 'queued', cancel_requested = 0, lease_token = '', heartbeat_at = NULL,
                            error = '本地服务关闭，任务将在下次启动时继续', started_at = NULL,
                            updated_at = datetime('now', 'localtime')
                        WHERE id = ? AND status = 'running'
                        """,
                        (job["id"],),
                    )
                    _normalize_resumable_domain(conn, job)
                    resumed += 1
                else:
                    conn.execute(
                        """
                        UPDATE runtime_jobs
                        SET status = 'interrupted', lease_token = '', error = '本地服务关闭时任务未能在限时内结束',
                            finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
                        WHERE id = ? AND status = 'running'
                        """,
                        (job["id"],),
                    )
                    _normalize_interrupted_domain(conn, job, "本地服务关闭时自动化任务被中断")
                    interrupted += 1
    return {"requested": len(active_jobs), "interrupted": interrupted, "resumed": resumed}


def enqueue(
    kind: str,
    *,
    entity_id: str = "",
    payload: dict[str, Any] | None = None,
    resource: str = "default",
    priority: int = 0,
    max_attempts: int = 1,
) -> dict[str, Any]:
    if resource not in RESOURCE_LIMITS:
        raise ValueError(f"未知任务资源：{resource}")
    clean_kind = str(kind or "").strip()
    clean_entity_id = str(entity_id or "").strip()
    if not clean_kind:
        raise ValueError("任务类型不能为空")

    with database.connect() as conn:
        if clean_entity_id:
            existing = conn.execute(
                """
                SELECT * FROM runtime_jobs
                WHERE kind = ? AND entity_id = ? AND status IN ('queued', 'running')
                ORDER BY created_at DESC LIMIT 1
                """,
                (clean_kind, clean_entity_id),
            ).fetchone()
            if existing:
                return _format_job(existing)
        job_id = uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO runtime_jobs(
                id, kind, entity_id, resource, payload, priority, max_attempts
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                clean_kind,
                clean_entity_id,
                resource,
                json.dumps(payload or {}, ensure_ascii=False),
                int(priority),
                max(1, int(max_attempts)),
            ),
        )
    _WAKE.set()
    return get_job(job_id)


def enqueue_crawl_task(task_id: str) -> dict[str, Any]:
    return enqueue("crawl_task", entity_id=task_id, payload={"task_id": task_id}, resource="browser")


def enqueue_crawl_batch(task_ids: list[str]) -> dict[str, Any]:
    clean_ids = [str(task_id) for task_id in task_ids if str(task_id).strip()]
    return enqueue(
        "crawl_batch",
        entity_id=":".join(clean_ids),
        payload={"task_ids": clean_ids},
        resource="browser",
    )


def enqueue_account_analysis(
    account_ids: list[int],
    task_id: str,
    kind: str = "account_analysis",
    *,
    auto_delete: bool | None = None,
) -> dict[str, Any]:
    return enqueue(
        kind,
        entity_id=task_id,
        payload={"account_ids": [int(value) for value in account_ids], "task_id": task_id, "auto_delete": auto_delete},
        resource="browser",
    )


def enqueue_ai_batch(job_ids: list[str], entity_id: str = "") -> dict[str, Any]:
    clean_ids = [str(job_id) for job_id in job_ids if str(job_id).strip()]
    identity = entity_id or ":".join(clean_ids)
    return enqueue("ai_batch", entity_id=identity, payload={"job_ids": clean_ids}, resource="ai", max_attempts=2)


def enqueue_ai_job(job_id: str) -> dict[str, Any]:
    return enqueue("ai_job", entity_id=job_id, payload={"job_id": job_id}, resource="ai", max_attempts=2)


def enqueue_traffic_run(run_id: str) -> dict[str, Any]:
    return enqueue("traffic_run", entity_id=run_id, payload={"run_id": run_id}, resource="browser")


def enqueue_message_batch(batch_id: str) -> dict[str, Any]:
    return enqueue("message_batch", entity_id=batch_id, payload={"batch_id": batch_id}, resource="browser")


def enqueue_single_message(
    lead_id: int,
    *,
    dry_run: bool,
    timeout_seconds: int,
    message_script: str,
    script_label: str,
    account_id: str,
) -> dict[str, Any]:
    return enqueue(
        "message_single",
        entity_id=f"lead:{lead_id}",
        payload={
            "lead_id": int(lead_id),
            "dry_run": bool(dry_run),
            "timeout_seconds": int(timeout_seconds),
            "message_script": message_script,
            "script_label": script_label,
            "account_id": account_id,
        },
        resource="browser",
    )


def enqueue_automation_run(run_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute(
            "SELECT p.sort_order FROM automation_runs r JOIN automation_plans p ON p.id = r.plan_id WHERE r.id = ?",
            (run_id,),
        ).fetchone()
    return enqueue(
        "automation_run",
        entity_id=str(run_id),
        payload={"run_id": str(run_id)},
        resource="automation",
        priority=-int(row["sort_order"] or 0) if row else 0,
        max_attempts=20,
    )


def enqueue_video_job(video_job_id: str) -> dict[str, Any]:
    return enqueue(
        "video_generation",
        entity_id=str(video_job_id),
        payload={"video_job_id": str(video_job_id)},
        resource="video",
    )


def enqueue_voice_runtime_install() -> dict[str, Any]:
    return enqueue(
        "voice_runtime_install",
        entity_id="voxcpm2",
        resource="default",
        priority=100,
        max_attempts=3,
    )


def enqueue_account_job(account_id: str, action: str, login_kind: str = "user") -> dict[str, Any]:
    if action not in {"login", "check"}:
        raise ValueError("账号任务只支持登录或检查")
    if login_kind not in {"user", "creator"}:
        raise ValueError("未知登录类型")
    return enqueue(
        f"account_{action}",
        entity_id=f"{account_id}:{login_kind}",
        payload={"account_id": str(account_id), "login_kind": login_kind},
        resource="browser",
        priority=100,
    )


def enqueue_publish_task(task_id: str) -> dict[str, Any]:
    return enqueue(
        "content_publish",
        entity_id=str(task_id),
        payload={"publish_task_id": str(task_id)},
        resource="browser",
        priority=50,
        max_attempts=100,
    )


def get_job(job_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM runtime_jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise ValueError("运行任务不存在")
    return _format_job(row)


def update_job_result(job_id: str, result: dict[str, Any]) -> None:
    """Persist live progress for long-running runtime jobs."""
    with database.connect() as conn:
        conn.execute(
            "UPDATE runtime_jobs SET result = ?, updated_at = datetime('now', 'localtime') WHERE id = ? AND status = 'running'",
            (json.dumps(_json_safe(result), ensure_ascii=False), job_id),
        )


def list_jobs(
    *,
    status: str = "",
    kind: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    conditions: list[str] = []
    params: list[Any] = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if kind:
        conditions.append("kind = ?")
        params.append(kind)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    safe_page_size = max(1, min(int(page_size), 200))
    safe_page = max(1, int(page))
    with database.connect() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) AS c FROM runtime_jobs {where}", params).fetchone()["c"])
        rows = conn.execute(
            f"""
            SELECT * FROM runtime_jobs
            {where}
            ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                     priority DESC, created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, safe_page_size, (safe_page - 1) * safe_page_size],
        ).fetchall()
    return {
        "items": [_format_job(row) for row in rows],
        "total": total,
        "page": safe_page,
        "page_size": safe_page_size,
        "active": active_summary(),
    }


def request_cancel(job_id: str, reason: str = "用户取消") -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM runtime_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise ValueError("运行任务不存在")
        status = str(row["status"])
        if status in TERMINAL_STATUSES:
            return _format_job(row)
        if status == "queued":
            conn.execute(
                """
                UPDATE runtime_jobs
                SET status = 'cancelled', cancel_requested = 1, error = ?,
                    finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (reason, job_id),
            )
        else:
            conn.execute(
                "UPDATE runtime_jobs SET cancel_requested = 1, error = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
                (reason, job_id),
            )
        job = _format_job(row)
    if status in {"queued", "running"}:
        _cancel_domain_job(job, reason, queued=status == "queued")
    _WAKE.set()
    return get_job(job_id)


def cancel_by_entity(kind: str, entity_id: str, reason: str = "用户取消") -> list[dict[str, Any]]:
    with database.connect() as conn:
        rows = conn.execute(
            "SELECT id FROM runtime_jobs WHERE kind = ? AND entity_id = ? AND status IN ('queued', 'running')",
            (kind, str(entity_id)),
        ).fetchall()
    return [request_cancel(str(row["id"]), reason) for row in rows]


def is_entity_cancel_requested(kind: str, entity_id: str) -> bool:
    with database.connect() as conn:
        row = conn.execute(
            """
            SELECT cancel_requested
            FROM runtime_jobs
            WHERE kind = ? AND entity_id = ? AND status = 'running'
            ORDER BY created_at DESC LIMIT 1
            """,
            (str(kind), str(entity_id)),
        ).fetchone()
    return bool(row and int(row["cancel_requested"] or 0))


def cancel_jobs_for_domain_id(domain_id: str, reason: str = "用户取消") -> list[dict[str, Any]]:
    target = str(domain_id)
    with database.connect() as conn:
        rows = conn.execute("SELECT * FROM runtime_jobs WHERE status IN ('queued', 'running')").fetchall()
    matched: list[str] = []
    for row in rows:
        job = _format_job(row)
        if str(job["entity_id"]) == target or target in _payload_identifiers(dict(job["payload"])):
            matched.append(str(job["id"]))
    return [request_cancel(job_id, reason) for job_id in matched]


def retry_job(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job["status"] not in TERMINAL_STATUSES:
        raise ValueError("只有已结束或已中断的运行任务可以重试")
    if job["kind"] == "message_batch":
        raise ValueError("自动私信批次请使用业务页面的“重试失败项”")
    if job["kind"] == "automation_run":
        raise ValueError("自动化计划请从计划页面重新运行")
    _prepare_domain_retry(job)
    return enqueue(
        str(job["kind"]),
        entity_id=str(job["entity_id"]),
        payload=dict(job["payload"]),
        resource=str(job["resource"]),
        priority=int(job["priority"]),
        max_attempts=int(job["max_attempts"]),
    )


def delete_job(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job["status"] not in TERMINAL_STATUSES:
        raise ValueError("运行中或排队中的任务不能删除，请先取消")
    with database.connect() as conn:
        conn.execute("DELETE FROM runtime_jobs WHERE id = ?", (job_id,))
    return {"ok": True, "id": job_id}


def active_summary() -> dict[str, int]:
    with _STATE_LOCK:
        summary = {resource: 0 for resource in RESOURCE_LIMITS}
        for item in _ACTIVE.values():
            summary[item.resource] = summary.get(item.resource, 0) + 1
    return summary


def recover_interrupted_jobs() -> dict[str, int]:
    recovered = 0
    resumed = 0
    reason = "检测到本地服务上次在任务执行中关闭"
    with database.connect() as conn:
        conn.execute(
            """
            UPDATE crawl_jobs
            SET status = 'failed', process_id = NULL, error = ?,
                finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE status = 'running'
            """,
            (f"{reason}，请确认登录状态后重试",),
        )
        conn.execute(
            """
            UPDATE traffic_runs
            SET status = 'stopped', stop_requested = 0,
                stop_reason = ?, stop_suggestion = '可以重新启动计划；防重复账本会避免重复互动。',
                finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE status = 'running'
            """,
            (reason,),
        )
        conn.execute(
            """
            UPDATE message_batches
            SET status = 'failed', error = ?, stop_requested = 0,
                finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE status = 'running'
            """,
            (f"{reason}，可使用“重试失败项”继续",),
        )
        conn.execute(
            """
            UPDATE message_batch_items
            SET status = 'failed', error = ?, finished_at = datetime('now', 'localtime'),
                updated_at = datetime('now', 'localtime')
            WHERE status = 'running'
            """,
            (reason,),
        )
        conn.execute(
            "UPDATE analysis_jobs SET status = 'pending', error = ?, updated_at = datetime('now', 'localtime') WHERE status = 'running'",
            (f"{reason}，已返回待执行队列",),
        )
        conn.execute(
            """
            UPDATE video_jobs
            SET status = 'interrupted', current_stage = 'interrupted', error = ?,
                finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE status = 'running'
            """,
            (f"{reason}，可在生成记录中重试",),
        )

        rows = conn.execute("SELECT * FROM runtime_jobs WHERE status = 'running'").fetchall()
        for row in rows:
            kind = str(row["kind"])
            if _can_resume(kind) and int(row["attempt"] or 0) < int(row["max_attempts"] or 1):
                conn.execute(
                    """
                    UPDATE runtime_jobs
                    SET status = 'queued', lease_token = '', heartbeat_at = NULL,
                        error = ?, started_at = NULL, updated_at = datetime('now', 'localtime')
                    WHERE id = ?
                    """,
                    (f"{reason}，已自动恢复排队", row["id"]),
                )
                resumed += 1
            else:
                conn.execute(
                    """
                    UPDATE runtime_jobs
                    SET status = 'interrupted', lease_token = '', error = ?,
                        finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
                    WHERE id = ?
                    """,
                    (f"{reason}，需要人工确认后重试", row["id"]),
                )
                recovered += 1
        conn.execute(
            """
            UPDATE runtime_jobs
            SET status = 'cancelled', error = CASE WHEN error = '' THEN '任务在排队时已取消' ELSE error END,
                finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE status = 'queued' AND cancel_requested = 1
            """
        )
    return {"recovered": recovered, "resumed": resumed}


def _dispatch_loop() -> None:
    global _LAST_HEARTBEAT
    while not _STOP.is_set():
        _prune_active()
        now = time.monotonic()
        if now - _LAST_HEARTBEAT >= HEARTBEAT_SECONDS:
            _heartbeat_active()
            _LAST_HEARTBEAT = now
        started = False
        while not _STOP.is_set():
            job = _claim_next_job()
            if not job:
                break
            thread = threading.Thread(target=_run_job, args=(job,), name=f"runtime-job-{job['id'][:8]}", daemon=True)
            with _STATE_LOCK:
                _ACTIVE[str(job["id"])] = ActiveJob(str(job["id"]), str(job["resource"]), thread)
            thread.start()
            started = True
        if not started:
            _WAKE.wait(POLL_SECONDS)
            _WAKE.clear()


def _claim_next_job() -> dict[str, Any] | None:
    from app.services import profile_manager

    with _STATE_LOCK:
        usage = active_summary()
    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM runtime_jobs
            WHERE status = 'queued' AND cancel_requested = 0
            ORDER BY priority DESC, created_at, id
            LIMIT 100
            """
        ).fetchall()
        for row in rows:
            resource = str(row["resource"])
            if usage.get(resource, 0) >= RESOURCE_LIMITS.get(resource, 1):
                continue
            reserved_browser = resource == "browser" and profile_manager.acquire_runtime(str(row["id"]))
            if resource == "browser" and not reserved_browser:
                continue
            lease_token = uuid.uuid4().hex
            cursor = conn.execute(
                """
                UPDATE runtime_jobs
                SET status = 'running', attempt = attempt + 1, lease_token = ?,
                    heartbeat_at = datetime('now', 'localtime'),
                    started_at = COALESCE(started_at, datetime('now', 'localtime')),
                    updated_at = datetime('now', 'localtime')
                WHERE id = ? AND status = 'queued' AND cancel_requested = 0
                """,
                (lease_token, row["id"]),
            )
            if cursor.rowcount:
                claimed = dict(_format_job(row))
                claimed["status"] = "running"
                claimed["attempt"] = int(row["attempt"] or 0) + 1
                claimed["lease_token"] = lease_token
                return claimed
            if reserved_browser:
                profile_manager.release_runtime(str(row["id"]))
    return None


def _run_job(job: dict[str, Any]) -> None:
    from app.services import profile_manager

    job_id = str(job["id"])
    try:
        result = _execute_job(job)
        outcome = _domain_outcome(job, result)
        if outcome["status"] == "failed":
            raise RuntimeError(str(outcome.get("error") or "业务任务执行失败"))
        final_status = "cancelled" if cancel_requested(job_id) or outcome["status"] == "cancelled" else "succeeded"
        _finish_job(job_id, final_status, result=result)
    except Exception as exc:
        if cancel_requested(job_id):
            _finish_job(job_id, "cancelled", error=str(exc))
        else:
            _mark_video_domain_failed(job, str(exc))
            _finish_job(job_id, "failed", error=str(exc))
    finally:
        if str(job["resource"]) == "browser":
            profile_manager.release_runtime(job_id)
        with _STATE_LOCK:
            _ACTIVE.pop(job_id, None)
        _WAKE.set()


def _execute_job(job: dict[str, Any]) -> Any:
    return _handlers().execute_job(job)


def _mark_video_domain_failed(job: dict[str, Any], error: str) -> None:
    _handlers().mark_domain_failed(job, error)


def _domain_outcome(job: dict[str, Any], result: Any = None) -> dict[str, str]:
    return _handlers().domain_outcome(job, result)


def _finish_job(job_id: str, status: str, *, result: Any = None, error: str = "") -> None:
    with database.connect() as conn:
        conn.execute(
            """
            UPDATE runtime_jobs
            SET status = ?, error = ?, result = ?, lease_token = '',
                heartbeat_at = datetime('now', 'localtime'),
                finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE id = ? AND status = 'running'
            """,
            (status, error[:2000], json.dumps(_json_safe(result), ensure_ascii=False), job_id),
        )


def _heartbeat_active() -> None:
    with _STATE_LOCK:
        job_ids = list(_ACTIVE)
    if not job_ids:
        return
    placeholders = ",".join("?" for _ in job_ids)
    with database.connect() as conn:
        conn.execute(
            f"UPDATE runtime_jobs SET heartbeat_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime') WHERE id IN ({placeholders}) AND status = 'running'",
            job_ids,
        )


def _prune_active() -> None:
    with _STATE_LOCK:
        finished = [job_id for job_id, item in _ACTIVE.items() if not item.thread.is_alive()]
        for job_id in finished:
            _ACTIVE.pop(job_id, None)


def cancel_requested(job_id: str) -> bool:
    with database.connect() as conn:
        row = conn.execute("SELECT cancel_requested FROM runtime_jobs WHERE id = ?", (job_id,)).fetchone()
    return bool(row and int(row["cancel_requested"] or 0))


def _cancel_domain_job(job: dict[str, Any], reason: str = "用户取消", *, queued: bool = False) -> None:
    _handlers().cancel_domain_job(job, reason, queued=queued)


def _prepare_domain_retry(job: dict[str, Any]) -> None:
    _handlers().prepare_domain_retry(job)


def _normalize_resumable_domain(conn: Any, job: dict[str, Any]) -> None:
    _handlers().normalize_resumable_domain(conn, job)


def _normalize_interrupted_domain(conn: Any, job: dict[str, Any], reason: str) -> None:
    _handlers().normalize_interrupted_domain(conn, job, reason)


def _can_resume(kind: str) -> bool:
    return bool(_handlers().can_resume(kind))


def _format_job(row: Any) -> dict[str, Any]:
    value = database.row_to_dict(row) or dict(row)
    # 队列接口约定这两个 JSON 字段始终为对象。
    for key in ("payload", "result"):
        try:
            parsed = json.loads(value.get(key) or "{}")
        except (TypeError, json.JSONDecodeError):
            parsed = {}
        value[key] = parsed if isinstance(parsed, dict) else {}
    value["cancel_requested"] = bool(value.get("cancel_requested"))
    return value


def _json_safe(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return {"summary": str(value)}


def _payload_identifiers(payload: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for value in payload.values():
        if isinstance(value, list):
            values.update(str(item) for item in value)
        elif isinstance(value, (str, int)):
            values.add(str(value))
    return values
