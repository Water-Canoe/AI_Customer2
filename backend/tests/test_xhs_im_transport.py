"""离线验证小红书协议与失败语义，不调用真实私信接口。"""
import base64
import json
import threading
from types import SimpleNamespace

import pytest
import websocket

from app.services import xhs_im_transport as transport
from app.vendor.spider_xhs import im

OWN = '64abcdef1234567890abcdef'
PEER = '65abcdef1234567890abcdef'


def _push(sender=PEER, receiver=OWN):
    body = {'sender': sender, 'receiver': receiver, 'contentType': 1,
            'content': json.dumps({'content': '你好，小红书'})}
    message = im._pb_message(4, im._pb_string(1, 'mid') + im._pb_string(2, 'server-message')
                             + im._pb_uint(3, 1791432000000) + im._pb_string(5, json.dumps(body)))
    return json.dumps({'v': 1, 't': 4, 'b': {'d': {'biz': 'im', 'b': [{'d': base64.b64encode(message).decode()}]}}})


def test_reuses_upstream_wire_format_and_normalizes_inbound_identity():
    events = []
    transport._receive_frame(_push(), OWN, events.append)
    assert len(events) == 1
    assert events[0]['peer_user_id'] == PEER and events[0]['direction'] == 'incoming'
    assert events[0]['platform_message_id'] == 'server-message'
    assert events[0]['content'] == '你好，小红书'
    assert events[0]['raw_content']['sender'] == PEER
    with pytest.raises(ValueError, match='参与者'):
        transport._receive_frame(_push(receiver='66abcdef1234567890abcdef'), OWN, events.append)
    with pytest.raises(ValueError):
        transport._user_id('123456')


@pytest.mark.parametrize(('response', 'expected'), [
    ({'success': True, 'code': 0, 'data': {'messageid': 'sent-id'}}, 'accepted'),
    ({'success': False, 'code': -1}, 'failed'),
    ({'success': True}, 'uncertain'),
    ({}, 'uncertain'),
])
def test_http_send_requires_business_ack_and_preserves_request_id(monkeypatch, response, expected):
    auth = SimpleNamespace(user_id=OWN, origin=lambda role: 'https://edith.xiaohongshu.com')
    monkeypatch.setattr(transport._send_cache, 'get', lambda *args: auth)
    requests = []
    def request(_self, origin, api, **kwargs):
        requests.append((origin, api, kwargs))
        return response
    monkeypatch.setattr(im.XHSLiveAPI, '__init__', lambda self, auth: None)
    monkeypatch.setattr(im.XHSLiveAPI, '_request', request)
    result = transport.send_text({}, PEER, '测试文字', account_id='xhs')
    assert result['status'] == expected and result['conversation_id'] == PEER
    assert result['client_message_id'] and len(requests) == 1
    assert requests[0][1] == '/api/im/web/short_link/send_message'
    assert requests[0][2]['method'] == 'POST'
    assert result['platform_message_id'] == ('sent-id' if expected == 'accepted' else '')


def test_network_failure_after_send_stays_uncertain_without_retry(monkeypatch):
    auth = SimpleNamespace(user_id=OWN, origin=lambda role: 'https://edith.xiaohongshu.com')
    monkeypatch.setattr(transport._send_cache, 'get', lambda *args: auth)
    monkeypatch.setattr(im.XHSLiveAPI, '__init__', lambda self, auth: None)
    count = []
    def request(*args, **kwargs):
        count.append(1)
        raise OSError('contains-private-token')
    monkeypatch.setattr(im.XHSLiveAPI, '_request', request)
    with pytest.raises(transport.TransportSendUncertain) as caught:
        transport.send_text({}, PEER, '测试文字', account_id='xhs')
    assert caught.value.result['status'] == 'uncertain' and count == [1]
    assert 'private-token' not in str(caught.value)


def test_handshake_waits_for_matching_ack_and_preserves_interleaved_messages():
    frame = json.dumps({'v': 1, 't': 2, 'm': 'handshake'})
    sent, pushed = [], []
    replies = iter([_push(), json.dumps({'v': 1, 't': 2, 'm': 'handshake', 'b': {'a': {'c': 0}}})])
    ws = SimpleNamespace(send=sent.append, recv=lambda: next(replies))
    transport._wait_ack(ws, frame, threading.Event(), lambda raw: transport._receive_frame(raw, OWN, pushed.append))
    assert sent == [frame] and pushed[0]['platform_message_id'] == 'server-message'
    ws.recv = lambda: json.dumps({'m': 'handshake', 'b': {'a': {'c': 3100001}}})
    with pytest.raises(ValueError, match='拒绝'):
        transport._wait_ack(ws, frame, threading.Event(), pushed.append)


def test_receive_retries_network_and_manual_stop_interrupts_backoff(monkeypatch):
    stop = threading.Event()
    states, attempts, closed = [], [], []
    def load(_credentials):
        attempts.append(1)
        if len(attempts) == 1:
            raise websocket.WebSocketTimeoutException('private-url')
        stop.set()
        return SimpleNamespace(close=lambda: closed.append(1))
    monkeypatch.setattr(transport, '_load_auth', load)
    monkeypatch.setattr(im.XHSWebSocket, 'from_auth_storage', lambda auth: SimpleNamespace(sid='', uid=OWN))
    monkeypatch.setattr(im.XHSLiveAPI, '__init__', lambda self, auth: None)
    monkeypatch.setattr(im.XHSLiveAPI, 'get_celestial_lt', lambda self: {'success': True, 'code': 0})
    transport.receive_forever({}, lambda event: None, lambda state, error: states.append((state, error)), stop)
    assert len(attempts) == 2 and closed == [1]
    assert any(state == 'reconnecting' for state, error in states)
    assert all('private-url' not in error for state, error in states)


def test_silent_connection_sends_rwp_heartbeats_and_reconnects(monkeypatch):
    now, stop = [100.0], threading.Event()
    states, frames, closed, pending = [], [], [], []
    auth = SimpleNamespace(user_id=OWN, cookies_for_url=lambda url: {}, close=lambda: closed.append('auth'))
    client = im.XHSWebSocket(auth, uid=OWN, sid='test-token', device_id='device', fingerprint='fingerprint')
    monkeypatch.setattr(transport, '_load_auth', lambda credentials: auth)
    monkeypatch.setattr(im.XHSWebSocket, 'from_auth_storage', lambda auth: client)
    monkeypatch.setattr(transport.time, 'monotonic', lambda: now[0])
    def send(raw):
        frame = json.loads(raw)
        frames.append(frame)
        if frame.get('t') == 2:
            pending.append(json.dumps({'v': 1, 't': 2, 'm': frame['m'], 'b': {'a': {'c': 0}}}))
    def recv():
        if pending:
            return pending.pop(0)
        now[0] += 25
        raise websocket.WebSocketTimeoutException()
    ws = SimpleNamespace(send=send, recv=recv, settimeout=lambda seconds: None, close=lambda: closed.append('socket'))
    monkeypatch.setattr(websocket, 'create_connection', lambda *args, **kwargs: ws)
    def state(name, error):
        states.append(name)
        if name == 'reconnecting':
            stop.set()
    transport.receive_forever({}, lambda event: None, state, stop)
    assert states == ['connecting', 'connected', 'reconnecting']
    assert sum(frame['t'] == 0 for frame in frames) >= 2
    assert closed == ['socket', 'auth']
