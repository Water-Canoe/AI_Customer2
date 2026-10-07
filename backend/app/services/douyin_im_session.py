from __future__ import annotations

import json
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.services.im_session import _session_path, _cookie_map, load_credentials, save_credentials, session_status


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
    save_credentials(account_id, credentials)
    return session_status(account_id)
