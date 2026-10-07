# encoding: utf-8
"""Node runtime adapter for the XHS PC JavaScript algorithms.

Shared cryptographic/signature algorithms live under
:mod:`xhs_utils.xhs_core.js`（唯一来源）；本目录仅保留 PC 特有模板
（profile/rap 等）。This module only validates Python inputs, invokes Node,
and validates outputs; it intentionally does not maintain a second Python
implementation.
"""
from __future__ import annotations

import json
import os
import subprocess
from playwright._impl._driver import compute_driver_executable
from typing import Any, Mapping, Optional


_JS_DIR = os.path.join(os.path.dirname(__file__), 'js')
_CORE_JS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'xhs_core', 'js',
)
_B1_JS = os.path.join(_CORE_JS_DIR, 'b1.js')
_SIGN_JS = os.path.join(_CORE_JS_DIR, 'sign.js')

_TIER_GATE = {
    '0101': ('mns0101_', 205),
    '0201': ('mns0201_', 208),
    '0301': ('mns0301_', 200),
}


def generate_b1(
    options: Optional[Mapping[str, Any]] = None,
    timeout: float = 10.0,
) -> str:
    """Generate b1 from an explicit 19-field browser-state snapshot."""
    if not os.path.isfile(_B1_JS):
        raise RuntimeError(f'b1 algorithm missing: {_B1_JS}')
    payload = json.dumps(dict(options or {}), ensure_ascii=False, separators=(',', ':'))
    try:
        proc = subprocess.run(
            [compute_driver_executable()[0], _B1_JS, '--generate', payload],
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=_JS_DIR,
            encoding='utf-8',
            errors='replace',
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f'b1 runtime failed to start: {exc}') from exc
    if proc.returncode != 0:
        raise RuntimeError(f'b1 runtime failed: {(proc.stderr or proc.stdout).strip()}')
    try:
        value = str(json.loads(proc.stdout.strip())['b1'])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(f'b1 runtime output invalid: {proc.stdout[:300]}') from exc
    if len(value) < 500 or len(value) % 4:
        raise RuntimeError(f'b1 length invalid: {len(value)}')
    return value


def run_signer(
    api: str,
    data: Any = '',
    *,
    a1: str,
    cookie: str,
    b1: Optional[str],
    dsl_pair: str,
    tier: str,
    sign_context: Mapping[str, Any],
    timeout: float = 30.0,
) -> Optional[dict]:
    """Run the JS signing core and enforce the selected MNS tier gate."""
    if not os.path.isfile(_SIGN_JS) or not cookie or 'a1=' not in cookie:
        return None
    if tier not in _TIER_GATE:
        return None
    context = dict(sign_context or {})
    required = ('loadts', 'seq', 'envConst', 'envFpTail', 'webBuild')
    if not all(key in context for key in required):
        return None

    payload = {
        'api': api,
        'data': data,
        'cookie': cookie,
        'a1': a1,
        # Empty b1 is the browser's real cold-login state. ``None`` remains
        # reserved for callers that failed to provide the field explicitly.
        'b1': '' if b1 is None else str(b1),
        'dslPair': dsl_pair,
        'tier': tier,
    }
    for key in (
        'now', 'version', 'loadts', 'seq', 'envConst', 'envFpTail', 'webBuild',
        'signVersion', 'appId', 'platform', 'deviceTag', 'webSsk',
    ):
        if key in context:
            payload[key] = context[key]

    try:
        proc = subprocess.run(
            [compute_driver_executable()[0], _SIGN_JS, json.dumps(payload, ensure_ascii=False)],
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=_JS_DIR,
            encoding='utf-8',
            errors='replace',
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None

    required_prefix, required_length = _TIER_GATE[tier]
    for line in reversed([
        item.strip() for item in (proc.stdout or '').splitlines() if item.strip()
    ]):
        if not line.startswith('{') or '"x3"' not in line:
            continue
        try:
            result = json.loads(line)
        except json.JSONDecodeError:
            continue
        x3 = str(result.get('x3') or '')
        if (
            result.get('xs')
            and x3.startswith(required_prefix)
            and len(x3) == required_length
        ):
            result['len'] = len(x3)
            result['prefix'] = x3[:12]
            return result
        return None
    return None


__all__ = ['generate_b1', 'run_signer']
