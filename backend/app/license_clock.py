from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic

from app import device_identity


CLOCK_TOLERANCE = timedelta(minutes=5)
CHECKPOINT_INTERVAL = timedelta(minutes=1)
_lock = threading.RLock()


class ClockError(ValueError):
    """时间记录不可用时要求在线确认，不能退回不受保护的系统时间。"""


@dataclass
class _Clock:
    path: Path
    device_code: str
    observed_at: datetime
    monotonic_at: float
    persisted_at: datetime
    issued_at: datetime
    requires_online: bool = False


_clock: _Clock | None = None


def _write(clock: _Clock) -> None:
    payload = json.dumps({'deviceId': clock.device_code, 'observedAt': clock.observed_at.isoformat(),
                          'issuedAt': clock.issued_at.isoformat(), 'requiresOnline': clock.requires_online}).encode('utf-8')
    try:
        # 与设备身份同目录、独立于业务数据库，复用 DPAPI 和原子替换。
        device_identity._write_protected_secret(clock.path, payload)
    except OSError as exc:
        raise ClockError('无法保存授权时间记录，请检查设备目录权限后重新校验授权') from exc
    clock.persisted_at = clock.observed_at


def _read(path: Path, device_code: str) -> _Clock:
    try:
        payload = json.loads(device_identity._unprotect(path.read_bytes()))
        observed = datetime.fromisoformat(payload['observedAt'])
        issued = datetime.fromisoformat(payload['issuedAt'])
        if payload['deviceId'] != device_code or observed.tzinfo is None or issued.tzinfo is None or type(payload['requiresOnline']) is not bool:
            raise ValueError('时间记录无效或不属于当前设备')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ClockError('授权时间记录缺失或损坏，请联网重新校验授权') from exc
    return _Clock(path, device_code, observed.astimezone(timezone.utc), monotonic(), observed,
                  issued.astimezone(timezone.utc), payload['requiresOnline'])


def record_online(device_code: str, issued_at: datetime) -> None:
    """只在服务器签名和租约字段校验通过后建立时间基准。"""
    global _clock
    wall = datetime.now(timezone.utc)
    if abs(wall - issued_at) > CLOCK_TOLERANCE:
        raise ClockError('授权服务未返回当前时间的租约，请校准系统时间后重新校验授权')
    with _lock:
        path = device_identity.identity_path().with_name('license_clock.bin')
        previous = _clock
        if previous is None or previous.path != path or previous.device_code != device_code:
            try:
                previous = _read(path, device_code)
            except ClockError:
                # 有效的新服务器响应可以重新建立缺失或损坏的记录。
                previous = None
        tick = monotonic()
        if previous:
            expected = previous.observed_at + timedelta(seconds=tick - previous.monotonic_at)
            if (previous.requires_online or wall + CLOCK_TOLERANCE < expected) and issued_at <= previous.issued_at:
                raise ClockError('系统时间异常后必须获取新签发的租约，不能重放旧授权响应')
        clock = _Clock(path, device_code, max(wall, issued_at), tick, wall,
                       max(issued_at, previous.issued_at) if previous else issued_at)
        _write(clock)
        _clock = clock


def now(device_code: str) -> datetime:
    """合并持久化高水位与单调计时，时间回拨不能延长离线租约。"""
    global _clock
    path = device_identity.identity_path().with_name('license_clock.bin')
    with _lock:
        if _clock is None or _clock.path != path or _clock.device_code != device_code:
            _clock = _read(path, device_code)
        if _clock.requires_online:
            raise ClockError('检测到系统时间回拨，请校准时间并联网重新校验授权')
        tick = monotonic()
        expected = _clock.observed_at + timedelta(seconds=tick - _clock.monotonic_at)
        wall = datetime.now(timezone.utc)
        if wall + CLOCK_TOLERANCE < expected:
            _clock.observed_at, _clock.monotonic_at, _clock.requires_online = expected, tick, True
            _write(_clock)
            raise ClockError('检测到系统时间回拨，请校准时间并联网重新校验授权')
        _clock.observed_at = max(wall, expected)
        _clock.monotonic_at = tick
        if _clock.observed_at - _clock.persisted_at >= CHECKPOINT_INTERVAL:
            _write(_clock)
        # shortcut: 本机管理员仍可恢复整机快照；更强保证需要云端或硬件可信时间。
        return _clock.observed_at
