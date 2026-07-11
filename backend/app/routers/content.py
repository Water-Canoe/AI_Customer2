from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.schemas import (
    ContentAssetUpdate,
    ContentPublishAccountCreate,
    ContentPublishAccountUpdate,
    ContentPublishOneClick,
    ContentPublishResultUpdate,
    ContentPublishTaskCreate,
    ContentScriptRequest,
    ContentSettingsUpdate,
    ContentSocialMetadataRequest,
    ContentTermsRequest,
    ContentVideoJobCreate,
    ContentVideoJobUpdate,
    ContentVideoOutputUpdate,
    ContentVoiceProfileCreate,
    ContentVoiceProfileUpdate,
)
from app.services import content_assets, content_publish, content_workbench, job_queue, voice_profiles


router = APIRouter(prefix="/api/content", tags=["content"])


@router.post("/assets/import")
async def import_assets(files: list[UploadFile] = File(...), purpose: str = "") -> list[dict[str, object]]:
    results = []
    for file in files:
        try:
            file.file.seek(0)
            results.append(
                content_assets.import_asset_file(
                    str(file.filename or ""),
                    file.file,
                    str(file.content_type or ""),
                    purpose,
                )
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            await file.close()
    return results


@router.get("/assets")
def list_assets(asset_type: str = "", search: str = "", purpose: str = "") -> list[dict[str, object]]:
    try:
        return content_assets.list_assets(asset_type, search, purpose)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str) -> dict[str, object]:
    return _asset_or_404(asset_id)


@router.patch("/assets/{asset_id}")
def rename_asset(asset_id: str, payload: ContentAssetUpdate) -> dict[str, object]:
    try:
        return content_assets.rename_asset(asset_id, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: str) -> dict[str, object]:
    try:
        return content_assets.delete_asset(asset_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/assets/{asset_id}/preview")
def preview_asset(asset_id: str) -> FileResponse:
    asset = _asset_or_404(asset_id)
    path = content_assets.resolve_asset_path(str(asset["relative_path"]))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="资产文件不存在")
    return FileResponse(path, media_type=str(asset.get("mime_type") or "application/octet-stream"))


@router.get("/assets/{asset_id}/thumbnail")
def preview_asset_thumbnail(asset_id: str) -> FileResponse:
    asset = _asset_or_404(asset_id)
    thumbnail = str(asset.get("thumbnail_path") or "")
    if not thumbnail:
        raise HTTPException(status_code=404, detail="资产没有缩略图")
    path = content_assets.resolve_asset_path(thumbnail)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="缩略图不存在")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/publish-accounts")
def list_publish_accounts() -> list[dict[str, object]]:
    return content_publish.list_accounts()


