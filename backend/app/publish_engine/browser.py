from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from app.publish_engine.utils.base_social_media import set_init_script


def profile_has_state(account_file: str | Path) -> bool:
    """判断持久化 Profile 是否已经由浏览器写入过状态。"""
    path = Path(account_file)
    return path.is_dir() and next(path.iterdir(), None) is not None


async def run_login_session(
    context: Any,
    operation: Any,
    *,
    wait_for_close: bool = True,
    check_cancel: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """确认登录后等待用户关窗；提前关窗报错，失败或取消时释放上下文。"""
    closed = asyncio.Event()
    context.on("close", lambda _: closed.set())
    login_task = asyncio.create_task(operation)
    close_task = asyncio.create_task(closed.wait())
    try:
        while not login_task.done() and not closed.is_set():
            if check_cancel:
                check_cancel()
            await asyncio.wait({login_task, close_task}, timeout=0.25, return_when=asyncio.FIRST_COMPLETED)
        if closed.is_set():
            raise RuntimeError("登录窗口已关闭，尚未确认登录成功，请重新登录")
        result = await login_task
        if result.get("success") and wait_for_close:
            # 保持任务及 Profile 占用，平台“保存账号”等后续操作由用户完成。
            while not closed.is_set():
                if check_cancel:
                    check_cancel()
                await asyncio.wait({close_task}, timeout=0.25)
        return result
    finally:
        for task in (login_task, close_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(login_task, close_task, return_exceptions=True)
        # CloakBrowser 的 close 封装还负责停止驱动，用户关窗后也须执行。
        await context.close()


async def launch_publish_context(
    *,
    headless: bool,
    account_file: str | Path | None = None,
    permissions: list[str] | None = None,
) -> Any:
    """使用项目统一的 CloakBrowser 内核创建发布上下文。"""
    try:
        from cloakbrowser import launch_context_async, launch_persistent_context_async
    except ImportError as exc:
        raise RuntimeError("缺少 CloakBrowser 依赖，请先执行发布环境检查") from exc

    context_options: dict[str, Any] = {}
    if permissions:
        context_options["permissions"] = permissions

    launch_options = {
        "headless": headless,
        "viewport": None if not headless else {"width": 1440, "height": 900},
        "locale": "zh-CN",
        "args": ["--start-maximized"] if not headless else [],
        **context_options,
    }
    if account_file:
        profile_dir = Path(account_file)
        profile_dir.mkdir(parents=True, exist_ok=True)
        context = await launch_persistent_context_async(str(profile_dir), **launch_options)
    else:
        context = await launch_context_async(**launch_options)
    try:
        return await set_init_script(context)
    except BaseException:
        await context.close()
        raise
