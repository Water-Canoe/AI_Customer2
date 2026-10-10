from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app import database, views
from app.schemas import BackupCreateRequest, BackupRestoreRequest, ClearDataRequest, LicenseUpdate, SettingsUpdate
from app.services import data_management, desktop_update, license_service, maintenance, workbench_status, update_workbench
from app.version import APP_VERSION


router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict[str, object]:
    packaged = os.getenv("AI_CUSTOMER_PACKAGED") == "1"
    # 报告实际安装环境和当前数据库，不能用源码常量冒充运行状态。
    environment_version = desktop_update.validate_environment(Path(os.environ["AI_CUSTOMER_INSTALL_ROOT"])) if packaged else ""
    return {
        "status": "ok",
        "product": "ai-customer",
        "version": APP_VERSION,
        "packaged": packaged,
        "environment_version": environment_version,
        "schema_version": database.schema_version()["current"],
        "updater_version": desktop_update.UPDATER_VERSION,
        "process_id": os.getpid(),
    }


@router.get("/workbench/status")
def get_workbench_status(scope: str = "lead") -> dict[str, object]:
    try:
        return workbench_status.get_status(scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/license")
def get_license() -> dict[str, object]:
    return license_service.license_overview()


@router.put("/license")
def update_license(payload: LicenseUpdate) -> dict[str, object]:
    return license_service.update_license_code(payload.license_code)


@router.post("/license/check")
def check_license(payload: LicenseUpdate) -> dict[str, object]:
    return license_service.check_license(payload.license_code)


@router.get("/settings")
def get_settings() -> dict[str, object]:
    return views.get_settings()


@router.put("/settings")
def update_settings(payload: SettingsUpdate) -> dict[str, object]:
    try:
        return views.update_settings(payload.values, payload.clear_secrets)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/settings/env-check")
def env_check() -> dict[str, object]:
    return views.environment_check()


@router.post("/system/check-update")
async def check_for_update() -> dict[str, object]:
    try:
        return await update_workbench.check()
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/system/update-status")
def get_update_status() -> dict[str, object]:
    try:
        return update_workbench.status()
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/system/prepare-update")
async def prepare_update() -> dict[str, object]:
    try:
        return await update_workbench.prepare()
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/system/install-update")
def install_update(request: Request, background_tasks: BackgroundTasks) -> dict[str, object]:
    server = getattr(request.app.state, "uvicorn_server", None)
    if server is None:
        raise HTTPException(status_code=400, detail="安装更新仅支持通过 AI_Customer.exe 启动的软件")
    try:
        result = update_workbench.install(os.getpid())
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 仅安装确认成功后关闭后台，由新入口等待旧进程退出再替换程序。
    background_tasks.add_task(setattr, server, "should_exit", True)
    return result


@router.post("/system/exit")
def exit_application(request: Request, background_tasks: BackgroundTasks) -> dict[str, object]:
    server = getattr(request.app.state, "uvicorn_server", None)
    if server is None:
        raise HTTPException(status_code=400, detail="当前启动方式不支持安全退出，请使用 script/start_all.ps1 启动开发环境")
    if getattr(request.app.state, "shutdown_requested", False):
        raise HTTPException(status_code=409, detail="应用正在退出")
    request.app.state.shutdown_requested = True
    # 响应发出后执行 lifespan 清理，确保队列和浏览器会话正确收尾。
    background_tasks.add_task(setattr, server, "should_exit", True)
    return {"ok": True, "message": "应用和当前页面正在安全退出"}


@router.get("/system/backups")
def list_system_backups() -> dict[str, object]:
    return data_management.list_backups()


@router.post("/system/backups")
def create_system_backup(payload: BackupCreateRequest) -> dict[str, object]:
    try:
        return data_management.create_backup(payload.reason)
    except (ValueError, RuntimeError, sqlite3.Error, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/system/backups/{backup_id}")
def delete_system_backup(backup_id: str) -> dict[str, object]:
    try:
        return data_management.delete_backup(backup_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/system/backups/{backup_id}/restore")
def restore_system_backup(backup_id: str, payload: BackupRestoreRequest) -> dict[str, object]:
    try:
        return data_management.restore_backup(backup_id, payload.confirm)
    except (ValueError, RuntimeError, sqlite3.Error, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/settings/clear-data")
def clear_data(payload: ClearDataRequest) -> dict[str, object]:
    try:
        return maintenance.clear_all_data(
            payload.confirm,
            create_backup=payload.create_backup,
            include_crawler=payload.include_crawler,
        )
    except (ValueError, sqlite3.Error) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
