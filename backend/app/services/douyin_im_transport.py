"""抖音私信协议桥接：账号凭据由账号中心提供，收发过程不启动浏览器。"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import websocket
from curl_cffi import CurlError
from app.services.im_transport import AuthCache, TransportSendUncertain, validate_peer
from app.vendor.douyin_spider.builder.auth import DouyinAuth
from app.vendor.douyin_spider.builder.header import HeaderBuilder
from app.vendor.douyin_spider.builder.params import Params
from app.vendor.douyin_spider.dy_apis.douyin_api import DouyinAPI, MessagePreparationError, MessageSendUncertain
from app.vendor.douyin_spider.static import Live_pb2, Response_pb2


_send_cache = AuthCache()
_send_auths = _send_cache.values
_logger = logging.getLogger(__name__)
# 项目仅此服务使用 websocket-client；握手不能无限等待网络恢复。
websocket.setdefaulttimeout(15)


def close_send_auth(account_id: str) -> None:
    """释放账号发送认证；调用方持有账号发送锁。"""
    _send_cache.close(account_id)


def _send_auth(account_id: str, credentials: dict[str, Any]) -> DouyinAuth:
    """复用两个平台共同的账号认证缓存，令牌续期仍由抖音协议管理。"""
    return _send_cache.get(account_id, credentials, lambda value: _load_auth(value, sending=True))


def _numeric_uid(value: Any) -> str:
    """协议需要数字 UID，不能把 sec_uid 当作收件人。"""
    return validate_peer('dy', value)


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
    try:
        raw_content = json.loads(message.content)
    except json.JSONDecodeError:
        # 正文格式异常只影响这一条消息；保留原文，不能中断后续接收。
        raw_content = {'unparsed_content': message.content}
    if not isinstance(raw_content, dict):
        raw_content = {'unparsed_content': message.content}
    if message.message_type == 7:
        text = raw_content.get('text')
        content = text if isinstance(text, str) and text else '[文本消息暂无法解析，请在抖音查看]'
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
    last_activity = time.monotonic()
    opened_at = 0.0
    timed_out_socket: Any = None

    def stop_watcher() -> None:
        nonlocal last_activity, timed_out_socket
        # 复用停止监视线程；收到消息或 Pong 都算活跃，无聊天也不会误判断线。
        while not watcher_done.wait(0.1):
            ws = current[0]
            if ws is None:
                continue
            if stop_event.is_set():
                ws.close()
            elif not fatal and time.monotonic() - last_activity >= 75:
                # 状态由主接收循环更新，不能覆盖同时发生的解析/保存/认证错误。
                timed_out_socket = ws
                last_activity = time.monotonic()
                ws.close()

    def opened(ws: Any) -> None:
        nonlocal last_activity, opened_at
        if stop_event.is_set():
            ws.close()
            return
        try:
            opened_at = last_activity = time.monotonic()
            on_state('connected', '')
        except Exception as exc:
            fatal.append(exc)
            ws.close()

    def received(ws: Any, raw: bytes) -> None:
        nonlocal last_activity
        last_activity = time.monotonic()
        try:
            event = _decode_message(raw, own_uid)
        except Exception as exc:
            on_state('error', f'私信消息解析失败（{type(exc).__name__}），接收已停止')
            fatal.append(exc)
            ws.close()
            return
        if event is None:
            return
        try:
            on_message(event)
        except Exception as exc:
            on_state('error', f'私信消息保存失败（{type(exc).__name__}），接收已停止，请检查本地存储')
            fatal.append(exc)
            ws.close()

    def pong(ws: Any, data: Any) -> None:
        nonlocal last_activity
        last_activity = time.monotonic()

    def failed(ws: Any, error: Exception) -> None:
        # websocket错误可能携带含token的URL，不向界面/日志原样输出。
        if isinstance(error, websocket.WebSocketBadStatusException) and error.status_code in (401, 403):
            on_state('expired', '私信登录已失效或被平台拒绝，请同步账号认证后重新连接')
            fatal.append(ValueError('私信登录已失效或被平台拒绝，请同步账号认证'))
            ws.close()
        elif isinstance(error, websocket.WebSocketBadStatusException) and 400 <= error.status_code < 500 and error.status_code != 429:
            on_state('error', f'私信连接被平台拒绝（HTTP {error.status_code}），接收已停止')
            fatal.append(ValueError('私信连接参数被平台拒绝'))
            ws.close()
        elif not stop_event.is_set() and not fatal:
            on_state('reconnecting', '接收连接中断，正在自动重连')

    def closed(ws: Any, code: Any, reason: Any) -> None:
        return  # 正常关闭与异常关闭统一交给外层循环决定重连。

    watcher = threading.Thread(target=stop_watcher, name='douyin-im-stop', daemon=True)
    watcher.start()
    try:
        on_state('connecting', '')
        if stop_event.is_set():
            return
        backoff = 1.0
        while not stop_event.is_set():
            try:
                own_uid = _numeric_uid(auth.get_uid())
                # 每次握手重建参数，使用该认证对象的当前 Cookie 与设备标识。
                url = _websocket_url(auth)
            except (CurlError, OSError, websocket.WebSocketException):
                on_state('reconnecting', '暂时无法连接私信服务，正在自动重连')
                if stop_event.wait(backoff):
                    break
                backoff = min(backoff * 2, 30.0)
                continue
            except Exception:
                on_state('error', '无法确认私信登录账号与设备，请重新同步登录材料')
                raise ValueError('无法确认私信登录账号与设备，请重新同步登录材料') from None
            ws = websocket.WebSocketApp(
                url=url,
                header={'Pragma': 'no-cache',
                        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
                        'User-Agent': HeaderBuilder.ua, 'Cache-Control': 'no-cache',
                        'Sec-WebSocket-Protocol': 'binary, base64, pbbp2',
                        'Sec-WebSocket-Extensions': 'permessage-deflate; client_max_window_bits'},
                cookie=auth.cookie_str,
                on_open=opened, on_message=received, on_error=failed, on_close=closed, on_pong=pong,
            )
            opened_at = 0.0
            last_activity = time.monotonic()
            current[0] = ws
            if stop_event.is_set():
                break
            try:
                ws.run_forever(origin='https://www.douyin.com', ping_interval=25, ping_timeout=10)
            except (OSError, websocket.WebSocketException) as exc:
                failed(ws, exc)
            finally:
                current[0] = None
                ws.close()
            if fatal:
                raise fatal[0]
            if stop_event.is_set():
                break
            # 稳定连接后从 1 秒重试；连续建立即断开则继续退避，避免频繁握手。
            if opened_at and time.monotonic() - opened_at >= 60:
                backoff = 1.0
            on_state('reconnecting', '接收连接心跳超时，正在自动重连' if timed_out_socket is ws
                     else '连接断开，正在自动重连；断线期间消息暂不支持补拉')
            if stop_event.wait(backoff):
                break
            backoff = min(backoff * 2, 30.0)
    finally:
        watcher_done.set()
        if current[0] is not None:
            current[0].close()
        auth.close()
        if stop_event.is_set():
            on_state('stopped', '')


def send_text(credentials: dict[str, Any], peer_user_id: str, text: str,
              conversation_id: str = '', conversation_short_id: int = 0,
              ticket: str = '', *, account_id: str) -> dict[str, Any]:
    """在账号发送锁内调用；仅提交前的网络故障重试一次，结果未知不重发。"""
    peer_user_id = _numeric_uid(peer_user_id)
    text = str(text).strip()
    if not text:
        raise ValueError('私信内容不能为空')
    if len(text) > 2000:
        raise ValueError('私信文本不能超过2000字')
    auth = None
    client_message_id = str(uuid.uuid4())
    for attempt in range(2):
        stage = 'account_auth'
        try:
            auth = _send_auth(account_id, credentials)
            stage = 'account_identity'
            own_uid = _numeric_uid(auth.get_uid())
            if own_uid == peer_user_id:
                raise ValueError('不能向当前账号自己发送私信')
            if conversation_id:
                if _conversation_peer(conversation_id, own_uid) != peer_user_id:
                    raise ValueError('私信会话与目标客户不一致')
            stage = 'conversation'
            if not conversation_id or not conversation_short_id or not ticket:
                conversation_id, conversation_short_id, ticket = DouyinAPI.create_conversation(
                    auth, int(peer_user_id))
                if _conversation_peer(str(conversation_id), own_uid) != peer_user_id:
                    raise ValueError('平台返回的私信会话与目标客户不一致')
            result = {'conversation_id': conversation_id,
                      'conversation_short_id': int(conversation_short_id),
                      'ticket': ticket, 'client_message_id': client_message_id,
                      'platform_message_id': ''}
            stage = 'send_request'
            response = DouyinAPI._send_message_raw(
                auth, conversation_id, int(conversation_short_id), ticket,
                7, {'text': text}, client_message_id=client_message_id)
        except MessageSendUncertain:
            _logger.warning('Douyin IM send uncertain: stage=send_request error_type=MessageSendUncertain retry=False')
            raise TransportSendUncertain({**result, 'status': 'uncertain'}) from None
        except Exception as exc:
            # 只有协议明确标记为提交前失败，才允许重试原始发送调用。
            if stage == 'send_request' and not isinstance(exc, MessagePreparationError):
                _logger.warning('Douyin IM send uncertain: stage=%s error_type=%s retry=False', stage, type(exc).__name__)
                raise TransportSendUncertain({**result, 'status': 'uncertain'}) from None
            failure = exc if isinstance(exc, MessagePreparationError) else MessagePreparationError(stage, exc)
            retry = attempt == 0 and failure.retryable
            # 不记录异常原文、凭据、客户标识或消息正文。
            _logger.warning('Douyin IM preparation failed: stage=%s error_type=%s error_code=%s attempt=%d retry=%s',
                            failure.stage, failure.error_type, failure.error_code, attempt + 1, retry)
            if retry:
                if auth is not None:
                    auth.close()
                continue
            if isinstance(exc, ValueError):
                raise
            raise ValueError('私信认证或会话请求失败，请检查并同步当前账号认证') from None
        accepted = response.get('message') == 'OK'
        # 已知Response schema没有send回执ID，空值表示未知，不制造平台ID。
        return {**result, 'status': 'accepted' if accepted else 'failed',
                'http_status': response.get('http_status'),
                'error': '' if accepted else '平台拒绝了本次私信发送'}
