"""采集账号的资料补全、分析与找客户任务入口。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api_dependencies import require_license
from app.services import account_actions, crawler_adapter, job_queue


router = APIRouter(prefix="/api", tags=["lead-accounts"])


@router.post("/accounts/{account_id}/profile-enrichment")
def create_profile_enrichment_task(account_id: int) -> dict[str, object]:
    require_license()
    try:
        task = crawler_adapter.create_profile_enrichment_task(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    task["runtime_job"] = job_queue.enqueue_crawl_task(str(task["id"]))
    return task


@router.post("/accounts/profile-enrichment/batch")
def create_profile_enrichment_batch(
    limit: int = Query(default=10, ge=1, le=50),
    run_now: bool = True,
) -> dict[str, object]:
    require_license()
    try:
        result = crawler_adapter.create_profile_enrichment_batch(limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if run_now and result["task_ids"]:
        result["runtime_job"] = job_queue.enqueue_crawl_batch(result["task_ids"])
    return result


@router.post("/accounts/{account_id}/analysis")
def create_account_analysis_task(account_id: int, run_now: bool = True) -> dict[str, object]:
    require_license()
    try:
        task = account_actions.create_account_analysis_task(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if run_now:
        task["runtime_job"] = job_queue.enqueue_account_analysis([account_id], str(task["id"]))
    return task


@router.post("/accounts/{account_id}/find-customers")
def create_account_find_customer_task(account_id: int, run_now: bool = True) -> dict[str, object]:
    require_license()
    try:
        result = account_actions.create_account_find_customer_task(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if run_now and result["task_ids"]:
        result["runtime_job"] = job_queue.enqueue_crawl_batch(result["task_ids"])
    return result
