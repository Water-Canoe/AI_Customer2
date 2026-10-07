"""Spider_XHS 私信适配：WebSocket 接收、签名 HTTP 发送，不读取聊天 DOM。"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlencode

import websocket
from curl_cffi import CurlError

from app.services.im_transport import AuthCache, TransportSendUncertain, validate_peer
from app.vendor.spider_xhs.im import BROWSER_UA, PUSH_URL, XHSLiveAPI, XHSWebSocket, encode_im_chat_message, rsa_encrypt_short_link_payload
from app.vendor.spider_xhs.xhs_utils.xhs_pc import XHSPcAuth

_send_cache = AuthCache()


def _user_id(value: Any) -> str:
    """小红书用户 ID 为 24 位十六进制，不能把抖音 UID 或主页 URL 当 ID。"""
    return validate_peer('xhs', value)


def _load_auth(credentials: dict[str, Any]) -> XHSPcAuth:
    # 工厂通过 user/me 确认真实账号身份，不根据本地昵称猜测用户。
    return XHSPcAuth.from_cookie(str(credentials.get('cookie_str') or ''),
                               cookie_source_url='https://www.xiaohongshu.com/')


def close_send_auth(account_id: str) -> None:
    """与抖音共用账号缓存规则；调用方已持有发送锁。"""
    _send_cache.close(account_id)


def _checked(payload: dict) -> dict:
    if payload.get('success') is not True or payload.get('code') not in (0, None):
        raise ValueError('小红书接口未接受请求，请检查账号认证或平台权限')
    return payload.get('data') or {}


def get_user_profile(credentials: dict[str, Any], peer_user_id: str) -> dict[str, str]:
    peer = _user_id(peer_user_id)
    auth = _load_auth(credentials)
    try:
        api = XHSLiveAPI(auth)
        data = _checked(api._request(auth.origin('api'), '/api/sns/web/v1/user/otherinfo?' + urlencode({'target_user_id': peer})))
        info = data.get('basic_info') or {}
        # otherinfo 的基本资料没有重复的用户 ID；请求目标固定为本会话 ID。
        returned_id = data.get('user_id') or info.get('user_id')
        if returned_id and str(returned_id) != peer:
            raise ValueError('平台返回的用户资料与会话不一致')
        avatar = str(info.get('imageb') or info.get('images') or '')
        return {'peer_user_id': peer, 'nickname': str(info.get('nickname') or ''),
                'avatar': avatar if avatar.startswith(('https://', 'http://')) else ''}
    except Exception:
        raise ValueError('小红书用户资料查询失败，请检查账号认证后重新连接接收') from None
    finally:
        auth.close()


def _message_event(message: dict, own_user_id: str) -> dict | None:
    """依据上游 ChatMessage 的 payload_json 规范化单聊，保留原始正文。"""
    body = message.get('payload_json')
    if not isinstance(body, dict):
        raise ValueError('小红书私信正文格式无法识别')
    if body.get('groupChat') or body.get('group_chat'):
        return None
    sender = _user_id(body.get('sender'))
    receiver = _user_id(body.get('receiver'))
    if own_user_id not in {sender, receiver} or sender == receiver:
        raise ValueError('小红书私信参与者与所选账号不一致')
    peer = receiver if sender == own_user_id else sender
    message_id = str(message.get('messageid') or '')
    if not message_id:
        raise ValueError('小红书私信推送缺少平台消息 ID')
    content = body.get('content')
    message_type = str(body.get('contentType', body.get('content_type', 1)))
    # 上游示例的文本常有多层 JSON 编码，最多按其六层结构提取。
    for _ in range(6):
        if isinstance(content, dict) and ('content' in content or 'text' in content):
            content = content.get('content', content.get('text'))
        elif isinstance(content, str):
            try:
                parsed = json.loads(content)
            except ValueError:
                break
            if not isinstance(parsed, (dict, list)):
                break
            content = parsed
        else:
            break
    if message_type != '1':
        content = f'[消息类型 {message_type}]'
    elif not isinstance(content, str):
        raise ValueError('小红书文字私信内容无法识别')
    ts = int(message.get('ts') or 0)
    sent_at = datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat() if ts > 0 else datetime.now(timezone.utc).isoformat()
    return {'platform_message_id': message_id, 'peer_user_id': peer, 'conversation_id': peer,
            'sender_user_id': sender, 'direction': 'outgoing' if sender == own_user_id else 'incoming',
            'content': content, 'message_type': message_type, 'raw_content': body, 'sent_at': sent_at,
            'nickname': str(body.get('nickname') or '')}


def _receive_frame(raw: str, own_user_id: str, on_message: Callable) -> None:
    frame = XHSWebSocket._decode_text_frame(raw)
    decoded = frame.get('decoded') or {}
    if decoded.get('im_error'):
        raise ValueError('小红书私信帧无法解析')
    entries = decoded.get('im') or []
    for entry in entries if isinstance(entries, list) else [entries]:
        if entry.get('decode_error'):
            raise ValueError('小红书私信帧无法解析')
        if entry.get('chatMessage'):
            event = _message_event(entry['chatMessage'], own_user_id)
            if event:
                try:
                    on_message(event)
                except Exception:
                    # 本地落库故障不能按网络异常重连，以免掩盖消息未保存。
                    raise RuntimeError('私信消息保存失败') from None


def _wait_ack(ws: Any, frame: str, stop: threading.Event, on_frame: Callable) -> None:
    """握手、注册必须取得匹配且成功的传输回执，期间消息照常保存。"""
    mid = json.loads(frame)['m']
    ws.send(frame)
    deadline = time.monotonic() + 10
    while not stop.is_set() and time.monotonic() < deadline:
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        if not raw:
            raise websocket.WebSocketConnectionClosedException('私信连接已关闭')
        reply = json.loads(raw)
        if reply.get('m') == mid:
            if (reply.get('b', {}).get('a') or {}).get('c') != 0:
                raise ValueError('小红书拒绝了私信连接认证，请检查账号权限后同步认证')
            return
        on_frame(raw)
    if not stop.is_set():
        raise websocket.WebSocketTimeoutException('私信连接认证超时')


def receive_forever(credentials: dict[str, Any], on_message: Callable,
                    on_state: Callable, stop_event: threading.Event) -> None:
    """后台监听；心跳静默检测及退避重连，认证或落库故障停止并报告。"""
    auth = None
    backoff = 1.0
    on_state('connecting', '')
    while not stop_event.is_set():
        ws = None
        opened_at = 0.0
        try:
            if auth is None:
                auth = _load_auth(credentials)
            if stop_event.is_set():
                break
            client = XHSWebSocket.from_auth_storage(auth)
            if not client.sid:
                _checked(XHSLiveAPI(auth).get_celestial_lt())
                client = XHSWebSocket.from_auth_storage(auth)
            if not client.uid or not client.sid or not client.device_id or not client.fingerprint:
                raise ValueError('小红书私信连接认证不完整，请重新同步')
            cookie = '; '.join(f'{key}={value}' for key, value in auth.cookies_for_url(PUSH_URL.replace('wss://', 'https://')).items())
            ws = websocket.create_connection(PUSH_URL, timeout=15, origin='https://www.xiaohongshu.com', cookie=cookie, header={'User-Agent': BROWSER_UA})
            ws.settimeout(1)
            on_frame = lambda raw: _receive_frame(raw, client.uid, on_message)
            _wait_ack(ws, client.handshake(), stop_event, on_frame)
            _wait_ack(ws, client.register('im', 'protobuf'), stop_event, on_frame)
            if stop_event.is_set():
                break
            opened_at = last_activity = last_heartbeat = time.monotonic()
            on_state('connected', '')
            while not stop_event.is_set():
                now = time.monotonic()
                if now - last_heartbeat >= 25:
                    ws.send(client.transport_heartbeat())
                    last_heartbeat = now
                if now - last_activity >= 75:
                    raise websocket.WebSocketTimeoutException('私信连接心跳超时')
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not raw:
                    raise websocket.WebSocketConnectionClosedException('私信连接已关闭')
                last_activity = time.monotonic()
                on_frame(raw)
        except (CurlError, OSError, websocket.WebSocketException) as exc:
            code = getattr(exc, 'status_code', None) or getattr(getattr(exc, 'response', None), 'status_code', None)
            if code in (401, 403):
                on_state('expired', '小红书私信认证已失效，请重新登录后同步认证')
                break
            if code and 400 <= code < 500 and code != 429:
                on_state('error', f'小红书私信连接被拒绝（HTTP {code}）')
                break
        except Exception as exc:
            # 不输出上游异常正文：可能含 Cookie、请求内容或密钥。
            on_state('error', f'小红书私信认证、解析或保存失败（{type(exc).__name__}），接收已停止')
            break
        finally:
            if ws is not None:
                ws.close()
        if stop_event.is_set():
            break
        if opened_at and time.monotonic() - opened_at >= 60:
            backoff = 1.0
        on_state('reconnecting', '小红书连接中断，正在自动重连；断线期间消息暂不补拉')
        if stop_event.wait(backoff):
            break
        backoff = min(backoff * 2, 30.0)
    if auth is not None:
        auth.close()


def send_text(credentials: dict[str, Any], peer_user_id: str, text: str,
              conversation_id: str = '', conversation_short_id: int = 0,
              ticket: str = '', *, account_id: str) -> dict[str, Any]:
    """使用上游签名 HTTP 私信入口；业务成功才标已提交，未知结果不重发。"""
    peer = _user_id(peer_user_id)
    text = text.strip()
    if not text or len(text) > 2000:
        raise ValueError('私信内容不能为空，且不能超过2000字')
    auth = _send_cache.get(account_id, credentials, _load_auth)
    if auth.user_id == peer:
        raise ValueError('不能向当前账号自己发送私信')
    if conversation_id and conversation_id != peer:
        raise ValueError('私信会话与目标客户不一致')
    mid = str(uuid.uuid4())
    payload = encode_im_chat_message(mid=mid, ts=int(time.time() * 1000), token='', sender=auth.user_id,
                                     receiver=peer, content=text, content_type=1, nickname='',
                                     group_chat=False, command=None, ref_id='', trigger_source=1)
    body = {'message': rsa_encrypt_short_link_payload(payload).decode('latin-1')}
    result = {'conversation_id': peer, 'client_message_id': mid, 'platform_message_id': ''}
    try:
        response = XHSLiveAPI(auth)._request(auth.origin('api'), '/api/im/web/short_link/send_message', method='POST', data=body)
    except Exception:
        raise TransportSendUncertain({**result, 'status': 'uncertain', 'error': '未取得明确的小红书发送结果，请核实后再操作'}) from None
    # HTTP 200 或按钮变化均不代表成功；必须有明确的业务接受状态。
    accepted = response.get('success') is True and response.get('code') == 0
    rejected = response.get('success') is False or (response.get('code') is not None and response.get('code') != 0)
    data = response.get('data') or {}
    if isinstance(data, dict):
        result['platform_message_id'] = str(data.get('messageid') or data.get('message_id') or '')
    return {**result, 'status': 'accepted' if accepted else 'failed' if rejected else 'uncertain',
            'error': '' if accepted else '小红书拒绝本次私信' if rejected else '平台未返回明确发送回执，请核实后再操作'}
