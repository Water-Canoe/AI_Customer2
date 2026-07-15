from __future__ import annotations

import base64
import binascii
import json
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app import database, product_config
from app.version import APP_VERSION


LICENSE_CHECK_TIMEOUT = 8.0
LICENSE_ENTITLEMENTS = {"lead", "traffic", "content"}
LEASE_RENEW_AFTER = timedelta(hours=6)


def license_overview() -> dict[str, Any]:
    return _license_overview(None)


def license_overview_for(entitlement: str) -> dict[str, Any]:
    return _license_overview(_normalize_entitlement(entitlement))


def _license_overview(entitlement: str | None) -> dict[str, Any]:
    with database.connect() as conn:
        device_code = _ensure_device_code(conn)
        license_code = database.get_setting(conn, "license_code").strip()
        cached = _cached_lease(conn, device_code)
        status = database.get_setting(conn, "license_last_status", "unconfigured")
        reason = database.get_setting(conn, "license_last_reason")
        message = database.get_setting(conn, "license_last_message", "未填写授权码")
        checked_at = database.get_setting(conn, "license_last_checked_at")
        max_devices = _setting_int(conn, "license_max_devices")
        active_device_count = _setting_int(conn, "license_active_device_count")

    authorized = bool(cached and (entitlement is None or entitlement in cached["entitlements"]))
    if cached and entitlement is not None and not authorized:
        status, reason, message = "failed", "ENTITLEMENT_NOT_INCLUDED", f"当前授权未包含{_entitlement_name(entitlement)}"
    elif authorized:
        status, reason = "authorized", "LEASE_VALID"
        message = "授权有效"
    return {
        "scope": entitlement or "product",
        "license_code": license_code,
        "device_code": device_code,
        "authorized": authorized,
        "status": status,
        "reason": reason,
        "message": message,
        "last_checked_at": checked_at,
        "max_devices": max_devices,
        "active_device_count": active_device_count,
        "entitlements": cached["entitlements"] if cached else [],
        "lease_expires_at": cached["expiresAt"] if cached else "",
    }


def update_license_code(license_code: str) -> dict[str, Any]:
    return _update_license_code(None, license_code)


def update_license_code_for(entitlement: str, license_code: str) -> dict[str, Any]:
    return _update_license_code(_normalize_entitlement(entitlement), license_code)


def _update_license_code(entitlement: str | None, license_code: str) -> dict[str, Any]:
    normalized = license_code.strip()
    with database.connect() as conn:
        _ensure_device_code(conn)
        if database.get_setting(conn, "license_code").strip() != normalized:
            _clear_lease(conn)
        database.set_setting(conn, "license_code", normalized)
        database.set_setting(conn, "license_last_status", "unconfigured")
        database.set_setting(conn, "license_last_reason", "")
        database.set_setting(conn, "license_last_message", "授权码已保存，尚未激活")
        database.set_setting(conn, "license_last_checked_at", "")
    return _license_overview(entitlement)


def check_license(license_code: str | None = None) -> dict[str, Any]:
    return _check_license(None, license_code)


def check_license_for(entitlement: str, license_code: str | None = None) -> dict[str, Any]:
    return _check_license(_normalize_entitlement(entitlement), license_code)


def _check_license(entitlement: str | None, license_code: str | None = None) -> dict[str, Any]:
    if license_code is not None:
        _update_license_code(entitlement, license_code)
    with database.connect() as conn:
        device_code = _ensure_device_code(conn)
        saved_license_code = database.get_setting(conn, "license_code").strip()
    if not saved_license_code:
        return _save_failure(entitlement, "unconfigured", "LICENSE_CODE_REQUIRED", "请先填写授权码")
    try:
        remote = _request_license("activate", saved_license_code, device_code)
    except httpx.RequestError:
        cached = _license_overview(entitlement)
        if cached.get("authorized"):
            return cached
        return _save_failure(entitlement, "failed", "LICENSE_SERVER_UNREACHABLE", "授权服务暂时不可用，请检查网络后重试")
    except ValueError:
        return _save_failure(entitlement, "failed", "LICENSE_RESPONSE_INVALID", "授权服务返回了无效数据")
    try:
        return _accept_remote(entitlement, remote, device_code)
    except (ValueError, json.JSONDecodeError):
        return _save_failure(entitlement, "failed", "LICENSE_SIGNATURE_INVALID", "授权签名校验失败")


