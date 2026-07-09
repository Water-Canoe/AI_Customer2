from __future__ import annotations

import json

from fastapi import HTTPException

from app import database
from app.schemas import TaskCreate
from app.services import license_service


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


def apply_own_account_defaults(payload: TaskCreate) -> TaskCreate:
    """Reuse self-account IDs saved in Settings when callers omit them."""
    if payload.mode != "own_account" or payload.creator_id.strip() or payload.specified_id.strip():
        return payload
    with database.connect() as conn:
        raw = database.get_setting(conn, "own_accounts", "{}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return payload
    items = parsed.get(payload.platform, []) if isinstance(parsed, dict) else []
    creator_ids = [str(item).strip() for item in items if str(item).strip()] if isinstance(items, list) else []
    return payload.model_copy(update={"creator_id": ",".join(creator_ids)}) if creator_ids else payload
