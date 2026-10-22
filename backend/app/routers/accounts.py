from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api_dependencies import require_license
from app.schemas import PlatformAccountCreate, PlatformAccountResponse, PlatformAccountUpdate, RuntimeJobResponse
from app.services import account_center, job_queue, message_inbox


router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def _ensure_im_idle(account_id: str) -> None:
    # 账号维护不能替换仍在收发私信的会话认证。
    if account_id in message_inbox.active_accounts():
        raise HTTPException(status_code=409, detail="请先断开该账号的私信连接并等待发送结束，再操作账号")
    message_inbox.release_send_session(account_id)


@router.get("", response_model=list[PlatformAccountResponse])
def list_accounts(feature: str = "", platform: str = "") -> list[dict[str, object]]:
    try:
        return account_center.list_accounts(feature=feature, platform=platform)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", response_model=PlatformAccountResponse)
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


@router.patch("/{account_id}", response_model=PlatformAccountResponse)
def update_account(account_id: str, payload: PlatformAccountUpdate) -> dict[str, object]:
    require_license()
    try:
        values = payload.model_dump(exclude_none=True)
        current = account_center.get_account(account_id)
        # 单纯改名不替换会话；身份、启用状态或功能变动仍需账号空闲。
        if any(value != current.get(key) for key, value in values.items() if key != 'name'):
            _ensure_im_idle(account_id)
        return account_center.update_account(account_id, values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{account_id}")
def delete_account(account_id: str) -> dict[str, object]:
    require_license()
    _ensure_im_idle(account_id)
    try:
        return account_center.delete_account(account_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{account_id}/login", response_model=RuntimeJobResponse)
def login_account(account_id: str, login_kind: Literal["user", "creator"] = "user") -> dict[str, object]:
    require_license()
    try:
        # 入队不改变在线状态；任务真正执行时才暂停私信并标记登录中。
        account_center.require_login_kind(account_id, login_kind)
        return job_queue.enqueue_account_job(account_id, "login", login_kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{account_id}/check", response_model=RuntimeJobResponse)
def check_account(account_id: str, login_kind: Literal["user", "creator"] = "user") -> dict[str, object]:
    require_license()
    _ensure_im_idle(account_id)
    try:
        account_center.set_login_status(account_id, login_kind, "checking")
        return job_queue.enqueue_account_job(account_id, "check", login_kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{account_id}/qrcode")
def account_qrcode(account_id: str) -> FileResponse:
    try:
        return FileResponse(account_center.account_qrcode_path(account_id), media_type="image/png")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
