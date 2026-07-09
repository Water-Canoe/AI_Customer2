from __future__ import annotations

from fastapi import APIRouter

from app.api_dependencies import require_license
from app.schemas import AiBatchCreate, AiBulkDelete, AiJobCreate
from app.services import ai_service, job_queue


router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post("/jobs")
def create_ai_job(payload: AiJobCreate) -> dict[str, object]:
    require_license()
    job = ai_service.create_ai_job(payload.target_type, payload.target_id, run_now=False)
    if payload.run_now:
        job["runtime_job"] = job_queue.enqueue_ai_job(str(job["id"]))
    return job


@router.post("/jobs/batch")
def create_ai_jobs(payload: AiBatchCreate) -> list[dict[str, object]]:
    require_license()
    jobs = ai_service.create_batch_jobs(payload.target_type, payload.target_ids, run_now=False)
    if payload.run_now and jobs:
        runtime_job = job_queue.enqueue_ai_batch([str(job["id"]) for job in jobs])
        for job in jobs:
            job["runtime_job"] = runtime_job
    return jobs


@router.get("/jobs")
def list_ai_jobs() -> list[dict[str, object]]:
    return ai_service.list_ai_jobs()


@router.get("/workbench")
def ai_workbench() -> dict[str, object]:
    return ai_service.ai_workbench()


@router.post("/workbench/non-competitors/delete")
def delete_ai_workbench_non_competitors(payload: AiBulkDelete) -> dict[str, object]:
    return ai_service.delete_workbench_non_competitors(payload.target_ids)


@router.post("/workbench/non-customers/delete")
def delete_ai_workbench_non_customers(payload: AiBulkDelete) -> dict[str, object]:
    return ai_service.delete_workbench_non_customers(payload.target_ids)


@router.post("/jobs/{job_id}/retry")
def retry_ai_job(job_id: str) -> dict[str, object]:
    require_license()
    job = ai_service.retry_ai_job(job_id, run_now=False)
    job["runtime_job"] = job_queue.enqueue_ai_job(job_id)
    return job
