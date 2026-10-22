"""两个平台共用的账号认证存储与同步入口，凭据只供后台读取。"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from app import protected_settings
from app.services import account_center


def _session_path(account_id: str) -> Path:
    account = account_center.get_account(account_id, feature='message', require_ready=False)
    if account['platform'] not in {'dy', 'xhs'}:
        raise ValueError('私信收件箱支持抖音和小红书账号')
    return account_center.account_auth_path(account_id, 'user').parent / 'im-session.dpapi'


def _cookie_map(cookie_str: str) -> dict[str, str]:
    return dict(token.strip().split('=', 1) for token in cookie_str.split(';') if '=' in token)


def load_credentials(account_id: str) -> dict[str, Any]:
    """从当前账号目录解密，禁止通过接口返回 Cookie 或票据。"""
    path = _session_path(account_id)
    if not path.is_file():
        raise ValueError('请先同步该账号的私信认证')
    try:
        value = json.loads(protected_settings.unprotect(path.read_text(encoding='utf-8')))
    except (OSError, ValueError) as exc:
        raise ValueError('私信认证无法读取，请重新同步') from exc
    if value.get('account_id') != account_id:
        raise ValueError('私信认证与当前账号不匹配，请重新同步')
    return value


def save_credentials(account_id: str, credentials: dict[str, Any]) -> None:
    """原子替换 Windows DPAPI 加密快照，不存整份浏览器数据。"""
    credentials = {**credentials, 'account_id': account_id, 'synced_at': datetime.now().isoformat(timespec='seconds')}
    path = _session_path(account_id)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    temporary.write_text(protected_settings.protect(json.dumps(credentials, ensure_ascii=False)), encoding='utf-8')
    temporary.replace(path)


def invalidate_credentials(account_id: str) -> None:
    """重新登录前作废单账号旧认证，保留浏览器 Profile 和聊天数据。"""
    _session_path(account_id).unlink(missing_ok=True)


def session_status(account_id: str) -> dict[str, Any]:
    account = account_center.get_account(account_id, feature='message', require_ready=False)
    try:
        value = load_credentials(account_id)
        cookies = _cookie_map(str(value.get('cookie_str') or ''))
        if account['platform'] == 'dy':
            receive_ready = bool(cookies.get('sessionid'))
            matched = not cookies.get('bd_ticket_guard_ts_sign_id') or str(value.get('ts_sign') or '').startswith(cookies['bd_ticket_guard_ts_sign_id'])
            send_ready = receive_ready and matched and all(value.get(key) for key in ('ticket', 'ts_sign', 'private_key'))
            error = '' if send_ready else '已读取登录会话，但发送认证材料不完整；请在账号中心重新登录后同步'
            if not receive_ready:
                error = '未读取到有效的抖音用户登录会话，请重新登录'
            return {'receive_ready': receive_ready, 'send_ready': bool(send_ready), 'error': error, 'synced_at': value.get('synced_at', '')}
        ready = bool(cookies.get('a1') and cookies.get('web_session'))
        return {'receive_ready': ready, 'send_ready': ready, 'synced_at': value.get('synced_at', ''),
                'error': '' if ready else '未读取到有效的小红书用户登录会话，请重新登录后同步'}
    except ValueError as exc:
        return {'receive_ready': False, 'send_ready': False, 'error': str(exc), 'synced_at': ''}


async def sync_account(account_id: str, capture_context: Callable[[str, Any], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    """手动或启动同步时复用账号 Profile；日常协议收发不打开浏览器。"""
    from app.publish_engine.browser import launch_publish_context
    from app.services import browser_queue

    account = account_center.get_account(account_id, feature='message', require_ready=True)
    _session_path(account_id)  # 同步前校验支持的平台，拒绝把其它平台账号套进协议。
    url = {'dy': 'https://www.douyin.com/', 'xhs': 'https://www.xiaohongshu.com/'}[account['platform']]
    async with browser_queue.async_browser_slot(f'im_sync:{account_id}'):
        context = await launch_publish_context(headless=True, account_file=account_center.profile_path(account_id, 'message'))
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(url, wait_until='domcontentloaded', timeout=30_000)
            return await capture_context(account_id, context)
        finally:
            await context.close()


async def capture_xhs_context(account_id: str, context: Any) -> dict[str, Any]:
    """导出小红书 HttpOnly Cookie，不依赖聊天页面的 DOM 或 document.cookie。"""
    cookies = await context.cookies('https://www.xiaohongshu.com/')
    values = {str(item['name']): str(item['value']) for item in cookies}
    if not values.get('a1') or not values.get('web_session'):
        raise ValueError('当前 Profile 未包含小红书用户登录会话，请先在账号中心完成用户登录')
    save_credentials(account_id, {'cookie_str': '; '.join(f'{key}={value}' for key, value in values.items())})
    return session_status(account_id)