def ensure_authorized() -> dict[str, Any]:
    return ensure_authorized_for("lead")


def ensure_authorized_for(entitlement: str) -> dict[str, Any]:
    entitlement = _normalize_entitlement(entitlement)
    with database.connect() as conn:
        device_code = _ensure_device_code(conn)
        license_code = database.get_setting(conn, "license_code").strip()
        cached = _cached_lease(conn, device_code)

    if cached and entitlement in cached["entitlements"]:
        issued_at = _parse_time(cached["issuedAt"])
        if datetime.now(timezone.utc) - issued_at < LEASE_RENEW_AFTER:
            return license_overview_for(entitlement)
        try:
            remote = _request_license("renew", license_code, device_code)
        except httpx.RequestError:
            return license_overview_for(entitlement)
        except ValueError as exc:
            raise ValueError("授权服务返回了无效数据") from exc
        result = _accept_remote(entitlement, remote, device_code)
        if result.get("authorized"):
            return result
        raise ValueError(str(result.get("message") or "授权已失效"))

    if cached:
        raise ValueError(f"当前授权未包含{_entitlement_name(entitlement)}")

    if not license_code:
        raise ValueError("请先在设置页填写并激活授权码")
    try:
        remote = _request_license("renew", license_code, device_code)
    except httpx.RequestError as exc:
        raise ValueError("授权已过期且暂时无法连接授权服务") from exc
    except ValueError as exc:
        raise ValueError("授权服务返回了无效数据") from exc
    result = _accept_remote(entitlement, remote, device_code)
    if not result.get("authorized"):
        raise ValueError(str(result.get("message") or "授权校验失败"))
    return result


def _request_license(action: str, license_code: str, device_code: str) -> dict[str, Any]:
    body = {
        "licenseCode": license_code,
        "deviceId": device_code,
        "deviceName": socket.gethostname(),
        "appVersion": APP_VERSION,
    }
    with httpx.Client(timeout=LICENSE_CHECK_TIMEOUT) as client:
        response = client.post(f"{product_config.license_endpoint()}/license/{action}", json=body)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("授权服务返回了非JSON内容") from exc
    if not isinstance(payload, dict):
        raise ValueError("授权服务返回格式不正确")
    return payload


def _accept_remote(entitlement: str | None, remote: dict[str, Any], device_code: str) -> dict[str, Any]:
    payload = remote.get("data") if isinstance(remote.get("data"), dict) else {}
    if not payload.get("permission"):
        reason = str(payload.get("reason") or "LICENSE_DENIED")
        message = str(remote.get("message") or "授权校验失败")
        with database.connect() as conn:
            _clear_lease(conn)
        return _save_failure(entitlement, "failed", reason, message)

    lease_text = payload.get("leaseText")
    signature = payload.get("signature")
    lease = _verify_lease(lease_text, signature, device_code)
    with database.connect() as conn:
        database.set_setting(conn, "license_lease_text", str(lease_text))
        database.set_setting(conn, "license_lease_signature", str(signature))
        database.set_setting(conn, "license_max_devices", str(payload.get("maxDevices") or ""))
        database.set_setting(conn, "license_active_device_count", str(payload.get("activeDeviceCount") or ""))
        database.set_setting(conn, "license_last_status", "authorized")
        database.set_setting(conn, "license_last_reason", str(payload.get("reason") or "LEASE_RENEWED"))
        database.set_setting(conn, "license_last_message", str(remote.get("message") or "授权有效"))
        database.set_setting(conn, "license_last_checked_at", _now_text())

    if entitlement is not None and entitlement not in lease["entitlements"]:
        return _save_failure(entitlement, "failed", "ENTITLEMENT_NOT_INCLUDED", f"当前授权未包含{_entitlement_name(entitlement)}", clear_lease=False)
    return _license_overview(entitlement)