@router.post("/publish-accounts")
def create_publish_account(payload: ContentPublishAccountCreate) -> dict[str, object]:
    try:
        return content_publish.create_account(payload.platform, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/publish-accounts/{account_id}")
def update_publish_account(account_id: str, payload: ContentPublishAccountUpdate) -> dict[str, object]:
    try:
        return content_publish.update_account(account_id, payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/publish-accounts/{account_id}")
def delete_publish_account(account_id: str) -> dict[str, object]:
    try:
        return content_publish.delete_account(account_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/publish-accounts/{account_id}/login")
def login_publish_account(account_id: str) -> dict[str, object]:
    try:
        content_publish.get_account(account_id)
        return job_queue.enqueue_publish_account_job(account_id, "login")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/publish-accounts/{account_id}/check")
def check_publish_account(account_id: str) -> dict[str, object]:
    try:
        content_publish.get_account(account_id)
        return job_queue.enqueue_publish_account_job(account_id, "check")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/publish-accounts/{account_id}/qrcode")
def publish_account_qrcode(account_id: str) -> FileResponse:
    try:
        return FileResponse(content_publish.account_qrcode_path(account_id), media_type="image/png")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/publish-tasks/one-click")
def one_click_publish(payload: ContentPublishOneClick) -> list[dict[str, object]]:
    try:
        source = payload.source
        if source.type == "video_output":
            tasks = content_publish.create_video_output_tasks(
                video_job_id=source.video_job_id,
                output_name=source.output_name,
            )
        else:
            if len(source.asset_ids) != 1:
                raise ValueError("一键发布内容资产时请选择一个视频；多图片图文请使用发布设置")
            asset = content_assets.get_asset(source.asset_ids[0])
            tasks = content_publish.create_asset_tasks(
                asset_ids=source.asset_ids,
                account_ids=None,
                title=str(asset["name"]),
            )
        return content_publish.enqueue_tasks(tasks)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/publish-tasks")
def create_publish_tasks(payload: ContentPublishTaskCreate) -> list[dict[str, object]]:
    try:
        values = payload.model_dump()
        source = values.pop("source")
        if source["type"] == "video_output":
            tasks = content_publish.create_video_output_tasks(
                video_job_id=source["video_job_id"],
                output_name=source["output_name"],
                **values,
            )
        else:
            tasks = content_publish.create_asset_tasks(asset_ids=source["asset_ids"], **values)
        return content_publish.enqueue_tasks(tasks)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/publish-tasks")
def list_publish_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    status: str = "",
) -> dict[str, object]:
    return content_publish.list_tasks(page, page_size, status)


@router.get("/publish-tasks/{task_id}")
def get_publish_task(task_id: str) -> dict[str, object]:
    try:
        return content_publish.get_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/publish-tasks/{task_id}/cancel")
def cancel_publish_task(task_id: str) -> dict[str, object]:
    try:
        return content_publish.cancel_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/publish-tasks/{task_id}/retry")
def retry_publish_task(task_id: str) -> dict[str, object]:
    try:
        return content_publish.retry_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/publish-tasks/{task_id}/mark-result")
def mark_publish_task(task_id: str, payload: ContentPublishResultUpdate) -> dict[str, object]:
    try:
        return content_publish.mark_task_result(task_id, payload.status, payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/video-jobs")
def create_video_job(payload: ContentVideoJobCreate) -> dict[str, object]:
    try:
        return content_workbench.create_video_job(
            payload.params,
            payload.asset_ids,
            payload.audio_asset_id,
            payload.bgm_asset_id,
            payload.publish,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/video-jobs")
def list_video_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    include_archived: bool = False,
) -> dict[str, object]:
    return content_workbench.list_video_jobs(page, page_size, include_archived)


@router.get("/video-jobs/{video_job_id}")
def get_video_job(video_job_id: str) -> dict[str, object]:
    try:
        return content_workbench.get_video_job(video_job_id, include_archived=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/video-jobs/{video_job_id}")
def update_video_job(video_job_id: str, payload: ContentVideoJobUpdate) -> dict[str, object]:
    try:
        return content_workbench.update_video_job(video_job_id, payload.subject)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/video-jobs/{video_job_id}/output-status")
def update_video_output_status(video_job_id: str, payload: ContentVideoOutputUpdate) -> dict[str, object]:
    try:
        return content_workbench.update_video_output_status(video_job_id, payload.output_name, payload.upload_status)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/video-jobs/{video_job_id}/cancel")
def cancel_video_job(video_job_id: str) -> dict[str, object]:
    try:
        return content_workbench.cancel_video_job(video_job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/video-jobs/{video_job_id}/retry")
def retry_video_job(video_job_id: str) -> dict[str, object]:
    try:
        return content_workbench.retry_video_job(video_job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/video-jobs/{video_job_id}")
def archive_video_job(video_job_id: str) -> dict[str, object]:
    try:
        return content_workbench.archive_video_job(video_job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/video-jobs/{video_job_id}/files/{filename}")
def get_video_file(video_job_id: str, filename: str) -> FileResponse:
    try:
        job = content_workbench.get_video_job(video_job_id, include_archived=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    output = next((item for item in job["outputs"] if str(item.get("name") or "") == filename), None)
    if not output:
        raise HTTPException(status_code=404, detail="视频文件不存在")
    try:
        path = content_workbench.resolve_video_output(str(output.get("relative_path") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.post("/scripts")
def generate_script(payload: ContentScriptRequest) -> dict[str, object]:
    try:
        return content_workbench.generate_script(payload.model_dump())
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/terms")
def generate_terms(payload: ContentTermsRequest) -> dict[str, object]:
    try:
        return content_workbench.generate_terms(payload.model_dump())
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/social-metadata")
def generate_social_metadata(payload: ContentSocialMetadataRequest) -> dict[str, object]:
    try:
        return content_workbench.generate_social_metadata(payload.model_dump())
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/voice-reference-script")
def generate_voice_reference_script() -> dict[str, object]:
    try:
        return content_workbench.generate_voice_reference_script()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/voices")
def list_voices(provider: str = "edge") -> list[str]:
    try:
        return content_workbench.list_voices(provider)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/voice-profiles")
def list_voice_profiles(provider: str = "") -> list[dict[str, object]]:
    return voice_profiles.list_profiles(provider)


@router.post("/voice-profiles")
def create_voice_profile(payload: ContentVoiceProfileCreate) -> dict[str, object]:
    try:
        return voice_profiles.create_profile(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/voice-profiles/{profile_id}")
def update_voice_profile(profile_id: str, payload: ContentVoiceProfileUpdate) -> dict[str, object]:
    try:
        return voice_profiles.update_profile(profile_id, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/voice-profiles/{profile_id}")
def delete_voice_profile(profile_id: str) -> dict[str, object]:
    try:
        return voice_profiles.delete_profile(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/settings")
def get_content_settings() -> dict[str, object]:
    return content_workbench.get_settings(mask_secrets=True)


@router.put("/settings")
def update_content_settings(payload: ContentSettingsUpdate) -> dict[str, object]:
    return content_workbench.update_settings(payload.values)


@router.get("/environment-check")
def content_environment_check() -> dict[str, object]:
    return content_workbench.environment_check()


def _asset_or_404(asset_id: str) -> dict[str, object]:
    try:
        return content_assets.get_asset(asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
