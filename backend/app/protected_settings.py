from __future__ import annotations

import base64
from urllib.parse import parse_qsl, urlsplit

from app import device_identity


PROTECTED_KEYS = {'ai_api_key', 'content_video_config'}
PREFIX = 'dpapi:user:'


def protect(value: str) -> str:
    """复用已有 DPAPI 字节封装；凭据限定当前 Windows 用户，设备身份范围不变。"""
    if not value:
        return ''
    return PREFIX + base64.b64encode(device_identity._protect(value.encode('utf-8'), machine_scope=False)).decode('ascii')


def unprotect(value: str) -> str:
    if not value:
        return ''
    if not value.startswith(PREFIX):
        raise ValueError('敏感配置不是当前用户保护格式，请明确重新配置')
    try:
        return device_identity._unprotect(base64.b64decode(value[len(PREFIX):], validate=True)).decode('utf-8')
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError('当前 Windows 用户无法解密敏感配置，请使用原用户恢复或重新配置') from exc


def validate_connection_url(value: str) -> None:
    """模型地址会进入公开摘要与任务记录，凭据必须填写在独立密钥字段。"""
    parsed = urlsplit(value)
    if parsed.username or parsed.password or any(key.lower() in {'key', 'api_key', 'token', 'password', 'secret', 'access_token'} for key, _ in parse_qsl(parsed.query)):
        raise ValueError('模型服务地址不能包含登录凭据或密钥，请使用独立密钥字段')
