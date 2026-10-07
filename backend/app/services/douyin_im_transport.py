"""抖音私信协议桥接：账号凭据由账号中心提供，收发过程不启动浏览器。"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import websocket
from app.vendor.douyin_spider.builder.auth import DouyinAuth
from app.vendor.douyin_spider.builder.header import HeaderBuilder
from app.vendor.douyin_spider.builder.params import Params
from app.vendor.douyin_spider.dy_apis.douyin_api import DouyinAPI, MessageSendUncertain
from app.vendor.douyin_spider.static import Live_pb2, Response_pb2


_send_auths: dict[str, tuple[str, DouyinAuth]] = {}


def close_send_auth(account_id: str) -> None:
    """释放账号发送会话；调用方须持有该账号的发送锁，避免关闭在途请求。"""
    cached = _send_auths.pop(account_id, None)
    if cached:
        cached[1].close()


def _send_auth(account_id: str, credentials: dict[str, Any]) -> DouyinAuth:
    """按账号及原始认证快照复用会话，令牌有效期由现有协议实现管理。"""
    fingerprint = hashlib.sha256(json.dumps(credentials, sort_keys=True).encode()).hexdigest()
    cached = _send_auths.get(account_id)
    if cached and cached[0] == fingerprint:
        return cached[1]
    close_send_auth(account_id)
    auth = _load_auth(credentials, sending=True)
    _send_auths[account_id] = (fingerprint, auth)
    return auth


class TransportSendUncertain(RuntimeError):
    """发送已开始但结果未知，result 用于记录待确认消息，不可自动重发。"""

    def __init__(self, result: dict[str, Any]):
        super().__init__('发送结果待确认，请先确认对方是否收到，避免重复发送')
        self.result = result


def _numeric_uid(value: Any) -> str:
    """协议需要数字 UID，不能把 sec_uid 当作收件人。"""
    value = str(value or '').strip()
    if not value.isascii() or not value.isdigit() or int(value) <= 0:
        raise ValueError('私信需要平台数字用户 ID，当前用户标识不能用于发送')
    return value


def _load_auth(credentials: dict[str, Any], *, sending: bool = False) -> DouyinAuth:
    """构造隔离的登录会话；认证材料不来自全局环境或其他账号。"""
    cookie_str = str(credentials.get('cookie_str') or '').strip()
    if not cookie_str:
        raise ValueError('账号尚未同步私信登录材料，请从账号中心同步')
    auth = DouyinAuth()
    auth.perepare_auth(cookie_str)
    if not auth.cookie.get('sessionid'):
        raise ValueError('当前账号没有有效私信登录会话，请重新同步登录材料')
    for key in ('ticket', 'ts_sign', 'client_cert', 'private_key', 'dtrait_blob',
                'dtrait_profile', 'session_dtrait'):
        if credentials.get(key):
            setattr(auth, key, credentials[key])
    if credentials.get('user_id'):
        auth.uid = int(_numeric_uid(credentials['user_id']))
    if credentials.get('device_id'):
        auth._webid = str(credentials['device_id'])
    if sending and (not auth.ticket or not auth.ts_sign or not auth.private_key):
        raise ValueError('私信发送认证材料不完整，请重新同步该账号的登录材料')
    if sending and not auth.ticket_matches_session():
        raise ValueError('私信票据与当前登录会话不匹配，请重新同步账号认证')
    return auth


def _conversation_peer(conversation_id: str, own_user_id: str) -> str:
    """只处理一对一会话，严格按参与者确定对方，避免错绑客户。"""
    parts = conversation_id.split(':')
    if len(parts) != 4 or parts[:2] != ['0', '1']:
        raise ValueError('当前仅支持抖音一对一私信会话')
    participants = [_numeric_uid(parts[2]), _numeric_uid(parts[3])]
    if own_user_id not in participants or participants[0] == participants[1]:
        raise ValueError('私信会话参与者与所选账号不一致')
    return participants[1] if participants[0] == own_user_id else participants[0]


def get_user_profile(credentials: dict[str, Any], peer_user_id: str) -> dict[str, str]:
    """只读查询昵称和头像；返回身份必须与目标 UID 一致才可保存。"""
    peer_user_id = _numeric_uid(peer_user_id)
    auth = _load_auth(credentials)
    try:
        payload = DouyinAPI.get_user_profile(auth, peer_user_id)
        user = payload.get('user') or {}
        if payload.get('status_code') != 0 or str(user.get('uid') or '') != peer_user_id:
            raise ValueError('平台未返回与会话一致的用户资料')
        urls = (user.get('avatar_thumb') or {}).get('url_list') or []
        avatar = next((url for url in urls if isinstance(url, str) and url.startswith(('https://', 'http://'))), '')
        return {'peer_user_id': peer_user_id, 'nickname': str(user.get('nickname') or ''), 'avatar': avatar}
    except Exception:
        raise ValueError('用户资料查询失败，请检查账号认证后重新连接接收') from None
    finally:
        auth.close()


def _decode_message(frame_bytes: bytes, own_user_id: str) -> dict[str, Any] | None:
    """解码上游已知通知；保留真实消息 ID，不伪造平台时间或缺失字段。"""
    frame = Live_pb2.PushFrame()
    frame.ParseFromString(frame_bytes)
    if frame.payloadType != 'pb':
        return None
    response = Response_pb2.Response()
    response.ParseFromString(frame.payload)
    if not response.body.HasField('new_message_notify'):
        return None
    message = response.body.new_message_notify.message
    if message.message_type == 50001:
        return None  # 已读事件不应显示成聊天正文。
    if not message.server_message_id or not message.sender or not message.conversation_id:
        raise ValueError('私信推送缺少消息标识或会话参与者，未保存不完整消息')
    if not message.conversation_id.startswith('0:1:'):
        return None  # 首版不承接群聊，群消息不能停止一对一私信监听。
    peer = _conversation_peer(message.conversation_id, own_user_id)
    sender = _numeric_uid(message.sender)
    if sender not in {own_user_id, peer}:
        raise ValueError('私信发送者与会话参与者不一致')
    raw_content = json.loads(message.content)
    if not isinstance(raw_content, dict):
        raise ValueError('私信内容格式无法识别')
    if message.message_type == 7:
        content = str(raw_content.get('text') or '')
    else:
        content = {5: '[表情]', 17: '[语音]', 27: '[图片]', 8: '[分享视频]'}.get(
            message.message_type, f'[消息类型 {message.message_type}]')
    # 上游 schema 不含服务器时间，明确使用接收时间，不能冒充历史发送时间。
    received_at = datetime.now(timezone.utc).isoformat()
    return {
        'platform_message_id': str(message.server_message_id),
        'conversation_id': message.conversation_id,
        'conversation_short_id': message.conversation_short_id,
        'peer_user_id': peer,
        'sender_user_id': sender,
        'message_type': str(message.message_type),
        'content': content,
        'raw_content': raw_content,
        'direction': 'outgoing' if sender == own_user_id else 'incoming',
        'created_at': received_at,
        'sent_at': received_at,
        'time_source': 'received',
    }


def _websocket_url(auth: DouyinAuth) -> str:
    """沿用上游 PC 私信连接参数，设备号来自真实会话查询。"""
    device_id = auth._webid or str(DouyinAPI.get_device_id(auth))
    if not device_id:
        raise ValueError('无法获取私信设备标识，请重新同步账号')
    auth._webid = device_id
    app_key = 'e1bd35ec9db7b8d846de66ed140b1ad9'
    access_key = hashlib.md5(f'9{app_key}{device_id}f8a69f1719916z'.encode()).hexdigest()
    params = Params()
    for key, value in (('aid', '6383'), ('device_platform', 'douyin_pc'),
                       ('fpid', '9'), ('device_id', device_id),
                       ('token', auth.cookie['sessionid']), ('access_key', access_key)):
        params.add_param(key, value)
    return f'wss://frontier-im.douyin.com/ws/v2?{params.toString()}'


def receive_forever(credentials: dict[str, Any], on_message: Callable[[dict], None],
                    on_state: Callable[[str, str], None], stop_event: threading.Event) -> None:
    """阻塞监听，服务应在后台线程调用；回调保存失败时停止，绝不假装已接收。"""
    auth = _load_auth(credentials)
    current: list[Any] = [None]
    watcher_done = threading.Event()
    fatal: list[Exception] = []

    def stop_watcher() -> None:
        # 外部停止时及时关闭阻塞的run_forever，而不是等待下一条消息。
        while not watcher_done.wait(0.1):
            if stop_event.is_set():
                if current[0] is not None:
                    current[0].close()

    def opened(ws: Any) -> None:
        if stop_event.is_set():
            ws.close()
            return
        try:
            on_state('connected', '')
        except Exception as exc:
            fatal.append(exc)
            ws.close()

    def received(ws: Any, raw: bytes) -> None:
        try:
            event = _decode_message(raw, own_uid)
            if event is not None:
                on_message(event)
        except Exception as exc:
            fatal.append(exc)
            ws.close()

    def failed(ws: Any, error: Exception) -> None:
        # websocket错误可能携带含token的URL，不向界面/日志原样输出。
        if isinstance(error, websocket.WebSocketBadStatusException) and error.status_code in (401, 403):
            fatal.append(ValueError('私信登录已失效或被平台拒绝，请同步账号认证'))
            ws.close()

    def closed(ws: Any, code: Any, reason: Any) -> None:
        return  # 正常关闭与异常关闭统一交给外层循环决定重连。

    watcher = threading.Thread(target=stop_watcher, name='douyin-im-stop', daemon=True)
    watcher.start()
    try:
        on_state('connecting', '')
        if stop_event.is_set():
            return
        try:
            own_uid = _numeric_uid(auth.get_uid())
            url = _websocket_url(auth)
        except Exception:
            raise ValueError('无法确认私信登录账号与设备，请重新同步登录材料') from None
        backoff = 1.0
        while not stop_event.is_set():
            ws = websocket.WebSocketApp(
                url=url,
                header={'Pragma': 'no-cache',
                        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
                        'User-Agent': HeaderBuilder.ua, 'Cache-Control': 'no-cache',
                        'Sec-WebSocket-Protocol': 'binary, base64, pbbp2',
                        'Sec-WebSocket-Extensions': 'permessage-deflate; client_max_window_bits'},
                cookie=auth.cookie_str,
                on_open=opened, on_message=received, on_error=failed, on_close=closed,
            )
            current[0] = ws
            if stop_event.is_set():
                break
            ws.run_forever(origin='https://www.douyin.com', ping_interval=25, ping_timeout=10)
            current[0] = None
            if fatal:
                raise fatal[0]
            if stop_event.is_set():
                break
            on_state('reconnecting', '连接断开，正在重新连接；断线期间消息暂不支持补拉')
            if stop_event.wait(backoff):
                break
            backoff = min(backoff * 2, 30.0)
    finally:
        watcher_done.set()
        if current[0] is not None:
            current[0].close()
        auth.close()
        on_state('stopped', '')


def send_text(credentials: dict[str, Any], peer_user_id: str, text: str,
              conversation_id: str = '', conversation_short_id: int = 0,
              ticket: str = '', *, account_id: str) -> dict[str, Any]:
    """在账号发送锁内调用；复用独立认证，结果未知时抛异常且不重发。"""
    peer_user_id = _numeric_uid(peer_user_id)
    text = str(text).strip()
    if not text:
        raise ValueError('私信内容不能为空')
    if len(text) > 2000:
        raise ValueError('私信文本不能超过2000字')
    auth = _send_auth(account_id, credentials)
    try:
        own_uid = _numeric_uid(auth.get_uid())
        if own_uid == peer_user_id:
            raise ValueError('不能向当前账号自己发送私信')
        if conversation_id:
            if _conversation_peer(conversation_id, own_uid) != peer_user_id:
                raise ValueError('私信会话与目标客户不一致')
        if not conversation_id or not conversation_short_id or not ticket:
            conversation_id, conversation_short_id, ticket = DouyinAPI.create_conversation(
                auth, int(peer_user_id))
            if _conversation_peer(str(conversation_id), own_uid) != peer_user_id:
                raise ValueError('平台返回的私信会话与目标客户不一致')
        client_message_id = str(uuid.uuid4())
        result = {'conversation_id': conversation_id,
                  'conversation_short_id': int(conversation_short_id),
                  'ticket': ticket, 'client_message_id': client_message_id,
                  'platform_message_id': ''}
        try:
            response = DouyinAPI._send_message_raw(
                auth, conversation_id, int(conversation_short_id), ticket,
                7, {'text': text}, client_message_id=client_message_id)
        except MessageSendUncertain:
            raise TransportSendUncertain({**result, 'status': 'uncertain'}) from None
        accepted = response.get('message') == 'OK'
        # 已知Response schema没有send回执ID，空值表示未知，不制造平台ID。
        return {**result, 'status': 'accepted' if accepted else 'failed',
                'http_status': response.get('http_status'),
                'error': '' if accepted else '平台拒绝了本次私信发送'}
    except (ValueError, TransportSendUncertain):
        raise
    except Exception:
        raise ValueError('私信认证或会话请求失败，请检查并同步当前账号认证') from None
