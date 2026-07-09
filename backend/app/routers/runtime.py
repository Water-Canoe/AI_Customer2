from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.services import job_queue, profile_manager


router = APIRouter(prefix="/api/runtime", tags=["runtime"])


@router.get("/profile")
def runtime_profile_status() -> dict[str, object]:
    return profile_manager.status()


@router.post("/profile/close")
def close_runtime_profile() -> dict[str, object]:
    return profile_manager.close_interactive()


@router.get("/jobs")
def list_runtime_jobs(
    status: str = Query(default=""),
    kind: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    return job_queue.list_jobs(status=status, kind=kind, page=page, page_size=page_size)


@router.get("/jobs/{job_id}")
def get_runtime_job(job_id: str) -> dict[str, object]:
    try:
        return job_queue.get_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/cancel")
def cancel_runtime_job(job_id: str) -> dict[str, object]:
    try:
        return job_queue.request_cancel(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/retry")
def retry_runtime_job(job_id: str) -> dict[str, object]:
    try:
        return job_queue.retry_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/jobs/{job_id}")
def delete_runtime_job(job_id: str) -> dict[str, object]:
    try:
        return job_queue.delete_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
