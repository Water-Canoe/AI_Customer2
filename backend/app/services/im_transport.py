"""私信协议共用的认证缓存、用户 ID 校验与结果语义。"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable


def validate_peer(platform: str, value: Any) -> str:
    """在存储与协议发送两个入口统一检查平台用户 ID。"""
    peer = str(value or '').strip()
    if platform == 'dy':
        if not peer.isascii() or not peer.isdecimal() or int(peer) <= 0:
            raise ValueError('请输入有效的抖音数字用户 ID')
    elif platform == 'xhs':
        if len(peer) != 24 or any(char not in '0123456789abcdef' for char in peer):
            raise ValueError('请输入有效的小红书用户 ID（24 位小写十六进制）')
    else:
        raise ValueError('私信收件箱支持抖音和小红书账号')
    return peer


class TransportSendUncertain(RuntimeError):
    """请求已开始但结果未知；保存待确认记录，禁止自动重发。"""
    def __init__(self, result: dict[str, Any]):
        super().__init__('发送结果待确认，请先确认对方是否收到，避免重复发送')
        self.result = result


class AuthCache:
    """按账号和认证快照复用连接；调用方持有账号锁保护在途请求。"""
    def __init__(self) -> None:
        self.values: dict[str, tuple[str, Any]] = {}

    def get(self, account_id: str, credentials: dict, load: Callable) -> Any:
        fingerprint = hashlib.sha256(json.dumps(credentials, sort_keys=True).encode()).hexdigest()
        cached = self.values.get(account_id)
        if cached and cached[0] == fingerprint:
            return cached[1]
        self.close(account_id)
        auth = load(credentials)
        self.values[account_id] = (fingerprint, auth)
        return auth

    def close(self, account_id: str) -> None:
        cached = self.values.pop(account_id, None)
        if cached:
            cached[1].close()
