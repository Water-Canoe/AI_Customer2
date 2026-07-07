from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DOUYIN_HOSTS = {"www.douyin.com", "douyin.com"}
DEFAULT_PROFILE_DIR = Path(__file__).resolve().parent / "runtime" / "cloak_profile"
DEFAULT_WAIT_SECONDS = 300
DM_PANEL_WAIT_SECONDS = 25

PROFILE_DM_SELECTORS = [
    "button:has-text('发私信')",
    "button:has-text('私信')",
    "div[role='button']:has-text('发私信')",
    "div[role='button']:has-text('私信')",
    "span:has-text('发私信')",
    "span:has-text('私信')",
    "a:has-text('发私信')",
    "a:has-text('私信')",
]

CHAT_INPUT_SELECTORS = [
    ".DraftEditor-root [contenteditable='true']",
    ".DraftEditor-editor [contenteditable='true']",
    "[data-contents='true']",
    "[contenteditable='true']",
    "textarea",
]

SEND_SELECTORS = [
    "button:has-text('发送')",
    "div[role='button']:has-text('发送')",
    "span:has-text('发送')",
]

def validate_douyin_user_url(value: str) -> str:
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("主页 URL 必须以 http:// 或 https:// 开头")
    if parsed.netloc not in DOUYIN_HOSTS:
        raise ValueError("只允许 douyin.com 用户主页 URL")
    if not parsed.path.startswith("/user/"):
        raise ValueError("URL 必须是抖音用户主页，例如 https://www.douyin.com/user/...")
    return url


def normalize_message(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("私信话术不能为空")
    if len(text) > 500:
        raise ValueError("私信话术最多 500 个字符")
    return text


async def first_visible(page: Any, selectors: list[str], timeout_ms: int = 700) -> Any | None:
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            for index in range(count):
                candidate = locator.nth(index)
                if await candidate.is_visible(timeout=timeout_ms):
                    return candidate
        except Exception:
            continue
    return None


async def wait_for_private_message_button(page: Any, seconds: int) -> Any:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        button = await first_visible(page, PROFILE_DM_SELECTORS)
        if button:
            return button
        await dismiss_easy_popups(page)
        await page.wait_for_timeout(3000)
    raise RuntimeError("等待私信按钮超时。请确认已登录，且目标主页允许私信。")


async def dismiss_easy_popups(page: Any) -> None:
    # 只处理明显遮挡主页操作的轻量弹窗，不做验证码/登录绕过。
    for selector in [
        "button:has-text('稍后再说')",
        "button:has-text('以后再说')",
        "button:has-text('我知道了')",
        "div[role='button']:has-text('稍后再说')",
        "div[role='button']:has-text('以后再说')",
        "div[role='button']:has-text('我知道了')",
    ]:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=300):
                await locator.click()
                return
        except Exception:
            continue


async def click_private_message_button(page: Any, seconds: int) -> None:
    await wait_for_private_message_button(page, seconds)

    for selector in ["button:has-text('发私信')", "button:has-text('私信')"]:
        locator = page.locator(selector)
        for index in range(await locator.count() - 1, -1, -1):
            button = locator.nth(index)
            try:
                if await button.is_visible(timeout=500):
                    # ponytail: force avoids Douyin's duplicate hidden button and actionability stalls.
                    await button.click(timeout=5000, force=True)
                    return
            except Exception:
                continue
    raise RuntimeError("找到了私信按钮，但点击失败。")


async def open_dm_panel(page: Any, seconds: int) -> None:
    await click_private_message_button(page, seconds)

    deadline = time.monotonic() + min(seconds, DM_PANEL_WAIT_SECONDS)
    while time.monotonic() < deadline:
        editor = await first_visible(page, CHAT_INPUT_SELECTORS, timeout_ms=1200)
        if editor:
            return
        await page.wait_for_timeout(1500)
    raise RuntimeError("已点击私信按钮，但没有找到聊天输入框。")


