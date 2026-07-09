from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.api_dependencies import require_license_for
from app.schemas import LicenseUpdate, TrafficPlanCreate, TrafficSettingsUpdate
from app.services import job_queue, license_service, traffic_workbench


router = APIRouter(prefix="/api/traffic", tags=["traffic"])


@router.get("/license")
def get_traffic_license() -> dict[str, object]:
    return license_service.license_overview_for("traffic")


@router.put("/license")
def update_traffic_license(payload: LicenseUpdate) -> dict[str, object]:
    return license_service.update_license_code_for("traffic", payload.license_code)


@router.post("/license")
@router.post("/license/check")
def check_traffic_license(payload: LicenseUpdate) -> dict[str, object]:
    return license_service.check_license_for("traffic", payload.license_code)


@router.get("/plans")
def list_traffic_plans(include_archived: bool = False) -> list[dict[str, object]]:
    return traffic_workbench.list_plans(include_archived)


@router.post("/plans")
def create_traffic_plan(payload: TrafficPlanCreate) -> dict[str, object]:
    try:
        return traffic_workbench.create_plan(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/plans/{plan_id}")
def update_traffic_plan(plan_id: str, payload: TrafficPlanCreate) -> dict[str, object]:
    try:
        return traffic_workbench.update_plan(plan_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/plans/{plan_id}")
def delete_traffic_plan(plan_id: str) -> dict[str, object]:
    try:
        return traffic_workbench.delete_plan(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/plans/{plan_id}/archive")
def archive_traffic_plan(plan_id: str) -> dict[str, object]:
    try:
        return traffic_workbench.archive_plan(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/plans/{plan_id}/restore")
def restore_traffic_plan(plan_id: str) -> dict[str, object]:
    try:
        return traffic_workbench.restore_plan(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/plans/{plan_id}/runs")
def create_traffic_run(plan_id: str) -> dict[str, object]:
    require_license_for("traffic")
    try:
        run = traffic_workbench.create_run(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    run["runtime_job"] = job_queue.enqueue_traffic_run(str(run["id"]))
    return run


@router.get("/runs")
def list_traffic_runs(include_archived: bool = False) -> list[dict[str, object]]:
    return traffic_workbench.list_runs(include_archived)


@router.get("/runs/{run_id}")
def get_traffic_run(run_id: str) -> dict[str, object]:
    run = traffic_workbench.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="引流批次不存在")
    return run


@router.post("/runs/{run_id}/stop")
def stop_traffic_run(run_id: str) -> dict[str, object]:
    try:
        job_queue.cancel_by_entity("traffic_run", run_id, "用户停止引流批次")
        return traffic_workbench.stop_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{run_id}/archive")
def archive_traffic_run(run_id: str) -> dict[str, object]:
    try:
        return traffic_workbench.archive_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/restore")
def restore_traffic_run(run_id: str) -> dict[str, object]:
    try:
        return traffic_workbench.restore_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/runs/{run_id}")
def delete_traffic_run(run_id: str) -> dict[str, object]:
    try:
        return traffic_workbench.delete_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}/logs")
def list_traffic_logs(run_id: str) -> list[dict[str, object]]:
    return traffic_workbench.list_logs(run_id)


@router.get("/records")
def list_traffic_records(
    query: str = "",
    platform: str = "",
    status: str = "",
    action: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    return traffic_workbench.list_records(query=query, platform=platform, status=status, action=action, page=page, page_size=page_size)


@router.delete("/records")
def clear_traffic_records() -> dict[str, object]:
    try:
        return traffic_workbench.clear_records()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/settings")
def get_traffic_settings() -> dict[str, object]:
    return traffic_workbench.get_settings()


@router.put("/settings")
def update_traffic_settings(payload: TrafficSettingsUpdate) -> dict[str, object]:
    return traffic_workbench.update_settings(payload)


@router.get("/environment-check")
def traffic_environment_check() -> dict[str, object]:
    return traffic_workbench.environment_check()


@router.post("/environment-install")
def traffic_environment_install() -> dict[str, object]:
    return traffic_workbench.install_environment()


@router.post("/douyin-login")
def open_traffic_douyin_login() -> dict[str, object]:
    try:
        return traffic_workbench.open_douyin_login_window()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/material-images")
async def upload_traffic_material_image(request: Request, filename: str = Query(default="")) -> dict[str, object]:
    try:
        return traffic_workbench.save_material_image(filename, await request.body())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/material-images/{name}")
def preview_traffic_material_image(name: str) -> FileResponse:
    try:
        return FileResponse(traffic_workbench.material_image_path(name))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/source-keywords")
def list_traffic_source_keywords() -> list[dict[str, object]]:
    return traffic_workbench.source_keywords()


@router.get("/source-competitor-videos")
def list_traffic_source_competitor_videos() -> list[dict[str, object]]:
    return traffic_workbench.source_competitor_videos()
