from __future__ import annotations

import hashlib
import mimetypes
import os
import uuid
from io import BufferedIOBase
from pathlib import Path
from typing import Any, BinaryIO

from app import database


ASSET_EXTENSIONS = {
    "video": {".mp4", ".mov", ".avi", ".flv", ".mkv", ".webm"},
    "image": {".jpg", ".jpeg", ".png", ".bmp", ".webp"},
    "audio": {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"},
}


def ensure_asset_dirs() -> dict[str, Path]:
    root = database.get_content_assets_root()
    originals = root / "originals"
    thumbnails = root / "thumbnails"
    originals.mkdir(parents=True, exist_ok=True)
    thumbnails.mkdir(parents=True, exist_ok=True)
    return {"root": root, "originals": originals, "thumbnails": thumbnails}


def import_asset_file(filename: str, source: BinaryIO, content_type: str = "") -> dict[str, Any]:
    safe_name = Path(str(filename or "")).name.strip()
    suffix = Path(safe_name).suffix.lower()
    asset_type = _asset_type_for_suffix(suffix)
    if not safe_name or not asset_type:
        raise ValueError("仅支持常见的视频、图片和音频文件")

    dirs = ensure_asset_dirs()
    temp_path = dirs["originals"] / f".{uuid.uuid4().hex}.upload"
    digest = hashlib.sha256()
    size = 0
    try:
        with temp_path.open("wb") as target:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                target.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        if size <= 0:
            raise ValueError("不能导入空文件")

        sha256 = digest.hexdigest()
        with database.connect() as conn:
            duplicate = conn.execute(
                "SELECT * FROM content_assets WHERE sha256 = ?",
                (sha256,),
            ).fetchone()
            if duplicate and duplicate["deleted_at"] is None:
                temp_path.unlink(missing_ok=True)
                result = _format_asset(duplicate)
                result["duplicate"] = True
                return result

            asset_id = str(duplicate["id"]) if duplicate else uuid.uuid4().hex
            relative_path = str(duplicate["relative_path"]) if duplicate else f"originals/{asset_id}{suffix}"
            final_path = resolve_asset_path(relative_path)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temp_path, final_path)

            metadata = _read_metadata(final_path, asset_type, asset_id)
            mime_type = content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
            if duplicate:
                conn.execute(
                    """
                    UPDATE content_assets
                    SET name = ?, asset_type = ?, mime_type = ?, file_size = ?,
                        thumbnail_path = ?, width = ?, height = ?, duration = ?,
                        deleted_at = NULL, updated_at = datetime('now', 'localtime')
                    WHERE id = ?
                    """,
                    (
                        safe_name,
                        asset_type,
                        mime_type,
                        size,
                        metadata["thumbnail_path"],
                        metadata["width"],
                        metadata["height"],
                        metadata["duration"],
                        asset_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO content_assets(
                        id, name, asset_type, relative_path, thumbnail_path,
                        mime_type, file_size, sha256, width, height, duration
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        asset_id,
                        safe_name,
                        asset_type,
                        relative_path,
                        metadata["thumbnail_path"],
                        mime_type,
                        size,
                        sha256,
                        metadata["width"],
                        metadata["height"],
                        metadata["duration"],
                    ),
                )
        return get_asset(asset_id)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def list_assets(asset_type: str = "", search: str = "") -> list[dict[str, Any]]:
    conditions = ["deleted_at IS NULL"]
    params: list[Any] = []
    clean_type = str(asset_type or "").strip()
    if clean_type:
        if clean_type not in ASSET_EXTENSIONS:
            raise ValueError("未知资产类型")
        conditions.append("asset_type = ?")
        params.append(clean_type)
    clean_search = str(search or "").strip()
    if clean_search:
        conditions.append("name LIKE ?")
        params.append(f"%{clean_search}%")
    with database.connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM content_assets WHERE {' AND '.join(conditions)} ORDER BY created_at DESC, id DESC",
            params,
        ).fetchall()
    return [_format_asset(row) for row in rows]


