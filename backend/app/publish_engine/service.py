from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


StageCallback = Callable[[str], None]
CancelCheck = Callable[[], bool]


class PublishCancelled(RuntimeError):
    pass


class PublishReviewRequired(RuntimeError):
    """平台可能已经收到发布请求，需要人工确认后再决定是否重试。"""


def run(coroutine: Any) -> Any:
    """在同步任务线程中运行发布器协程。"""
    return asyncio.run(coroutine)


async def login_account(
    platform: str,
    account_file: Path,
    qrcode_callback: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    setup, _ = _account_handlers(platform)
    account_file.parent.mkdir(parents=True, exist_ok=True)
    return await setup(
        str(account_file),
        handle=True,
        return_detail=True,
        qrcode_callback=qrcode_callback,
        headless=False,
    )


async def check_account(platform: str, account_file: Path) -> bool:
    if not account_file.is_file():
        return False
    _, checker = _account_handlers(platform)
    return bool(await checker(str(account_file)))


async def publish(
    *,
    platform: str,
    content_type: str,
    account_file: Path,
    title: str,
    description: str,
    tags: list[str],
    media_paths: list[Path],
    publish_strategy: str = "immediate",
    publish_date: datetime | int = 0,
    options: dict[str, Any] | None = None,
    stage_callback: StageCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict[str, Any]:
    options = options or {}
    _stage(stage_callback, "preparing")
    _check_cancel(cancel_check)
    if not account_file.is_file():
        raise ValueError("发布账号登录状态不存在，请先扫码登录")
    if content_type not in {"video", "note"}:
        raise ValueError("发布内容类型必须是 video 或 note")
    if content_type == "video" and len(media_paths) != 1:
        raise ValueError("视频发布必须且只能选择一个视频")
    if content_type == "note" and not media_paths:
        raise ValueError("图文发布至少需要一张图片")

    app = _build_uploader(
        platform=platform,
        content_type=content_type,
        account_file=account_file,
        title=title,
        description=description,
        tags=tags,
        media_paths=media_paths,
        publish_strategy=publish_strategy,
        publish_date=publish_date,
        options=options,
    )
    app.submitted = False

    def before_submit() -> None:
        _check_cancel(cancel_check)
        _stage(stage_callback, "publishing")

    app.before_submit = before_submit
    _stage(stage_callback, "uploading")
    try:
        await app.main()
    except PublishCancelled:
        raise
    except Exception as exc:
        if bool(getattr(app, "submitted", False)):
            raise PublishReviewRequired(str(exc)) from exc
        raise
    _stage(stage_callback, "completed")
    return {"success": True, "platform": platform, "content_type": content_type}


def _account_handlers(platform: str) -> tuple[Any, Any]:
    if platform == "dy":
        from app.publish_engine.uploader.douyin_uploader.main import cookie_auth, douyin_setup

        return douyin_setup, cookie_auth
    if platform == "ks":
        from app.publish_engine.uploader.ks_uploader.main import cookie_auth, ks_setup

        return ks_setup, cookie_auth
    if platform == "xhs":
        from app.publish_engine.uploader.xiaohongshu_uploader.main import cookie_auth, xiaohongshu_setup

        return xiaohongshu_setup, cookie_auth
    raise ValueError(f"不支持的发布平台：{platform}")


def _build_uploader(**values: Any) -> Any:
    platform = values["platform"]
    content_type = values["content_type"]
    common = {
        "title": values["title"],
        "tags": values["tags"],
        "publish_date": values["publish_date"],
        "account_file": str(values["account_file"]),
        "publish_strategy": values["publish_strategy"],
        "debug": False,
        "headless": True,
    }
    paths = [str(path) for path in values["media_paths"]]
    options = values["options"]
    description = values["description"]

    if platform == "dy":
        from app.publish_engine.uploader.douyin_uploader.main import DouYinNote, DouYinVideo

        if content_type == "video":
            return DouYinVideo(
                file_path=paths[0],
                desc=description,
                thumbnail_landscape_path=options.get("thumbnail_landscape_path"),
                thumbnail_portrait_path=options.get("thumbnail_portrait_path"),
                productLink=options.get("product_link", ""),
                productTitle=options.get("product_title", ""),
                **common,
            )
        return DouYinNote(image_paths=paths, note=description, bgm=options.get("bgm", ""), **common)
    if platform == "ks":
        from app.publish_engine.uploader.ks_uploader.main import KSNote, KSVideo

        if content_type == "video":
            return KSVideo(file_path=paths[0], desc=description, thumbnail_path=options.get("thumbnail_path"), **common)
        return KSNote(image_paths=paths, note=description, **common)
    if platform == "xhs":
        from app.publish_engine.uploader.xiaohongshu_uploader.main import XiaoHongShuNote, XiaoHongShuVideo

        if content_type == "video":
            return XiaoHongShuVideo(file_path=paths[0], desc=description, thumbnail_path=options.get("thumbnail_path"), **common)
        return XiaoHongShuNote(image_paths=paths, note=description, desc=description, **common)
    raise ValueError(f"不支持的发布平台：{platform}")


def _stage(callback: StageCallback | None, stage: str) -> None:
    if callback:
        callback(stage)


def _check_cancel(callback: CancelCheck | None) -> None:
    if callback and callback():
        raise PublishCancelled("用户已取消发布")
