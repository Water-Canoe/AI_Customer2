"""新抖音私信的独立扫码会话；浏览器认证与旧回滚材料保持独立。"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from app import protected_settings
from app.services import account_center


@dataclass
class _Login:
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    state: str = 'starting'
    image_base64: str = ''
    verification_url: str = ''
    error: str = ''


_logins: dict[str, _Login] = {}
_lock = threading.RLock()


def _account(account_id: str) -> dict[str, Any]:
    account = account_center.get_account(account_id, feature='message')
    if account['platform'] != 'dy':
        raise ValueError('独立私信扫码仅适用于抖音账号')
    return account


def _marker_path(account_id: str):
    _account(account_id)
    return account_center.account_auth_path(account_id, 'user').parent / 'im-sdk-session.dpapi'


def _read_marker(account_id: str) -> dict[str, Any]:
    """只读取本业务账号绑定的 UID，不从旧浏览器 Cookie 推断身份。"""
    path = _marker_path(account_id)
    if not path.is_file():
        raise ValueError('请先点击“扫码登录私信”，首次接入需要重新扫码')
    try:
        value = json.loads(protected_settings.unprotect(path.read_text(encoding='utf-8')))
    except (OSError, ValueError) as exc:
        raise ValueError('私信账号绑定无法读取，请重新扫码') from exc
    if (not isinstance(value, dict) or value.get('account_id') != account_id
            or value.get('sdk') != 'douyin-im' or not str(value.get('sdk_uid') or '').isdigit()):
        raise ValueError('私信账号绑定与所选账号不匹配，请重新扫码')
    return value


def login_status(account_id: str) -> dict[str, str]:
    """二维码和验证地址只留在内存，不写入日志或账号数据库。"""
    _account(account_id)
    with _lock:
        login = _logins.get(account_id)
        return {
            'state': login.state if login else 'idle',
            'image_base64': login.image_base64 if login else '',
            'verification_url': login.verification_url if login else '',
            'error': login.error if login else '',
        }


def session_status(account_id: str) -> dict[str, Any]:
    from app.services import douyin_sdk_transport

    result = {'receive_ready': False, 'send_ready': False, 'synced_at': '',
              'error': '', 'sdk_login': login_status(account_id)}
    try:
        marker = _read_marker(account_id)
        saved = douyin_sdk_transport.session_status(account_id)
        if not saved.get('saved') or str(saved.get('uid') or '') != marker['sdk_uid']:
            raise ValueError('私信认证与绑定账号不一致或已失效，请重新扫码原抖音账号')
        with _lock:
            login = _logins.get(account_id)
            scanning = bool(login and login.thread and login.thread.is_alive())
        if not scanning and saved.get('state') in {'connected', 'reconnecting'}:
            # 已恢复的当前连接优先于历史扫码结果，避免验证结束后重现旧错误。
            result['sdk_login'] = {'state': 'online', 'image_base64': '', 'verification_url': '', 'error': ''}
        verification = saved.get('verification') or {}
        if verification.get('url') or verification.get('error'):
            # 在线续期继续原验证请求，不能让用户重新扫码并销毁当前会话。
            result['sdk_login'] = {
                'state': 'verification' if verification.get('url') else 'error',
                'image_base64': '', 'verification_url': str(verification.get('url') or ''),
                'error': str(verification.get('error') or ''),
                'operation': str(verification.get('operation') or ''),
            }
        result.update(receive_ready=True, send_ready=True, synced_at=marker['synced_at'])
    except ValueError as exc:
        result['error'] = str(exc)
    return result


def load_credentials(account_id: str) -> dict[str, Any]:
    """业务层只传递绑定标记；真实凭据由 SDK 的受限账号目录读取。"""
    marker = _read_marker(account_id)
    status = session_status(account_id)
    if not status['receive_ready']:
        raise ValueError(status['error'])
    return marker


def start_login(account_id: str, login_session: Callable) -> dict[str, str]:
    """后台等待扫码，复用本账号现有登录互斥，其他账号继续工作。"""
    account = _account(account_id)
    if not account['enabled']:
        raise ValueError('请先启用该账号，再扫码登录私信')
    with _lock:
        existing = _logins.get(account_id)
        if existing and existing.thread and existing.thread.is_alive():
            return login_status(account_id)
        login = _Login()
        _logins[account_id] = login

        def run() -> None:
            from app.services import douyin_sdk_transport

            try:
                with login_session(account_id, 'user', login.stop.is_set):
                    path = _marker_path(account_id)
                    previous = _read_marker(account_id) if path.is_file() else None

                    def event(value: dict[str, Any]) -> None:
                        with _lock:
                            kind = value.get('event') or value.get('type') or value.get('state')
                            if kind == 'qrcode':
                                login.state = 'qrcode'
                                login.image_base64 = str(value.get('image_base64') or '')
                                login.verification_url = ''
                            elif kind == 'verification':
                                login.state = 'verification'
                                login.image_base64 = ''
                                login.verification_url = str(value.get('url') or '')
                            elif kind in {'verification_finished', 'verification_failed'}:
                                login.verification_url = ''
                                login.state = 'starting'

                    credentials = douyin_sdk_transport.login(
                        account_id, event, login.stop,
                        expected_uid=previous['sdk_uid'] if previous else '')
                    uid = str(credentials.get('sdk_uid') or '')
                    if not uid.isdigit():
                        raise ValueError('平台没有返回有效私信账号身份，请重新扫码')
                    if previous and previous['sdk_uid'] != uid:
                        raise ValueError('扫码账号与原绑定不一致，请使用原抖音账号重新扫码')
                    marker = {'account_id': account_id, 'sdk': 'douyin-im', 'sdk_uid': uid,
                              'synced_at': datetime.now().isoformat(timespec='seconds')}
                    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
                    temporary.write_text(protected_settings.protect(json.dumps(marker)), encoding='utf-8')
                    temporary.replace(path)
                    # 只确认私信已登录，不能据此认定采集、引流浏览器也已登录。
                    account_center.set_feature_status(account_id, 'message', 'ready')
                with _lock:
                    login.state = 'online'
            except Exception as exc:
                with _lock:
                    login.state = 'cancelled' if login.stop.is_set() else 'error'
                    login.error = '' if login.stop.is_set() else (
                        str(exc) if isinstance(exc, ValueError) else f'私信扫码失败（{type(exc).__name__}），请重试')
            finally:
                with _lock:
                    login.image_base64 = login.verification_url = ''

        login.thread = threading.Thread(target=run, name=f'douyin-sdk-login-{account_id}', daemon=True)
        login.thread.start()
    return login_status(account_id)


def cancel_login(account_id: str) -> dict[str, str]:
    _account(account_id)
    scanning = False
    with _lock:
        login = _logins.get(account_id)
        if login and login.thread and login.thread.is_alive():
            scanning = True
            login.stop.set()
    if not scanning:
        from app.services import douyin_sdk_transport
        # 没有扫码任务时只取消当前安全挑战，保留独立私信连接。
        douyin_sdk_transport.cancel_verification(account_id)
    return login_status(account_id)


def shutdown() -> None:
    """退出时取消扫码等待，避免遗留后台 SDK 进程。"""
    with _lock:
        pending = list(_logins.values())
        for login in pending:
            login.stop.set()
    for login in pending:
        if login.thread and login.thread is not threading.current_thread():
            login.thread.join(timeout=5)