def get_asset(asset_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM content_assets WHERE id = ?", (str(asset_id),)).fetchone()
    if not row or (row["deleted_at"] is not None and not include_deleted):
        raise ValueError("内容资产不存在")
    return _format_asset(row)


def rename_asset(asset_id: str, name: str) -> dict[str, Any]:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("资产名称不能为空")
    with database.connect() as conn:
        cursor = conn.execute(
            """
            UPDATE content_assets
            SET name = ?, updated_at = datetime('now', 'localtime')
            WHERE id = ? AND deleted_at IS NULL
            """,
            (clean_name[:200], str(asset_id)),
        )
        if not cursor.rowcount:
            raise ValueError("内容资产不存在")
    return get_asset(asset_id)


def delete_asset(asset_id: str) -> dict[str, Any]:
    asset = get_asset(asset_id)
    with database.connect() as conn:
        active = conn.execute(
            """
            SELECT 1
            FROM video_job_assets link
            JOIN video_jobs job ON job.id = link.video_job_id
            WHERE link.asset_id = ? AND job.status IN ('queued', 'running')
            LIMIT 1
            """,
            (str(asset_id),),
        ).fetchone()
        if active:
            raise RuntimeError("运行中的视频任务正在使用该资产")
        voice_profile = conn.execute(
            "SELECT 1 FROM voice_profiles WHERE reference_asset_id = ? AND deleted_at IS NULL LIMIT 1",
            (str(asset_id),),
        ).fetchone()
        if voice_profile:
            raise RuntimeError("该音频正在被克隆音色使用，请先删除对应音色")
        conn.execute(
            """
            UPDATE content_assets
            SET deleted_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (str(asset_id),),
        )

    resolve_asset_path(asset["relative_path"]).unlink(missing_ok=True)
    if asset.get("thumbnail_path"):
        resolve_asset_path(asset["thumbnail_path"]).unlink(missing_ok=True)
    return {"id": str(asset_id), "deleted": True}


def resolve_asset_path(relative_path: str) -> Path:
    root = database.get_content_assets_root().resolve()
    candidate = (root / str(relative_path or "")).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("资产路径超出内容资产目录")
    return candidate


def _asset_type_for_suffix(suffix: str) -> str:
    for asset_type, extensions in ASSET_EXTENSIONS.items():
        if suffix in extensions:
            return asset_type
    return ""


def _read_metadata(path: Path, asset_type: str, asset_id: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"width": None, "height": None, "duration": None, "thumbnail_path": ""}
    try:
        if asset_type == "image":
            from PIL import Image

            with Image.open(path) as image:
                metadata["width"], metadata["height"] = image.size
                thumbnail = image.convert("RGB")
                thumbnail.thumbnail((480, 270))
                thumbnail_path = ensure_asset_dirs()["thumbnails"] / f"{asset_id}.jpg"
                thumbnail.save(thumbnail_path, "JPEG", quality=85)
                metadata["thumbnail_path"] = f"thumbnails/{asset_id}.jpg"
        elif asset_type == "video":
            from moviepy.video.io.VideoFileClip import VideoFileClip
            from PIL import Image

            with VideoFileClip(str(path)) as clip:
                metadata["width"], metadata["height"] = [int(value) for value in clip.size]
                metadata["duration"] = float(clip.duration or 0)
                frame = clip.get_frame(min(1.0, max(0.0, metadata["duration"] / 2)))
                thumbnail_path = ensure_asset_dirs()["thumbnails"] / f"{asset_id}.jpg"
                Image.fromarray(frame).convert("RGB").save(thumbnail_path, "JPEG", quality=85)
                metadata["thumbnail_path"] = f"thumbnails/{asset_id}.jpg"
        elif asset_type == "audio":
            from moviepy.audio.io.AudioFileClip import AudioFileClip

            with AudioFileClip(str(path)) as clip:
                metadata["duration"] = float(clip.duration or 0)
    except Exception:
        # Metadata is supplementary; the generation engine performs final media validation.
        pass
    return metadata


def _format_asset(row: Any) -> dict[str, Any]:
    data = database.row_to_dict(row) or {}
    asset_id = str(data.get("id") or "")
    data["preview_url"] = f"/api/content/assets/{asset_id}/preview"
    data["thumbnail_url"] = (
        f"/api/content/assets/{asset_id}/thumbnail" if data.get("thumbnail_path") else ""
    )
    data["duplicate"] = False
    return data
