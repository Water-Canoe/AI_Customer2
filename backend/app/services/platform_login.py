from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable

from app.publish_engine.browser import launch_publish_context, profile_has_state


CancelCheck = Callable[[], bool]
LOGIN_URLS = {
    "dy": "https://www.douyin.com/?recommend=1",
    "xhs": "https://www.xiaohongshu.com/explore",
    "ks": "https://www.kuaishou.com/new-reco",
}


class AccountOperationCancelled(RuntimeError):
    pass


async def login_account(
    platform: str,
    profile_dir: Path,
    *,
    cancel_check: CancelCheck | None = None,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    context = await launch_publish_context(headless=False, account_file=profile_dir)
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        initial_cookies = await _cookie_map(context)
        await _open_login_page(page, platform, cancel_check)
        for _ in range(max(1, timeout_seconds)):
            _raise_if_cancelled(cancel_check)
            if await _is_logged_in(platform, context, page, initial_cookies):
                return {"success": True, "message": "用户登录成功", "current_url": page.url}
            await asyncio.sleep(1)
        return {"success": False, "message": "等待用户登录超时", "current_url": page.url}
    finally:
        await context.close()


async def check_account(
    platform: str,
    profile_dir: Path,
    *,
    cancel_check: CancelCheck | None = None,
) -> bool:
    if not profile_has_state(profile_dir):
        return False
    context = await launch_publish_context(headless=True, account_file=profile_dir)
    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await _open_login_page(page, platform, cancel_check)
        _raise_if_cancelled(cancel_check)
        await asyncio.sleep(1)
        return await _is_logged_in(platform, context, page)
    finally:
        await context.close()


async def _open_login_page(page: Any, platform: str, cancel_check: CancelCheck | None = None) -> None:
    url = LOGIN_URLS.get(platform)
    if not url:
        raise ValueError("用户登录只支持抖音、小红书和快手")
    navigation = asyncio.create_task(page.goto(url, wait_until="domcontentloaded", timeout=15_000))
    try:
        while not navigation.done():
            _raise_if_cancelled(cancel_check)
            await asyncio.wait({navigation}, timeout=0.25)
        await navigation
    except AccountOperationCancelled:
        raise
    except Exception:
        # 页面已打开但持续加载时仍允许用户在可见窗口完成登录。
        if not str(page.url or "").startswith("http"):
            raise
    finally:
        if not navigation.done():
            navigation.cancel()
            with suppress(asyncio.CancelledError):
                await navigation


async def _is_logged_in(
    platform: str,
    context: Any,
    page: Any,
    initial_cookies: dict[str, str] | None = None,
) -> bool:
    cookies = await _cookie_map(context)
    if platform == "dy":
        if cookies.get("LOGIN_STATUS") == "1" or cookies.get("sessionid") or cookies.get("sessionid_ss"):
            return True
        try:
            return await page.evaluate("() => window.localStorage.getItem('HasUserLogin') === '1'")
        except Exception:
            return False
    if platform == "xhs":
        try:
            if await page.locator("xpath=//a[contains(@href, '/user/profile/')]//span[text()='我']").count():
                return True
        except Exception:
            pass
        if initial_cookies is None:
            return False
        previous = (initial_cookies or {}).get("web_session", "")
        return bool(cookies.get("web_session") and cookies.get("web_session") != previous)
    if platform == "ks":
        return bool(cookies.get("passToken"))
    raise ValueError("未知平台")


async def _cookie_map(context: Any) -> dict[str, str]:
    return {str(item.get("name") or ""): str(item.get("value") or "") for item in await context.cookies()}


def _raise_if_cancelled(cancel_check: CancelCheck | None) -> None:
    if cancel_check and cancel_check():
        raise AccountOperationCancelled("账号操作已取消")
