from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app import views
from app.schemas import BackupCreateRequest, BackupRestoreRequest, ClearDataRequest, LicenseUpdate, SettingsUpdate
from app.services import data_management, license_service, maintenance, workbench_status
from app.version import APP_VERSION


router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "product": "ai-customer",
        "version": APP_VERSION,
        "packaged": os.getenv("AI_CUSTOMER_PACKAGED") == "1",
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
    return views.update_settings(payload.values)


@router.get("/settings/env-check")
def env_check() -> dict[str, object]:
    return views.environment_check()


def _start_manual_update_launcher() -> None:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("检查更新仅支持打包版，请通过 AI_Customer.exe 启动软件")
    install_root = Path(sys.executable).resolve().parent
    launcher = install_root / "AI_Customer.exe"
    if not launcher.is_file():
        raise RuntimeError("程序目录缺少 AI_Customer.exe，无法检查更新")
    subprocess.Popen(
        [str(launcher), "--wait-for-pid", str(os.getpid())],
        cwd=str(install_root),
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


@router.post("/system/check-update")
def check_for_update(request: Request, background_tasks: BackgroundTasks) -> dict[str, object]:
    server = getattr(request.app.state, "uvicorn_server", None)
    if server is None:
        raise HTTPException(status_code=400, detail="检查更新仅支持打包版，请通过 AI_Customer.exe 启动软件")
    if getattr(request.app.state, "update_restart_requested", False):
        raise HTTPException(status_code=409, detail="应用正在重启并检查更新")
    try:
        data_management.ensure_idle("检查更新")
        _start_manual_update_launcher()
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.app.state.update_restart_requested = True
    # 响应发出后让 Uvicorn 正常执行 lifespan 清理，再由稳定启动器检查更新。
    background_tasks.add_task(setattr, server, "should_exit", True)
    return {"ok": True, "message": "应用正在重启并检查更新"}


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
