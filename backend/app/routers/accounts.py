from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api_dependencies import require_license
from app.schemas import PlatformAccountCreate, PlatformAccountUpdate
from app.services import account_center, content_publish, job_queue


router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("")
def list_accounts(feature: str = "", platform: str = "") -> list[dict[str, object]]:
    try:
        return account_center.list_accounts(feature=feature, platform=platform)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("")
def create_account(payload: PlatformAccountCreate) -> dict[str, object]:
    require_license()
    try:
        return account_center.create_account(
            payload.platform,
            payload.name,
            payload.role,
            payload.features,
            payload.default_features,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{account_id}")
def update_account(account_id: str, payload: PlatformAccountUpdate) -> dict[str, object]:
    require_license()
    try:
        return account_center.update_account(account_id, payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{account_id}")
def delete_account(account_id: str) -> dict[str, object]:
    require_license()
    try:
        return account_center.delete_account(account_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{account_id}/login")
def login_account(account_id: str) -> dict[str, object]:
    require_license()
    try:
        account_center.get_account(account_id)
        content_publish.set_account_state(account_id, "checking")
        account_center.set_all_feature_status(account_id, "checking")
        return job_queue.enqueue_account_job(account_id, "login")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{account_id}/check")
def check_account(account_id: str) -> dict[str, object]:
    require_license()
    try:
        account_center.get_account(account_id)
        content_publish.set_account_state(account_id, "checking")
        account_center.set_all_feature_status(account_id, "checking")
        return job_queue.enqueue_account_job(account_id, "check")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{account_id}/qrcode")
def account_qrcode(account_id: str) -> FileResponse:
    try:
        return FileResponse(content_publish.account_qrcode_path(account_id), media_type="image/png")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
