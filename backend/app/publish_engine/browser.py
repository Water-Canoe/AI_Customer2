from __future__ import annotations

from pathlib import Path
from typing import Any

from app.publish_engine.utils.base_social_media import set_init_script


async def launch_publish_context(
    *,
    headless: bool,
    account_file: str | Path | None = None,
    permissions: list[str] | None = None,
) -> Any:
    """使用项目统一的 CloakBrowser 内核创建发布上下文。"""
    try:
        from cloakbrowser import launch_context_async
    except ImportError as exc:
        raise RuntimeError("缺少 CloakBrowser 依赖，请先执行发布环境检查") from exc

    context_options: dict[str, Any] = {}
    if account_file:
        context_options["storage_state"] = str(account_file)
    if permissions:
        context_options["permissions"] = permissions

    context = await launch_context_async(
        headless=headless,
        viewport=None if not headless else {"width": 1440, "height": 900},
        locale="zh-CN",
        args=["--start-maximized"] if not headless else [],
        **context_options,
    )
    try:
        return await set_init_script(context)
    except BaseException:
        await context.close()
        raise