async def type_message(page: Any, message: str) -> None:
    editor = await first_visible(page, CHAT_INPUT_SELECTORS, timeout_ms=3000)
    if not editor:
        raise RuntimeError("没有找到聊天输入框。")

    await editor.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")

    # Draft.js 需要真实输入事件；先用键盘输入，失败再用粘贴事件补一次。
    await page.keyboard.insert_text(message)
    await page.wait_for_timeout(500)

    typed = await page.evaluate(
        """text => {
            const editors = [
                document.querySelector('.DraftEditor-root [contenteditable="true"]'),
                document.querySelector('.DraftEditor-editor [contenteditable="true"]'),
                document.querySelector('[data-contents="true"]'),
                document.querySelector('[contenteditable="true"]'),
                document.querySelector('textarea')
            ].filter(Boolean);
            return editors.some(el => (el.innerText || el.value || '').includes(text));
        }""",
        message,
    )
    if typed:
        return

    pasted = await page.evaluate(
        """text => {
            const editor =
                document.querySelector('.DraftEditor-root [contenteditable="true"]') ||
                document.querySelector('.DraftEditor-editor [contenteditable="true"]') ||
                document.querySelector('[data-contents="true"]') ||
                document.querySelector('[contenteditable="true"]') ||
                document.querySelector('textarea');
            if (!editor) return false;
            editor.focus();
            const data = new DataTransfer();
            data.setData('text/plain', text);
            const event = new ClipboardEvent('paste', {
                clipboardData: data,
                bubbles: true,
                cancelable: true
            });
            editor.dispatchEvent(event);
            return true;
        }""",
        message,
    )
    if not pasted:
        raise RuntimeError("输入私信话术失败。")


async def click_send(page: Any) -> None:
    button = await first_visible(page, SEND_SELECTORS, timeout_ms=2500)
    if button:
        await button.click()
        return
    await page.keyboard.press("Enter")


async def send_douyin_dm(
    user_url: str,
    message: str,
    *,
    profile_dir: Path = DEFAULT_PROFILE_DIR,
    login_wait_seconds: int = DEFAULT_WAIT_SECONDS,
    dry_run: bool = False,
) -> dict[str, Any]:
    user_url = validate_douyin_user_url(user_url)
    message = normalize_message(message)
    profile_dir.mkdir(parents=True, exist_ok=True)

    try:
        from cloakbrowser import launch_persistent_context_async
    except ImportError as exc:
        raise RuntimeError("当前 Python 环境缺少 cloakbrowser，请先安装 backend/requirements.txt。") from exc

    context = await launch_persistent_context_async(
        str(profile_dir),
        headless=False,
        locale="zh-CN",
        timezone="Asia/Shanghai",
        viewport=None,
        humanize=True,
        human_preset="careful",
        args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
    )

    page = context.pages[0] if context.pages else await context.new_page()
    try:
        await page.goto(user_url, wait_until="domcontentloaded", timeout=60000)
        await open_dm_panel(page, login_wait_seconds)
        await type_message(page, message)
        if not dry_run:
            await click_send(page)
            await page.wait_for_timeout(1200)
        return {
            "ok": True,
            "sent": not dry_run,
            "url": user_url,
            "message": message,
            "note": "已发送" if not dry_run else "已输入话术，未点击发送",
        }
    finally:
        await context.close()


def run_self_check() -> None:
    assert validate_douyin_user_url("https://www.douyin.com/user/abc") == "https://www.douyin.com/user/abc"
    assert normalize_message("  hi  ") == "hi"
    for bad_url in ["https://example.com/user/abc", "https://www.douyin.com/video/1"]:
        try:
            validate_douyin_user_url(bad_url)
        except ValueError:
            continue
        raise AssertionError(f"bad URL accepted: {bad_url}")
    print("self-check ok")


async def main() -> None:
    parser = argparse.ArgumentParser(description="单用户抖音私信自动化测试脚本")
    parser.add_argument("--user-url", help="抖音用户主页 URL")
    parser.add_argument("--message", help="要发送的私信话术")
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR, help="CloakBrowser 持久化登录目录")
    parser.add_argument("--login-wait-seconds", type=int, default=DEFAULT_WAIT_SECONDS, help="等待手动登录和页面加载的秒数")
    parser.add_argument("--dry-run", action="store_true", help="只打开并输入话术，不点击发送")
    parser.add_argument("--self-check", action="store_true", help="运行不依赖浏览器的脚本自检")
    args = parser.parse_args()

    if args.self_check:
        run_self_check()
        return
    if not args.user_url or not args.message:
        parser.error("--user-url 和 --message 必填，除非使用 --self-check")

    result = await send_douyin_dm(
        args.user_url,
        args.message,
        profile_dir=args.profile_dir,
        login_wait_seconds=args.login_wait_seconds,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
