from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api_dependencies import apply_own_account_defaults, require_license
from app.schemas import TaskCreate
from app.services import account_actions, crawler_adapter, deletion, diagnostics, job_queue, ops_visibility


router = APIRouter(prefix="/api", tags=["tasks"])


@router.post("/tasks")
def create_task(payload: TaskCreate) -> dict[str, object]:
    require_license()
    try:
        payload = apply_own_account_defaults(payload)
        account_ids = (
            account_actions.resolve_account_analysis_task_account_ids(payload.model_dump())
            if payload.mode == "account_analysis"
            else []
        )
        task = crawler_adapter.create_task(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.mode == "account_analysis" and account_ids:
        task["runtime_job"] = job_queue.enqueue_account_analysis(account_ids, str(task["id"]))
    else:
        task["runtime_job"] = job_queue.enqueue_crawl_task(str(task["id"]))
    return task


@router.post("/tasks/preview")
def preview_task(payload: TaskCreate) -> dict[str, object]:
    try:
        return crawler_adapter.preview_task(apply_own_account_defaults(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tasks")
def list_tasks(
    include_archived: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    query: str = Query(default=""),
) -> dict[str, object]:
    return crawler_adapter.list_tasks(include_archived, page, page_size, query)


@router.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, object]:
    task = crawler_adapter.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    task["logs"] = crawler_adapter.list_task_logs(task_id)
    return task


@router.get("/tasks/{task_id}/diagnostics")
def get_task_diagnostics(task_id: str) -> dict[str, object]:
    return diagnostics.task_diagnostics(task_id)


@router.get("/tasks/{task_id}/dedup-summary")
def get_task_dedup_summary(task_id: str) -> dict[str, object]:
    return ops_visibility.task_dedup_summary(task_id)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> dict[str, object]:
    job_queue.cancel_jobs_for_domain_id(task_id, "用户取消采集任务")
    return crawler_adapter.cancel_task(task_id)


@router.post("/tasks/{task_id}/archive")
def archive_task(task_id: str) -> dict[str, object]:
    return crawler_adapter.archive_task(task_id)


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str) -> dict[str, object]:
    return deletion.delete_task(task_id)