def _verify_lease(lease_text: Any, signature_text: Any, device_code: str) -> dict[str, Any]:
    if not isinstance(lease_text, str) or not isinstance(signature_text, str):
        raise ValueError("授权租约不完整")
    try:
        signature = base64.b64decode(signature_text, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("授权签名格式不正确") from exc
    if len(signature) != 64:
        raise ValueError("授权签名长度不正确")
    public_key = serialization.load_pem_public_key(product_config.license_public_key_pem())
    if not isinstance(public_key, Ed25519PublicKey):
        raise ValueError("授权公钥格式不正确")
    try:
        public_key.verify(signature, lease_text.encode("utf-8"))
    except InvalidSignature as exc:
        raise ValueError("授权签名校验失败") from exc
    payload = json.loads(lease_text)
    if not isinstance(payload, dict) or payload.get("version") != 1 or payload.get("deviceId") != device_code:
        raise ValueError("授权租约不适用于当前设备")
    entitlements = payload.get("entitlements")
    if not isinstance(entitlements, list) or not entitlements or any(item not in LICENSE_ENTITLEMENTS for item in entitlements):
        raise ValueError("授权租约权益无效")
    issued_at = _parse_time(payload.get("issuedAt"))
    expires_at = _parse_time(payload.get("expiresAt"))
    now = datetime.now(timezone.utc)
    if issued_at > now + timedelta(minutes=5) or expires_at <= issued_at or expires_at <= now:
        raise ValueError("授权租约已过期或时间无效")
    if expires_at - issued_at > timedelta(hours=73):
        raise ValueError("授权租约有效期超出限制")
    return payload


def _cached_lease(conn, device_code: str) -> dict[str, Any] | None:
    lease_text = database.get_setting(conn, "license_lease_text")
    signature = database.get_setting(conn, "license_lease_signature")
    if not lease_text or not signature:
        return None
    try:
        return _verify_lease(lease_text, signature, device_code)
    except (ValueError, json.JSONDecodeError):
        return None


def _clear_lease(conn) -> None:
    for key in ("license_lease_text", "license_lease_signature", "license_max_devices", "license_active_device_count"):
        database.set_setting(conn, key, "")


def _save_failure(entitlement: str | None, status: str, reason: str, message: str, *, clear_lease: bool = True) -> dict[str, Any]:
    with database.connect() as conn:
        if clear_lease:
            _clear_lease(conn)
        database.set_setting(conn, "license_last_status", status)
        database.set_setting(conn, "license_last_reason", reason)
        database.set_setting(conn, "license_last_message", message)
        database.set_setting(conn, "license_last_checked_at", _now_text())
    return _license_overview(entitlement)


def _ensure_device_code(conn) -> str:
    device_code = database.get_setting(conn, "device_code").strip()
    if device_code:
        return device_code
    # 设备码只在首次运行时生成，前端不能修改。
    device_code = f"AI-CUS-{uuid.uuid4().hex[:8].upper()}-{uuid.uuid4().hex[:8].upper()}"
    database.set_setting(conn, "device_code", device_code)
    return device_code


def _normalize_entitlement(value: str) -> str:
    entitlement = str(value or "lead").strip().lower()
    if entitlement not in LICENSE_ENTITLEMENTS:
        raise ValueError(f"未知授权权益：{entitlement}")
    return entitlement


def _entitlement_name(value: str) -> str:
    return {"lead": "拓客功能", "traffic": "引流功能", "content": "内容功能"}[value]


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("授权时间格式不正确")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("授权时间格式不正确") from exc
    if parsed.tzinfo is None:
        raise ValueError("授权时间缺少时区")
    return parsed.astimezone(timezone.utc)


def _setting_int(conn, key: str) -> int | None:
    try:
        return int(database.get_setting(conn, key))
    except (TypeError, ValueError):
        return None


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
