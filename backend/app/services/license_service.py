from __future__ import annotations

import socket
import uuid
from datetime import datetime
from typing import Any

import httpx

from app import database


DEFAULT_LICENSE_SERVER_URL = "https://tfwqsfaegbdj.sealosbja.site/ai-customer"
LICENSE_CHECK_TIMEOUT = 8.0


def license_overview() -> dict[str, Any]:
    """Return local license settings and create a stable device code if missing."""
    with database.connect() as conn:
        device_code = _ensure_device_code(conn)
        status = database.get_setting(conn, "license_last_status", "unconfigured")
        return {
            "license_code": database.get_setting(conn, "license_code"),
            "device_code": device_code,
            "license_server_url": _server_url(conn),
            "authorized": status == "authorized",
            "status": status,
            "reason": database.get_setting(conn, "license_last_reason"),
            "message": database.get_setting(conn, "license_last_message", "未填写授权码"),
            "last_checked_at": database.get_setting(conn, "license_last_checked_at"),
        }


def update_license_code(license_code: str) -> dict[str, Any]:
    """Persist the editable authorization code while keeping device code immutable."""
    with database.connect() as conn:
        _ensure_device_code(conn)
        database.set_setting(conn, "license_code", license_code.strip())
        database.set_setting(conn, "license_last_status", "unconfigured")
        database.set_setting(conn, "license_last_reason", "")
        database.set_setting(conn, "license_last_message", "授权码已保存，尚未校验")
        database.set_setting(conn, "license_last_checked_at", "")
    return license_overview()


def check_license(license_code: str | None = None) -> dict[str, Any]:
    """Validate the current license code against the Sealos authorization service."""
    with database.connect() as conn:
        device_code = _ensure_device_code(conn)
        if license_code is not None:
            database.set_setting(conn, "license_code", license_code.strip())
        saved_license_code = database.get_setting(conn, "license_code").strip()
        server_url = _server_url(conn)

    if not saved_license_code:
        result = _result(
            authorized=False,
            status="unconfigured",
            reason="LICENSE_CODE_REQUIRED",
            message="请先在设置页填写授权码",
            device_code=device_code,
            license_code=saved_license_code,
            server_url=server_url,
        )
        _save_result(result)
        return result

    try:
        remote = _request_license_check(server_url, saved_license_code, device_code)
    except (httpx.RequestError, ValueError) as exc:
        result = _result(
            authorized=False,
            status="failed",
            reason="LICENSE_SERVER_UNREACHABLE",
            message=f"授权服务器不可访问：{exc}",
            device_code=device_code,
            license_code=saved_license_code,
            server_url=server_url,
        )
        _save_result(result)
        return result

    payload = remote.get("data") if isinstance(remote.get("data"), dict) else {}
    authorized = bool(payload.get("permission"))
    reason = str(payload.get("reason") or ("AUTHORIZED" if authorized else "LICENSE_DENIED"))
    message = str(remote.get("message") or payload.get("message") or ("授权通过" if authorized else "授权失败"))
    result = _result(
        authorized=authorized,
        status="authorized" if authorized else "failed",
        reason=reason,
        message=message,
        device_code=device_code,
        license_code=saved_license_code,
        server_url=server_url,
        max_devices=payload.get("maxDevices"),
        active_device_count=payload.get("activeDeviceCount"),
        bound_new_device=payload.get("boundNewDevice"),
    )
    _save_result(result)
    return result


def ensure_authorized() -> dict[str, Any]:
    """Block task execution when the local device is not authorized."""
    result = check_license()
    if not result.get("authorized"):
        raise ValueError(str(result.get("message") or "授权校验失败，请在设置页检查授权码"))
    return result


def _request_license_check(server_url: str, license_code: str, device_code: str) -> dict[str, Any]:
    url = f"{server_url.rstrip('/')}/check-license"
    body = {
        "licenseCode": license_code,
        "deviceId": device_code,
        "deviceName": socket.gethostname(),
        "remark": "AI_Customer 本地工作台",
    }
    with httpx.Client(timeout=LICENSE_CHECK_TIMEOUT) as client:
        response = client.post(url, json=body)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError(f"授权服务器返回了非 JSON 内容，HTTP {response.status_code}") from exc
    if response.status_code >= 400:
        message = payload.get("message") if isinstance(payload, dict) else ""
        raise ValueError(message or f"授权服务器返回 HTTP {response.status_code}")
    if not isinstance(payload, dict):
        raise ValueError("授权服务器返回格式不正确")
    return payload


def _ensure_device_code(conn) -> str:
    device_code = database.get_setting(conn, "device_code").strip()
    if device_code:
        return device_code
    # 设备码只在首次运行时生成，后续不通过前端修改。
    device_code = f"AI-CUS-{uuid.uuid4().hex[:8].upper()}-{uuid.uuid4().hex[:8].upper()}"
    database.set_setting(conn, "device_code", device_code)
    return device_code


def _server_url(conn) -> str:
    return database.get_setting(conn, "license_server_url", DEFAULT_LICENSE_SERVER_URL).strip() or DEFAULT_LICENSE_SERVER_URL


def _result(
    *,
    authorized: bool,
    status: str,
    reason: str,
    message: str,
    device_code: str,
    license_code: str,
    server_url: str,
    max_devices: Any = None,
    active_device_count: Any = None,
    bound_new_device: Any = None,
) -> dict[str, Any]:
    return {
        "authorized": authorized,
        "status": status,
        "reason": reason,
        "message": message,
        "license_code": license_code,
        "device_code": device_code,
        "license_server_url": server_url,
        "max_devices": max_devices,
        "active_device_count": active_device_count,
        "bound_new_device": bound_new_device,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _save_result(result: dict[str, Any]) -> None:
    with database.connect() as conn:
        database.set_setting(conn, "license_last_status", result.get("status", ""))
        database.set_setting(conn, "license_last_reason", result.get("reason", ""))
        database.set_setting(conn, "license_last_message", result.get("message", ""))
        database.set_setting(conn, "license_last_checked_at", result.get("checked_at", ""))
