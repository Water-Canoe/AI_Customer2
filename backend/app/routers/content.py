from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.schemas import (
    ContentAssetUpdate,
    ContentScriptRequest,
    ContentSettingsUpdate,
    ContentSocialMetadataRequest,
    ContentTermsRequest,
    ContentVideoJobCreate,
)
from app.services import content_assets, content_workbench


router = APIRouter(prefix="/api/content", tags=["content"])


@router.post("/assets/import")
async def import_assets(files: list[UploadFile] = File(...)) -> list[dict[str, object]]:
    results = []
    for file in files:
        try:
            file.file.seek(0)
            results.append(
                content_assets.import_asset_file(
                    str(file.filename or ""),
                    file.file,
                    str(file.content_type or ""),
                )
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            await file.close()
    return results


@router.get("/assets")
def list_assets(asset_type: str = "", search: str = "") -> list[dict[str, object]]:
    try:
        return content_assets.list_assets(asset_type, search)
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


@router.post("/video-jobs")
def create_video_job(payload: ContentVideoJobCreate) -> dict[str, object]:
    try:
        return content_workbench.create_video_job(
            payload.params,
            payload.asset_ids,
            payload.audio_asset_id,
            payload.bgm_asset_id,
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


@router.post("/video-jobs/{video_job_id}/publish")
def publish_video_job(video_job_id: str) -> dict[str, object]:
    try:
        return content_workbench.publish_video_job(video_job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@router.get("/voices")
def list_voices(provider: str = "edge") -> list[str]:
    try:
        return content_workbench.list_voices(provider)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
