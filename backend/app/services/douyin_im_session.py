from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app import protected_settings
from app.services import account_center


def _session_path(account_id: str) -> Path:
    account = account_center.get_account(account_id, feature="message", require_ready=False)
    if account["platform"] != "dy":
        raise ValueError("协议私信当前只支持抖音账号")
    return account_center.account_auth_path(account_id, "user").parent / "im-session.dpapi"


def _cookie_map(cookie_str: str) -> dict[str, str]:
    return dict(token.strip().split("=", 1) for token in cookie_str.split(";") if "=" in token)


def load_credentials(account_id: str) -> dict[str, Any]:
    """仅供后台读取当前用户保护的认证材料，禁止作为 API 响应。"""
    path = _session_path(account_id)
    if not path.is_file():
        raise ValueError("请先同步该账号的私信认证")
    try:
        value = json.loads(protected_settings.unprotect(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise ValueError("私信认证无法读取，请重新同步") from exc
    if value.get("account_id") != account_id:
        raise ValueError("私信认证与当前账号不匹配，请重新同步")
    return value


def session_status(account_id: str) -> dict[str, Any]:
    """公开状态只包含可用性；网页登录成功不等于发送认证齐全。"""
    try:
        value = load_credentials(account_id)
    except ValueError as exc:
        return {"receive_ready": False, "send_ready": False, "error": str(exc), "synced_at": ""}
    cookies = _cookie_map(str(value.get("cookie_str") or ""))
    receive_ready = bool(cookies.get("sessionid"))
    matched = not cookies.get("bd_ticket_guard_ts_sign_id") or str(value.get("ts_sign") or "").startswith(cookies["bd_ticket_guard_ts_sign_id"])
    send_ready = receive_ready and matched and all(value.get(key) for key in ("ticket", "ts_sign", "private_key"))
    error = "" if send_ready else "已读取登录会话，但发送认证材料不完整；请在账号中心重新登录后同步"
    if not receive_ready:
        error = "未读取到有效的抖音用户登录会话，请重新登录"
    return {"receive_ready": receive_ready, "send_ready": bool(send_ready), "error": error, "synced_at": value.get("synced_at", "")}


def _objects(value: Any):
    """读取浏览器导出的嵌套 JSON，包括上游使用的 data 双层编码。"""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            yield item
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str) and item.lstrip().startswith(("{", "[", '"')):
            try:
                parsed = json.loads(item)
            except ValueError:
                continue
            if parsed != item:
                stack.append(parsed)


def _normalize_private_key(value: str) -> str:
    try:
        key = serialization.load_pem_private_key(value.encode(), password=None)
    except (ValueError, TypeError):
        return ""
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        return ""
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode()


def credentials_from_snapshot(cookies: list[dict[str, Any]], snapshot: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """从同一个用户会话提取已存在材料，不生成票据或混用另一次登录的密钥。"""
    values = {str(item["name"]): str(item["value"]) for item in cookies}
    result: dict[str, Any] = {"cookie_str": "; ".join(f"{key}={value}" for key, value in values.items())}
    if not values.get("sessionid"):
        raise ValueError("当前 Profile 未包含抖音用户登录会话，请先在账号中心完成用户登录")
    previous = previous or {}
    old_cookies = _cookie_map(str(previous.get("cookie_str") or ""))
    if old_cookies.get("sessionid") == values["sessionid"]:
        result.update({key: previous[key] for key in ("ticket", "ts_sign", "client_cert", "private_key") if previous.get(key)})
    sign_id = values.get("bd_ticket_guard_ts_sign_id", "")
    tickets: dict[str, dict[str, str]] = {}
    keys: set[str] = set()
    # localStorage 的 value 和 IndexedDB 中的嵌套对象都由 Playwright 导出。
    origins = [origin for origin in snapshot.get("origins", []) if origin.get("origin") == "https://www.douyin.com"]
    for item in _objects(origins):
        if item.get("ticket") and item.get("ts_sign"):
            ts_sign = str(item["ts_sign"])
            if not sign_id or ts_sign.startswith(sign_id):
                tickets[ts_sign] = {field: str(item[field]) for field in ("ticket", "ts_sign", "client_cert") if item.get(field)}
        private = item.get("ec_privateKey") or item.get("private_key")
        if isinstance(private, str):
            normalized = _normalize_private_key(private)
            if normalized:
                keys.add(normalized)
    # 多组无法确定归属的材料保持不可发送，不通过猜选绕过会话校验。
    if len(tickets) == 1:
        result.update(next(iter(tickets.values())))
    elif len(tickets) > 1:
        result.pop("ticket", None)
        result.pop("ts_sign", None)
    if len(keys) == 1:
        result["private_key"] = next(iter(keys))
    elif len(keys) > 1:
        result.pop("private_key", None)
    if sign_id and not str(result.get("ts_sign") or "").startswith(sign_id):
        for key in ("ticket", "ts_sign", "client_cert", "private_key"):
            result.pop(key, None)
    return result


async def capture_context(account_id: str, context: Any) -> dict[str, Any]:
    """登录或明确同步时导出一次；日常私信不打开浏览器。"""
    try:
        previous = load_credentials(account_id)
    except ValueError:
        previous = None
    cookies = await context.cookies("https://www.douyin.com/")
    snapshot = await context.storage_state(indexed_db=True)
    credentials = credentials_from_snapshot(cookies, snapshot, previous)
    credentials.update(account_id=account_id, synced_at=datetime.now().isoformat(timespec="seconds"))
    path = _session_path(account_id)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    # 复用项目已有 DPAPI，只保存提取出的材料，不保存整个浏览器快照。
    temporary.write_text(protected_settings.protect(json.dumps(credentials, ensure_ascii=False)), encoding="utf-8")
    temporary.replace(path)
    return session_status(account_id)


async def sync_account(account_id: str) -> dict[str, Any]:
    from app.publish_engine.browser import launch_publish_context
    from app.services import browser_queue

    account_center.get_account(account_id, feature="message", require_ready=True)
    async with browser_queue.async_browser_slot(f"im_sync:{account_id}"):
        context = await launch_publish_context(headless=True, account_file=account_center.profile_path(account_id, "message"))
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=30_000)
            return await capture_context(account_id, context)
        finally:
            await context.close()
