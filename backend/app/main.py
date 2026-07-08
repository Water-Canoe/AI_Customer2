from __future__ import annotations

import os
import sys
import sqlite3
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app import database
from app.schemas import (
    AiBatchCreate,
    AiBulkDelete,
    AiJobCreate,
    BulkActionPreview,
    ClearDataRequest,
    CustomerAutoMessageRequest,
    CustomerFollowStatusUpdate,
    LicenseUpdate,
    MessageAutoBatchCreate,
    SettingsUpdate,
    TableUpdate,
    TaskCreate,
    TrafficPlanCreate,
    TrafficSettingsUpdate,
)
from app.services import account_actions, ai_service, bulk_actions, crawler_adapter, deletion, diagnostics, license_service, maintenance, message_workbench, ops_visibility, traffic_workbench
from app import views


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.init_db()
    crawler_adapter.recover_interrupted_running_tasks()
    yield


app = FastAPI(title="AI拓客工具", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def require_license() -> None:
    try:
        license_service.ensure_authorized()
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def require_license_for(scope: str) -> None:
    try:
        license_service.ensure_authorized_for(scope)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _own_account_items_for_platform(platform: str) -> list[str]:
    """Read configured self-account IDs for the selected platform."""
    with database.connect() as conn:
        raw = database.get_setting(conn, "own_accounts", "{}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    items = parsed.get(platform, [])
    if not isinstance(items, list):
        return []
    return [str(item).strip() for item in items if str(item).strip()]


def _apply_own_account_defaults(payload: TaskCreate) -> TaskCreate:
    """Allow API callers to reuse self accounts configured in Settings."""
    if payload.mode != "own_account" or payload.creator_id.strip() or payload.specified_id.strip():
        return payload
    creator_ids = _own_account_items_for_platform(payload.platform)
    if not creator_ids:
        return payload
    return payload.model_copy(update={"creator_id": ",".join(creator_ids)})


@app.get("/api/license")
def get_license() -> dict[str, object]:
    return license_service.license_overview()


@app.put("/api/license")
def update_license(payload: LicenseUpdate) -> dict[str, object]:
    return license_service.update_license_code(payload.license_code)


@app.post("/api/license/check")
def check_license(payload: LicenseUpdate) -> dict[str, object]:
    return license_service.check_license(payload.license_code)


@app.get("/api/traffic/license")
def get_traffic_license() -> dict[str, object]:
    return license_service.license_overview_for("traffic")


@app.put("/api/traffic/license")
def update_traffic_license(payload: LicenseUpdate) -> dict[str, object]:
    return license_service.update_license_code_for("traffic", payload.license_code)


@app.post("/api/traffic/license")
@app.post("/api/traffic/license/check")
def check_traffic_license(payload: LicenseUpdate) -> dict[str, object]:
    return license_service.check_license_for("traffic", payload.license_code)


@app.get("/api/traffic/plans")
def list_traffic_plans(include_archived: bool = False) -> list[dict[str, object]]:
    return traffic_workbench.list_plans(include_archived)


@app.post("/api/traffic/plans")
def create_traffic_plan(payload: TrafficPlanCreate) -> dict[str, object]:
    try:
        return traffic_workbench.create_plan(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/traffic/plans/{plan_id}")
def update_traffic_plan(plan_id: str, payload: TrafficPlanCreate) -> dict[str, object]:
    try:
        return traffic_workbench.update_plan(plan_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/traffic/plans/{plan_id}")
def delete_traffic_plan(plan_id: str) -> dict[str, object]:
    try:
        return traffic_workbench.delete_plan(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/traffic/plans/{plan_id}/archive")
def archive_traffic_plan(plan_id: str) -> dict[str, object]:
    try:
        return traffic_workbench.archive_plan(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/traffic/plans/{plan_id}/restore")
def restore_traffic_plan(plan_id: str) -> dict[str, object]:
    try:
        return traffic_workbench.restore_plan(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/traffic/plans/{plan_id}/runs")
def create_traffic_run(plan_id: str, background_tasks: BackgroundTasks) -> dict[str, object]:
    require_license_for("traffic")
    try:
        run = traffic_workbench.create_run(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background_tasks.add_task(traffic_workbench.run_traffic_run, str(run["id"]))
    return run


@app.get("/api/traffic/runs")
def list_traffic_runs(include_archived: bool = False) -> list[dict[str, object]]:
    return traffic_workbench.list_runs(include_archived)


@app.get("/api/traffic/runs/{run_id}")
def get_traffic_run(run_id: str) -> dict[str, object]:
    run = traffic_workbench.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="引流批次不存在")
    return run


@app.post("/api/traffic/runs/{run_id}/stop")
def stop_traffic_run(run_id: str) -> dict[str, object]:
    try:
        return traffic_workbench.stop_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/traffic/runs/{run_id}/archive")
def archive_traffic_run(run_id: str) -> dict[str, object]:
    try:
        return traffic_workbench.archive_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/traffic/runs/{run_id}/restore")
def restore_traffic_run(run_id: str) -> dict[str, object]:
    try:
        return traffic_workbench.restore_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/traffic/runs/{run_id}")
def delete_traffic_run(run_id: str) -> dict[str, object]:
    try:
        return traffic_workbench.delete_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/traffic/runs/{run_id}/logs")
def list_traffic_logs(run_id: str) -> list[dict[str, object]]:
    return traffic_workbench.list_logs(run_id)


@app.get("/api/traffic/records")
def list_traffic_records(
    query: str = "",
    platform: str = "",
    status: str = "",
    action: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    return traffic_workbench.list_records(query=query, platform=platform, status=status, action=action, page=page, page_size=page_size)


@app.delete("/api/traffic/records")
def clear_traffic_records() -> dict[str, object]:
    return traffic_workbench.clear_records()


@app.get("/api/traffic/settings")
def get_traffic_settings() -> dict[str, object]:
    return traffic_workbench.get_settings()


@app.put("/api/traffic/settings")
def update_traffic_settings(payload: TrafficSettingsUpdate) -> dict[str, object]:
    return traffic_workbench.update_settings(payload)


@app.get("/api/traffic/environment-check")
def traffic_environment_check() -> dict[str, object]:
    return traffic_workbench.environment_check()


@app.post("/api/traffic/environment-install")
def traffic_environment_install() -> dict[str, object]:
    return traffic_workbench.install_environment()


@app.post("/api/traffic/douyin-login")
def open_traffic_douyin_login() -> dict[str, object]:
    try:
        return traffic_workbench.open_douyin_login_window()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/traffic/material-images")
async def upload_traffic_material_image(request: Request, filename: str = Query(default="")) -> dict[str, object]:
    try:
        return traffic_workbench.save_material_image(filename, await request.body())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/traffic/material-images/{name}")
def preview_traffic_material_image(name: str) -> FileResponse:
    try:
        return FileResponse(traffic_workbench.material_image_path(name))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/traffic/source-keywords")
def list_traffic_source_keywords() -> list[dict[str, object]]:
    return traffic_workbench.source_keywords()


@app.get("/api/traffic/source-competitor-videos")
def list_traffic_source_competitor_videos() -> list[dict[str, object]]:
    return traffic_workbench.source_competitor_videos()


@app.post("/api/tasks")
def create_task(payload: TaskCreate, background_tasks: BackgroundTasks) -> dict[str, object]:
    require_license()
    try:
        payload = _apply_own_account_defaults(payload)
        account_ids = (
            account_actions.resolve_account_analysis_task_account_ids(payload.model_dump())
            if payload.mode == "account_analysis"
            else []
        )
        task = crawler_adapter.create_task(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.mode == "account_analysis" and account_ids:
        if len(account_ids) == 1:
            background_tasks.add_task(account_actions.run_account_analysis, account_ids[0], str(task["id"]))
        else:
            background_tasks.add_task(account_actions.run_account_analysis_batch, account_ids, str(task["id"]))
    else:
        background_tasks.add_task(crawler_adapter.run_task, str(task["id"]))
    return task


@app.post("/api/tasks/preview")
def preview_task(payload: TaskCreate) -> dict[str, object]:
    try:
        payload = _apply_own_account_defaults(payload)
        return crawler_adapter.preview_task(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/accounts/{account_id}/profile-enrichment")
def create_profile_enrichment_task(account_id: int, background_tasks: BackgroundTasks) -> dict[str, object]:
    require_license()
    try:
        task = crawler_adapter.create_profile_enrichment_task(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background_tasks.add_task(crawler_adapter.run_task, str(task["id"]))
    return task


@app.post("/api/accounts/profile-enrichment/batch")
def create_profile_enrichment_batch(
    background_tasks: BackgroundTasks,
    limit: int = Query(default=10, ge=1, le=50),
    run_now: bool = True,
) -> dict[str, object]:
    require_license()
    try:
        result = crawler_adapter.create_profile_enrichment_batch(limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if run_now and result["task_ids"]:
        background_tasks.add_task(crawler_adapter.run_tasks_serially, result["task_ids"])
    return result


@app.post("/api/accounts/{account_id}/analysis")
def create_account_analysis_task(
    account_id: int,
    background_tasks: BackgroundTasks,
    run_now: bool = True,
) -> dict[str, object]:
    require_license()
    try:
        task = account_actions.create_account_analysis_task(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if run_now:
        background_tasks.add_task(account_actions.run_account_analysis, account_id, str(task["id"]))
    return task


@app.post("/api/accounts/{account_id}/find-customers")
def create_account_find_customer_task(
    account_id: int,
    background_tasks: BackgroundTasks,
    run_now: bool = True,
) -> dict[str, object]:
    require_license()
    try:
        result = account_actions.create_account_find_customer_task(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if run_now and result["task_ids"]:
        background_tasks.add_task(crawler_adapter.run_tasks_serially, result["task_ids"])
    return result


@app.post("/api/overview/customers/{lead_id}/intent-analysis")
def analyze_overview_customer_intent(
    lead_id: int,
    run_now: bool = True,
) -> dict[str, object]:
    require_license()
    try:
        return account_actions.create_customer_intent_analysis(lead_id, run_now=run_now)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/overview/customers/{lead_id}/follow-status")
def update_overview_customer_follow_status(
    lead_id: int,
    payload: CustomerFollowStatusUpdate,
) -> dict[str, object]:
    try:
        return account_actions.update_customer_follow_status(lead_id, payload.follow_status, payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/overview/accounts/{account_id}/customers/analyze")
def analyze_overview_account_customers(
    account_id: int,
    background_tasks: BackgroundTasks,
    run_now: bool = True,
) -> dict[str, object]:
    require_license()
    try:
        result = account_actions.create_account_customer_intent_jobs(account_id, run_now=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if run_now and result["job_ids"]:
        background_tasks.add_task(account_actions.run_account_customer_intent_jobs, result["job_ids"])
    return result


@app.post("/api/overview/accounts/{account_id}/customers/non-customers/delete")
def delete_overview_account_non_customers(account_id: int) -> dict[str, object]:
    try:
        return account_actions.delete_account_non_customers(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/tasks")
def list_tasks(include_archived: bool = False) -> list[dict[str, object]]:
    return crawler_adapter.list_tasks(include_archived)


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, object]:
    task = crawler_adapter.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    task["logs"] = crawler_adapter.list_task_logs(task_id)
    return task


@app.get("/api/tasks/{task_id}/diagnostics")
def get_task_diagnostics(task_id: str) -> dict[str, object]:
    return diagnostics.task_diagnostics(task_id)


@app.get("/api/tasks/{task_id}/dedup-summary")
def get_task_dedup_summary(task_id: str) -> dict[str, object]:
    return ops_visibility.task_dedup_summary(task_id)


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> dict[str, object]:
    return crawler_adapter.cancel_task(task_id)


@app.post("/api/tasks/{task_id}/archive")
def archive_task(task_id: str) -> dict[str, object]:
    return crawler_adapter.archive_task(task_id)


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str) -> dict[str, object]:
    return deletion.delete_task(task_id)


@app.get("/api/tables/{library}")
def list_table(
    library: str,
    status: str = Query(default=""),
    keyword: str = Query(default=""),
) -> dict[str, object]:
    try:
        return views.list_library(library, status=status, keyword=keyword)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="未知数据表") from exc


@app.patch("/api/tables/{library}/{row_id}")
def update_table_row(library: str, row_id: int, payload: TableUpdate) -> dict[str, object]:
    return views.update_library_row(library, row_id, payload.values)


@app.delete("/api/tables/{library}/{row_id}")
def delete_table_row(
    library: str,
    row_id: int,
    hard: bool | None = Query(default=None),
) -> dict[str, object]:
    return deletion.delete_library_row(library, row_id, hard)


@app.get("/api/overview/tree")
def overview_tree() -> list[dict[str, object]]:
    return views.overview_tree()


@app.post("/api/overview/keywords/non-competitors/delete")
def delete_keyword_non_competitors(
    platform: str = Query(...),
    keyword: str = Query(...),
) -> dict[str, object]:
    return account_actions.delete_keyword_non_competitors(platform, keyword)


@app.post("/api/overview/keywords/analyze")
def analyze_keyword_accounts(
    background_tasks: BackgroundTasks,
    platform: str = Query(...),
    keyword: str = Query(...),
    run_now: bool = True,
) -> dict[str, object]:
    require_license()
    result = account_actions.create_keyword_account_analysis_tasks(platform, keyword)
    if run_now and result["task_ids"]:
        account_ids = [int(item["account_id"]) for item in result.get("accounts", [])]
        background_tasks.add_task(account_actions.run_keyword_account_analysis, account_ids, str(result["task_ids"][0]))
    return result


@app.post("/api/overview/keywords/find-customers")
def find_keyword_customers(
    background_tasks: BackgroundTasks,
    platform: str = Query(...),
    keyword: str = Query(...),
    run_now: bool = True,
) -> dict[str, object]:
    require_license()
    result = account_actions.create_keyword_find_customer_task(platform, keyword)
    if run_now and result["task_ids"]:
        background_tasks.add_task(crawler_adapter.run_tasks_serially, result["task_ids"])
    return result


@app.delete("/api/overview/platforms/{platform}")
def delete_overview_platform(platform: str) -> dict[str, object]:
    return deletion.delete_overview_platform(platform)


@app.delete("/api/overview/keywords")
def delete_overview_keyword(
    platform: str = Query(...),
    keyword: str = Query(...),
) -> dict[str, object]:
    return deletion.delete_overview_keyword(platform, keyword)


@app.delete("/api/overview/accounts/{account_id}")
def delete_overview_account(account_id: int) -> dict[str, object]:
    return deletion.delete_overview_account(account_id)


@app.delete("/api/overview/customers/{lead_id}")
def delete_overview_customer(
    lead_id: int,
    source_account_id: int | None = Query(default=None),
) -> dict[str, object]:
    return deletion.delete_lead_customer(lead_id, source_account_id=source_account_id, source="overview_customer_delete")


@app.get("/api/workbench/actions")
def workbench_actions() -> dict[str, object]:
    return views.workbench_actions()


@app.get("/api/tombstones/summary")
def get_tombstone_summary() -> dict[str, object]:
    return ops_visibility.tombstone_summary()


@app.get("/api/tombstones")
def get_tombstones(
    entity_type: str = Query(default=""),
    platform: str = Query(default=""),
    source: str = Query(default=""),
    query: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    return ops_visibility.list_tombstones(
        entity_type=entity_type,
        platform=platform,
        source=source,
        query=query,
        page=page,
        page_size=page_size,
    )


@app.post("/api/bulk-actions/preview")
def bulk_action_preview(payload: BulkActionPreview) -> dict[str, object]:
    return bulk_actions.preview_bulk_action(payload)


@app.get("/api/message-workbench/keywords")
def message_workbench_keywords() -> list[dict[str, object]]:
    return message_workbench.list_keywords()


@app.get("/api/message-workbench/customers")
def message_workbench_customers(
    keyword: str = Query(default=""),
    platform: str = Query(default=""),
    status: str = Query(default="待私信"),
    query: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    return message_workbench.list_customers(keyword=keyword, platform=platform, status=status, query=query, page=page, page_size=page_size)


@app.get("/api/message-workbench/customers/{lead_id}")
def message_workbench_customer_detail(lead_id: int) -> dict[str, object]:
    try:
        return message_workbench.customer_detail(lead_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/message-workbench/customers/{lead_id}/auto-message")
async def message_workbench_customer_auto_message(
    lead_id: int,
    payload: CustomerAutoMessageRequest,
) -> dict[str, object]:
    require_license()
    try:
        return await message_workbench.auto_message_customer(
            lead_id,
            dry_run=payload.dry_run,
            timeout_seconds=payload.timeout_seconds,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/message-workbench/auto-message-batches")
def message_workbench_auto_message_batches(batch_id: str = Query(default="")) -> dict[str, object]:
    return message_workbench.list_auto_message_batches(batch_id=batch_id)


@app.post("/api/message-workbench/auto-message-batches")
async def create_message_workbench_auto_message_batch(payload: MessageAutoBatchCreate) -> dict[str, object]:
    require_license()
    try:
        return message_workbench.create_auto_message_batch(
            platform=payload.platform,
            keyword=payload.keyword,
            count=payload.count,
            interval_min_seconds=payload.interval_min_seconds,
            interval_max_seconds=payload.interval_max_seconds,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/message-workbench/auto-message-batches/{batch_id}/cancel")
def cancel_message_workbench_auto_message_batch(batch_id: str) -> dict[str, object]:
    require_license()
    try:
        return message_workbench.cancel_auto_message_batch(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/platform-capabilities")
def platform_capabilities() -> list[dict[str, object]]:
    return views.platform_capabilities()


@app.get("/api/overview/node/{node_id:path}")
def overview_node(node_id: str) -> dict[str, object]:
    return views.overview_node(node_id)


@app.post("/api/ai/jobs")
def create_ai_job(payload: AiJobCreate) -> dict[str, object]:
    require_license()
    return ai_service.create_ai_job(payload.target_type, payload.target_id, payload.run_now)


@app.post("/api/ai/jobs/batch")
def create_ai_jobs(payload: AiBatchCreate) -> list[dict[str, object]]:
    require_license()
    return ai_service.create_batch_jobs(payload.target_type, payload.target_ids, payload.run_now)


@app.get("/api/ai/jobs")
def list_ai_jobs() -> list[dict[str, object]]:
    return ai_service.list_ai_jobs()


@app.get("/api/ai/workbench")
def ai_workbench() -> dict[str, object]:
    return ai_service.ai_workbench()


@app.post("/api/ai/workbench/non-competitors/delete")
def delete_ai_workbench_non_competitors(payload: AiBulkDelete) -> dict[str, object]:
    return ai_service.delete_workbench_non_competitors(payload.target_ids)


@app.post("/api/ai/workbench/non-customers/delete")
def delete_ai_workbench_non_customers(payload: AiBulkDelete) -> dict[str, object]:
    return ai_service.delete_workbench_non_customers(payload.target_ids)


@app.post("/api/ai/jobs/{job_id}/retry")
def retry_ai_job(job_id: str) -> dict[str, object]:
    require_license()
    return ai_service.retry_ai_job(job_id)


@app.get("/api/settings")
def get_settings() -> dict[str, object]:
    return views.get_settings()


@app.put("/api/settings")
def update_settings(payload: SettingsUpdate) -> dict[str, object]:
    return views.update_settings(payload.values)


@app.get("/api/settings/env-check")
def env_check() -> dict[str, object]:
    return views.environment_check()


@app.post("/api/settings/clear-data")
def clear_data(payload: ClearDataRequest) -> dict[str, object]:
    try:
        return maintenance.clear_all_data(payload.confirm)
    except (ValueError, sqlite3.Error) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _frontend_dist() -> Path | None:
    candidates: list[Path] = []
    env_path = os.getenv("AI_CUSTOMER_FRONTEND_DIST", "").strip()
    if env_path:
        candidates.append(Path(env_path))
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", "")) / "frontend_dist")
        candidates.append(Path(sys.executable).resolve().parent / "frontend_dist")
    candidates.append(database.WORKSPACE_ROOT / "frontend" / "dist")
    for path in candidates:
        if path and (path / "index.html").exists():
            return path
    return None


@app.get("/{full_path:path}", include_in_schema=False)
def serve_frontend(full_path: str) -> FileResponse:
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="接口不存在")
    dist = _frontend_dist()
    if not dist:
        raise HTTPException(status_code=404, detail="前端静态文件不存在，请先运行 npm run build")
    safe_dist = dist.resolve()
    target = (safe_dist / full_path).resolve()
    if target.is_file() and str(target).startswith(str(safe_dist)):
        return FileResponse(target)
    return FileResponse(safe_dist / "index.html")
