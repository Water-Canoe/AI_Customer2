from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException

from app import views
from app.schemas import BackupCreateRequest, BackupRestoreRequest, ClearDataRequest, LicenseUpdate, SettingsUpdate
from app.services import data_management, license_service, maintenance, traffic_workbench, workbench_status
from app.version import APP_VERSION


router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": APP_VERSION}


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


@router.post("/settings/platform-login/{platform}")
def open_settings_platform_login(platform: str) -> dict[str, object]:
    try:
        return traffic_workbench.open_platform_login_window(platform)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/settings")
def get_settings() -> dict[str, object]:
    return views.get_settings()


@router.put("/settings")
def update_settings(payload: SettingsUpdate) -> dict[str, object]:
    return views.update_settings(payload.values)


@router.get("/settings/env-check")
def env_check() -> dict[str, object]:
    return views.environment_check()


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
