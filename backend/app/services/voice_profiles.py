from __future__ import annotations

import uuid
from typing import Any

from app import database
from app.services import content_assets


# 音色档案保持供应商无关，后续数字人和其他克隆引擎共用同一数据入口。
SUPPORTED_PROVIDERS = {"voxcpm2"}


def list_profiles(provider: str = "") -> list[dict[str, Any]]:
    where = "deleted_at IS NULL"
    params: list[Any] = []
    if provider:
        where += " AND provider = ?"
        params.append(provider)
    with database.connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM voice_profiles WHERE {where} ORDER BY created_at DESC, id DESC",
            params,
        ).fetchall()
    return [_format_profile(row) for row in rows]


def get_profile(profile_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM voice_profiles WHERE id = ?", (str(profile_id),)).fetchone()
    if not row or (row["deleted_at"] is not None and not include_deleted):
        raise ValueError("克隆音色不存在")
    return _format_profile(row)


def create_profile(payload: dict[str, Any]) -> dict[str, Any]:
    provider = str(payload.get("provider") or "voxcpm2").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError("暂不支持该音色克隆引擎")
    if not payload.get("consent_confirmed"):
        raise ValueError("必须确认已获得参考声音的使用授权")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("音色名称不能为空")
    asset = content_assets.get_asset(str(payload.get("reference_asset_id") or ""))
    if asset["asset_type"] != "audio":
        raise ValueError("克隆音色只能使用音频资产")
    profile_id = uuid.uuid4().hex
    with database.connect() as conn:
        conn.execute(
            "UPDATE content_assets SET purpose = 'voice_reference', updated_at = datetime('now', 'localtime') WHERE id = ?",
            (str(asset["id"]),),
        )
        conn.execute(
            """
            INSERT INTO voice_profiles(
                id, name, provider, reference_asset_id, prompt_text, style_prompt,
                consent_confirmed, consent_confirmed_at
            ) VALUES(?, ?, ?, ?, ?, ?, 1, datetime('now', 'localtime'))
            """,
            (
                profile_id,
                name,
                provider,
                str(asset["id"]),
                str(payload.get("prompt_text") or "").strip(),
                str(payload.get("style_prompt") or "").strip(),
            ),
        )
    return get_profile(profile_id)


def update_profile(profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    profile = get_profile(profile_id)
    values = {
        "name": str(payload.get("name") if payload.get("name") is not None else profile["name"]).strip(),
        "prompt_text": str(payload.get("prompt_text") if payload.get("prompt_text") is not None else profile["prompt_text"]).strip(),
        "style_prompt": str(payload.get("style_prompt") if payload.get("style_prompt") is not None else profile["style_prompt"]).strip(),
    }
    if not values["name"]:
        raise ValueError("音色名称不能为空")
    with database.connect() as conn:
        conn.execute(
            """
            UPDATE voice_profiles
            SET name = ?, prompt_text = ?, style_prompt = ?, updated_at = datetime('now', 'localtime')
            WHERE id = ? AND deleted_at IS NULL
            """,
            (values["name"], values["prompt_text"], values["style_prompt"], profile_id),
        )
    return get_profile(profile_id)


def delete_profile(profile_id: str) -> dict[str, Any]:
    get_profile(profile_id)
    voice_name = f"voxcpm2:{profile_id}"
    with database.connect() as conn:
        active = conn.execute(
            """
            SELECT 1 FROM video_jobs
            WHERE status IN ('queued', 'running') AND json_extract(params, '$.voice_name') = ?
            LIMIT 1
            """,
            (voice_name,),
        ).fetchone()
        if active:
            raise RuntimeError("运行中的视频任务正在使用该音色")
        conn.execute(
            """
            UPDATE voice_profiles
            SET deleted_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (profile_id,),
        )
    return {"id": profile_id, "deleted": True}


def _format_profile(row: Any) -> dict[str, Any]:
    item = database.row_to_dict(row) or {}
    item["consent_confirmed"] = bool(item.get("consent_confirmed"))
    item["reference_asset"] = content_assets.get_asset(str(item["reference_asset_id"]), include_deleted=True)
    return item
