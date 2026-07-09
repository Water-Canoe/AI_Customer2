from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api_dependencies import require_license
from app.schemas import CustomerAutoMessageRequest, MessageAutoBatchCreate
from app.services import job_queue, message_workbench


router = APIRouter(prefix="/api/message-workbench", tags=["message"])


@router.get("/keywords")
def message_workbench_keywords() -> list[dict[str, object]]:
    return message_workbench.list_keywords()


@router.get("/customers")
def message_workbench_customers(
    keyword: str = Query(default=""),
    platform: str = Query(default=""),
    status: str = Query(default="待私信"),
    query: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    return message_workbench.list_customers(keyword=keyword, platform=platform, status=status, query=query, page=page, page_size=page_size)


@router.get("/customers/{lead_id}")
def message_workbench_customer_detail(lead_id: int) -> dict[str, object]:
    try:
        return message_workbench.customer_detail(lead_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/customers/{lead_id}/auto-message")
def message_workbench_customer_auto_message(
    lead_id: int,
    payload: CustomerAutoMessageRequest,
) -> dict[str, object]:
    require_license()
    try:
        return job_queue.enqueue_single_message(
            lead_id,
            dry_run=payload.dry_run,
            timeout_seconds=payload.timeout_seconds,
            message_script=payload.message_script,
            script_label=payload.script_label,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/auto-message-batches")
def message_workbench_auto_message_batches(batch_id: str = Query(default="")) -> dict[str, object]:
    return message_workbench.list_auto_message_batches(batch_id=batch_id)


@router.post("/auto-message-batches")
async def create_message_workbench_auto_message_batch(payload: MessageAutoBatchCreate) -> dict[str, object]:
    require_license()
    try:
        batch = message_workbench.create_auto_message_batch(
            platform=payload.platform,
            keyword=payload.keyword,
            count=payload.count,
            interval_min_seconds=payload.interval_min_seconds,
            interval_max_seconds=payload.interval_max_seconds,
            run_now=False,
        )
        batch["runtime_job"] = job_queue.enqueue_message_batch(str(batch["id"]))
        return batch
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/auto-message-batches/{batch_id}/cancel")
def cancel_message_workbench_auto_message_batch(batch_id: str) -> dict[str, object]:
    require_license()
    try:
        job_queue.cancel_by_entity("message_batch", batch_id, "用户取消自动私信批次")
        return message_workbench.cancel_auto_message_batch(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/auto-message-batches/{batch_id}/retry")
def retry_message_workbench_auto_message_batch(batch_id: str) -> dict[str, object]:
    require_license()
    try:
        batch = message_workbench.retry_auto_message_batch(batch_id)
        batch["runtime_job"] = job_queue.enqueue_message_batch(str(batch["id"]))
        return batch
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/auto-message-batches/{batch_id}")
def delete_message_workbench_auto_message_batch(batch_id: str) -> dict[str, object]:
    require_license()
    try:
        return message_workbench.delete_auto_message_batch(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
