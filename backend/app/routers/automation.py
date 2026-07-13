from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api_dependencies import require_license
from app.schemas import AutomationMessageLimitsUpdate, AutomationPlanCreate, AutomationPlanOrderUpdate, AutomationPlanPatch
from app.services import automation_workbench, job_queue


router = APIRouter(prefix="/api/automation", tags=["automation"])


@router.get("/plans")
def automation_plans() -> dict[str, object]:
    return automation_workbench.list_plans()


@router.post("/plans")
def create_automation_plan(payload: AutomationPlanCreate) -> dict[str, object]:
    try:
        return automation_workbench.create_plan(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/plans/order")
def reorder_automation_plans(payload: AutomationPlanOrderUpdate) -> dict[str, object]:
    try:
        return automation_workbench.reorder_plans(payload.plan_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/plans/{plan_id}")
def update_automation_plan(plan_id: str, payload: AutomationPlanPatch) -> dict[str, object]:
    try:
        return automation_workbench.update_plan(plan_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/plans/{plan_id}/run")
def run_automation_plan(plan_id: str) -> dict[str, object]:
    require_license()
    try:
        return automation_workbench.create_run(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/plans/{plan_id}/archive")
def archive_automation_plan(plan_id: str) -> dict[str, object]:
    try:
        return automation_workbench.archive_plan(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs")
def automation_runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
) -> dict[str, object]:
    return automation_workbench.list_runs(page=page, page_size=page_size)


@router.get("/runs/{run_id}")
def automation_run_detail(run_id: str) -> dict[str, object]:
    try:
        return automation_workbench.get_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{run_id}/cancel")
def cancel_automation_run(run_id: str) -> dict[str, object]:
    require_license()
    try:
        run = automation_workbench.get_run(run_id)
        runtime_job_id = str(run.get("runtime_job_id") or "")
        if runtime_job_id:
            job_queue.request_cancel(runtime_job_id, reason="用户停止自动化计划")
        return automation_workbench.cancel_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/message-limits")
def automation_message_limits() -> dict[str, object]:
    return automation_workbench.get_message_limits()


@router.put("/message-limits")
def update_automation_message_limits(payload: AutomationMessageLimitsUpdate) -> dict[str, object]:
    return automation_workbench.update_message_limits(payload.daily_limit, payload.hourly_limit)
