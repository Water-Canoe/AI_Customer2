from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app.services import douyin_im_transport as transport
from app.vendor.douyin_spider.static import Live_pb2, Response_pb2


@pytest.fixture(autouse=True)
def clear_send_sessions():
    # 离线测试之间不能共享认证缓存。
    yield
    for account_id in list(transport._send_auths):
        transport.close_send_auth(account_id)


def test_user_profile_requires_matching_uid_and_keeps_real_avatar(monkeypatch):
    monkeypatch.setattr(transport.DouyinAPI, 'get_user_profile', lambda *args: {
        'status_code': 0, 'user': {'uid': '22', 'nickname': '真实昵称', 'avatar_thumb': {'url_list': ['https://example.com/avatar.jpg']}},
    })
    assert transport.get_user_profile({'cookie_str': 'sessionid=test'}, '22') == {
        'peer_user_id': '22', 'nickname': '真实昵称', 'avatar': 'https://example.com/avatar.jpg',
    }
    with pytest.raises(ValueError, match='资料查询失败'):
        transport.get_user_profile({'cookie_str': 'sessionid=test'}, '33')


def test_sdk_tokens_are_account_scoped_and_failure_does_not_use_random_token(monkeypatch):
    from app.vendor.douyin_spider.utils import mstoken

    calls = []
    def post(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(headers={'x-ms-token': f'token-{len(calls)}'})
    monkeypatch.setattr(mstoken.requests, 'post', post)
    first = transport._load_auth({'cookie_str': 'sessionid=first'})
    second = transport._load_auth({'cookie_str': 'sessionid=second'})
    try:
        assert first.msToken == 'token-1'
        assert second.msToken == 'token-2'
        assert first.msToken == 'token-1' and len(calls) == 2
        second._ms_ts = 0
        monkeypatch.setattr(mstoken.requests, 'post', lambda *args, **kwargs: SimpleNamespace(headers={}))
        with pytest.raises(ValueError, match='令牌更新失败'):
            _ = second.msToken
    finally:
        first.close()
        second.close()


def _frame(*, sender: int = 22, conversation: str = '0:1:11:22', message_id: int = 99,
           message_type: int = 7, content: str = '{"text":"测试私信"}') -> bytes:
    # 构造已知协议帧验证账号隔离；不访问外网或真实账号。
    response = Response_pb2.Response()
    message = response.body.new_message_notify.message
    message.conversation_id = conversation
    message.conversation_short_id = 44
    message.server_message_id = message_id
    message.sender = sender
    message.message_type = message_type
    message.content = content
    frame = Live_pb2.PushFrame(payloadType='pb', payload=response.SerializeToString())
    return frame.SerializeToString()


def test_decode_preserves_real_id_and_account_direction():
    incoming = transport._decode_message(_frame(), '11')
    assert incoming['platform_message_id'] == '99'
    assert incoming['peer_user_id'] == '22'
    assert incoming['content'] == '测试私信'
    assert incoming['time_source'] == 'received'
    outgoing = transport._decode_message(_frame(sender=11), '11')
    assert outgoing['direction'] == 'outgoing'
    assert outgoing['peer_user_id'] == '22'
    with pytest.raises(ValueError, match='参与者'):
        transport._decode_message(_frame(conversation='0:1:22:33'), '11')
    with pytest.raises(ValueError, match='标识'):
        transport._decode_message(_frame(message_id=0), '11')


@pytest.mark.parametrize(('message_type', 'body', 'expected_raw', 'expected_content'), [
    (7, 'not-json', {'unparsed_content': 'not-json'}, '[文本消息暂无法解析，请在抖音查看]'),
    (7, '', {'unparsed_content': ''}, '[文本消息暂无法解析，请在抖音查看]'),
    (7, 'null', {'unparsed_content': 'null'}, '[文本消息暂无法解析，请在抖音查看]'),
    (7, '[]', {'unparsed_content': '[]'}, '[文本消息暂无法解析，请在抖音查看]'),
    (7, '{"other":"未识别字段"}', {'other': '未识别字段'}, '[文本消息暂无法解析，请在抖音查看]'),
    (27, '', {'unparsed_content': ''}, '[图片]'),
    (999, 'opaque-body', {'unparsed_content': 'opaque-body'}, '[消息类型 999]'),
])
def test_unrecognised_message_content_keeps_original_and_visible_notice(message_type, body, expected_raw, expected_content):
    event = transport._decode_message(_frame(message_type=message_type, content=body), '11')
    assert event['content'] == expected_content
    assert event['raw_content'] == expected_raw
    assert event['platform_message_id'] == '99'
    assert event['peer_user_id'] == '22'


def test_bad_content_does_not_stop_the_next_message(monkeypatch):
    stop = threading.Event()
    states, events, sockets = [], [], []

    class FakeSocket:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            sockets.append(self)

        def run_forever(self, **kwargs):
            self.kwargs['on_open'](self)
            # 同一连接先收到异常正文，再收到正常客户回复。
            self.kwargs['on_message'](self, _frame(message_id=98, content='not-json'))
            self.kwargs['on_message'](self, _frame(message_id=99))
            stop.set()

        def close(self):
            pass

    monkeypatch.setattr(transport.websocket, 'WebSocketApp', FakeSocket)
    transport.receive_forever(
        {'cookie_str': 'sessionid=offline-test', 'user_id': '11', 'device_id': '33'},
        events.append, lambda state, error='': states.append(state), stop)
    assert len(sockets) == 1
    assert [event['platform_message_id'] for event in events] == ['98', '99']
    assert events[-1]['content'] == '测试私信'
    assert states == ['connecting', 'connected', 'stopped']


def test_sending_requires_session_material_and_numeric_peer():
    with pytest.raises(ValueError, match='数字用户'):
        transport.send_text({}, 'MS4wLjAB', '测试', account_id='account-1')
    with pytest.raises(ValueError, match='认证材料不完整'):
        transport.send_text({'cookie_str': 'sessionid=offline-test', 'user_id': '11'}, '22', '测试', account_id='account-1')


def test_server_close_reconnects_and_stop_finishes(monkeypatch):
    stop = threading.Event()
    states = []
    sockets = []

    class FakeSocket:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            sockets.append(self)

        def run_forever(self, **kwargs):
            self.kwargs['on_open'](self)
            self.kwargs['on_close'](self, 1000, 'server close')
            if len(sockets) == 2:
                stop.set()

        def close(self):
            pass

    monkeypatch.setattr(transport.websocket, 'WebSocketApp', FakeSocket)
    transport.receive_forever(
        {'cookie_str': 'sessionid=offline-test', 'user_id': '11', 'device_id': '33'},
        lambda event: None, lambda state, error='': states.append(state), stop)
    assert len(sockets) == 2
    assert states == ['connecting', 'connected', 'reconnecting', 'connected', 'stopped']


def test_message_save_failure_stops_listener(monkeypatch):
    stop = threading.Event()

    class FakeSocket:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run_forever(self, **kwargs):
            self.kwargs['on_message'](self, _frame())

        def close(self):
            pass

    def save_failed(event):
        raise RuntimeError('database save failed')

    monkeypatch.setattr(transport.websocket, 'WebSocketApp', FakeSocket)
    with pytest.raises(RuntimeError, match='database save failed'):
        transport.receive_forever(
            {'cookie_str': 'sessionid=offline-test', 'user_id': '11', 'device_id': '33'},
            save_failed, lambda state, error='': None, stop)


def test_transient_preparation_and_socket_errors_retry_with_current_auth(monkeypatch):
    stop = threading.Event()
    waits, sockets, states = [], [], []
    monkeypatch.setattr(stop, 'wait', lambda delay: waits.append(delay) or stop.is_set())
    original_url = transport._websocket_url
    attempts = []

    def prepare(auth):
        attempts.append(auth)
        if len(attempts) == 1:
            raise OSError('offline')
        return original_url(auth)

    class FakeSocket:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            sockets.append(self)

        def run_forever(self, **kwargs):
            self.kwargs['on_open'](self)
            if len(sockets) == 1:
                attempts[-1].cookie['sessionid'] = 'updated-test-session'
                raise transport.websocket.WebSocketTimeoutException('network timeout')
            stop.set()

        def close(self):
            pass

    monkeypatch.setattr(transport, '_websocket_url', prepare)
    monkeypatch.setattr(transport.websocket, 'WebSocketApp', FakeSocket)
    transport.receive_forever({'cookie_str': 'sessionid=offline-test', 'user_id': '11', 'device_id': '33'},
                             lambda event: None, lambda state, error='': states.append(state), stop)
    assert len(sockets) == 2 and len(attempts) == 3
    assert waits == [1, 2]
    assert 'token=updated-test-session' in sockets[1].kwargs['url']
    assert 'sessionid=updated-test-session' in sockets[1].kwargs['cookie']
    assert states[-2:] == ['connected', 'stopped']


def test_heartbeat_detects_silent_connection_but_pongs_keep_idle_chat_alive(monkeypatch):
    stop = threading.Event()
    clock = [1.0]
    checked, closed = threading.Event(), threading.Event()
    sockets, states = [], []

    def now():
        if threading.current_thread().name == 'douyin-im-stop':
            checked.set()
        return clock[0]

    monkeypatch.setattr(transport, 'time', SimpleNamespace(monotonic=now))
    monkeypatch.setattr(stop, 'wait', lambda delay: stop.is_set())

    class FakeSocket:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            sockets.append(self)

        def run_forever(self, **kwargs):
            assert kwargs['ping_interval'] == 25 and kwargs['ping_timeout'] == 10
            self.kwargs['on_open'](self)
            if len(sockets) == 2:
                stop.set()
                return
            # 无客户消息，服务器心跳回复仍维持连接；模拟时钟避免真实等待 75 秒。
            clock[0] += 70
            self.kwargs['on_pong'](self, b'')
            checked.clear()
            assert checked.wait(2)
            assert not closed.is_set()
            clock[0] += 76
            assert closed.wait(2), '静默连接没有被健康检测关闭'

        def close(self):
            closed.set()

    monkeypatch.setattr(transport.websocket, 'WebSocketApp', FakeSocket)
    transport.receive_forever({'cookie_str': 'sessionid=offline-test', 'user_id': '11', 'device_id': '33'},
                             lambda event: None, lambda state, error='': states.append((state, error)), stop)
    assert len(sockets) == 2
    assert any(state == 'reconnecting' and '心跳超时' in error for state, error in states)


@pytest.mark.parametrize(('code', 'state'), [(401, 'expired'), (403, 'expired'), (400, 'error')])
def test_rejected_auth_or_connection_does_not_retry_or_expose_credentials(monkeypatch, code, state):
    stop = threading.Event()
    states, sockets = [], []

    class FakeSocket:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            sockets.append(self)

        def run_forever(self, **kwargs):
            self.kwargs['on_error'](self, transport.websocket.WebSocketBadStatusException('token=private-secret', status_code=code))

        def close(self):
            pass

    monkeypatch.setattr(transport.websocket, 'WebSocketApp', FakeSocket)
    with pytest.raises(ValueError):
        transport.receive_forever({'cookie_str': 'sessionid=offline-test', 'user_id': '11', 'device_id': '33'},
                                 lambda event: None, lambda value, error='': states.append((value, error)), stop)
    assert len(sockets) == 1 and states[-1][0] == state
    assert 'private-secret' not in str(states)


def test_heartbeat_race_does_not_overwrite_auth_failure(monkeypatch):
    stop = threading.Event()
    clock = [1.0]
    checking, release, watcher_closed = threading.Event(), threading.Event(), threading.Event()
    states = []

    def now():
        if threading.current_thread().name == 'douyin-im-stop' and clock[0] > 75 and not checking.is_set():
            checking.set()
            assert release.wait(2)
        return clock[0]

    class FakeSocket:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run_forever(self, **kwargs):
            self.kwargs['on_open'](self)
            clock[0] += 76
            assert checking.wait(2)
            # 超时判断与认证失败同时发生时，停止原因不能被“正在重连”覆盖。
            self.kwargs['on_error'](self, transport.websocket.WebSocketBadStatusException('rejected', status_code=403))
            release.set()
            assert watcher_closed.wait(2)

        def close(self):
            if threading.current_thread().name == 'douyin-im-stop':
                watcher_closed.set()

    monkeypatch.setattr(transport, 'time', SimpleNamespace(monotonic=now))
    monkeypatch.setattr(transport.websocket, 'WebSocketApp', FakeSocket)
    with pytest.raises(ValueError):
        transport.receive_forever({'cookie_str': 'sessionid=offline-test', 'user_id': '11', 'device_id': '33'},
                                 lambda event: None, lambda state, error='': states.append((state, error)), stop)
    assert states[-1][0] == 'expired' and '登录已失效' in states[-1][1]


def test_stable_connection_resets_backoff_and_stop_interrupts_retry_wait(monkeypatch):
    stop = threading.Event()
    clock = [1.0]
    waits, sockets = [], []
    monkeypatch.setattr(transport, 'time', SimpleNamespace(monotonic=lambda: clock[0]))

    def wait(delay):
        waits.append(delay)
        if len(waits) == 3:
            stop.set()
        return stop.is_set()

    monkeypatch.setattr(stop, 'wait', wait)

    class FakeSocket:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            sockets.append(self)

        def run_forever(self, **kwargs):
            self.kwargs['on_open'](self)
            if len(sockets) == 3:
                clock[0] += 61
                self.kwargs['on_pong'](self, b'')
            self.kwargs['on_close'](self, 1000, '')

        def close(self):
            pass

    monkeypatch.setattr(transport.websocket, 'WebSocketApp', FakeSocket)
    transport.receive_forever({'cookie_str': 'sessionid=offline-test', 'user_id': '11', 'device_id': '33'},
                             lambda event: None, lambda state, error='': None, stop)
    assert waits == [1, 2, 1] and len(sockets) == 3


def test_external_stop_closes_blocking_socket(monkeypatch):
    # 持续联网无新消息时，停止仍能打断阻塞监听。
    stop = threading.Event()
    opened = threading.Event()
    closed = threading.Event()
    errors = []

    class FakeSocket:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run_forever(self, **kwargs):
            self.kwargs['on_open'](self)
            opened.set()
            assert closed.wait(2), '停止没有关闭阻塞连接'

        def close(self):
            closed.set()

    def listen():
        try:
            transport.receive_forever(
                {'cookie_str': 'sessionid=offline-test', 'user_id': '11', 'device_id': '33'},
                lambda event: None, lambda state, error='': None, stop)
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr(transport.websocket, 'WebSocketApp', FakeSocket)
    worker = threading.Thread(target=listen, daemon=True)
    worker.start()
    assert opened.wait(1)
    stop.set()
    worker.join(1)
    assert not worker.is_alive()
    assert not errors


def test_unknown_send_result_is_not_retried(monkeypatch):
    calls = []

    def unknown_result(*args, **kwargs):
        calls.append(kwargs['client_message_id'])
        raise transport.MessageSendUncertain('offline timeout')

    monkeypatch.setattr(transport.DouyinAPI, '_send_message_raw', unknown_result)
    credentials = {'cookie_str': 'sessionid=offline-test', 'user_id': '11',
                   'ticket': 'test-ticket', 'ts_sign': 'test-sign', 'private_key': 'test-key'}
    with pytest.raises(transport.TransportSendUncertain) as result:
        transport.send_text(credentials, '22', '测试', '0:1:11:22', 44, 'test-ticket', account_id='account-1')
    assert len(calls) == 1
    assert result.value.result['status'] == 'uncertain'
    assert result.value.result['conversation_id'] == '0:1:11:22'
    assert result.value.result['client_message_id'] == calls[0]


@pytest.mark.parametrize(('response', 'status'), [
    ({'message': 'OK', 'http_status': 200}, 'accepted'),
    ({'message': 'REJECTED', 'http_status': 403}, 'failed'),
])
def test_only_explicit_platform_acceptance_is_accepted(monkeypatch, response, status):
    monkeypatch.setattr(transport.DouyinAPI, '_send_message_raw', lambda *args, **kwargs: response)
    credentials = {'cookie_str': 'sessionid=offline-test', 'user_id': '11',
                   'ticket': 'test-ticket', 'ts_sign': 'test-sign', 'private_key': 'test-key'}
    result = transport.send_text(credentials, '22', '测试', '0:1:11:22', 44, 'test-ticket', account_id='account-1')
    assert result['status'] == status
    assert result['platform_message_id'] == ''


def test_sends_reuse_auth_isolate_accounts_and_replace_changed_credentials(monkeypatch):
    used = []
    closed = []
    monkeypatch.setattr(transport.DouyinAuth, 'close', lambda auth: closed.append(auth))
    def send(auth, *args, **kwargs):
        used.append(auth)
        # 运行中的 Cookie 更新不应被误判为原始认证文件变化。
        auth.cookie['runtime-cookie'] = 'response-value'
        return {'message': 'OK', 'http_status': 200}
    monkeypatch.setattr(transport.DouyinAPI, '_send_message_raw', send)
    credentials = {'cookie_str': 'sessionid=offline-test', 'user_id': '11',
                   'ticket': 'test-ticket', 'ts_sign': 'test-sign', 'private_key': 'test-key'}
    def submit(account_id, material):
        return transport.send_text(material, '22', '测试', '0:1:11:22', 44, 'test-ticket', account_id=account_id)

    submit('first', credentials)
    submit('first', dict(credentials))
    submit('second', credentials)
    assert used[0] is used[1] and used[0] is not used[2] and not closed
    submit('first', {**credentials, 'ticket': 'new-ticket'})
    assert used[3] is not used[0] and closed == [used[0]]
    transport.close_send_auth('first')
    assert closed == [used[0], used[3]] and 'second' in transport._send_auths
    submit('first', credentials)
    assert used[4] is not used[3]


def test_reused_auth_keeps_token_cache_and_renews_expired_tokens(monkeypatch):
    from app.vendor.douyin_spider.builder.header import Header
    from app.vendor.douyin_spider.dy_apis.douyin_api import DouyinAPI
    from app.vendor.douyin_spider.utils import mstoken

    token_calls, identity_calls = [], []
    def sdk_token(*args, **kwargs):
        token_calls.append(True)
        return SimpleNamespace(headers={'x-ms-token': f'sdk-{len(token_calls)}'})
    def identity_token(*args, **kwargs):
        identity_calls.append(True)
        return SimpleNamespace(text='{}', headers={}, json=lambda: {
            'message': 'success', 'data': {'identity_security_token': f'identity-{len(identity_calls)}', 'device_id': '33'},
        })
    monkeypatch.setattr(mstoken.requests, 'post', sdk_token)
    monkeypatch.setattr(mstoken.requests, 'get', identity_token)
    monkeypatch.setattr(Header, 'with_bd', lambda self, *args, **kwargs: self)
    def send(auth, *args, **kwargs):
        # 仍使用协议层的有效期判断，只替换最终真实发送。
        _ = auth.msToken
        DouyinAPI.get_identity_security_token(auth)
        return {'message': 'OK', 'http_status': 200}
    monkeypatch.setattr(DouyinAPI, '_send_message_raw', send)
    credentials = {'cookie_str': 'sessionid=offline-test', 'user_id': '11',
                   'ticket': 'test-ticket', 'ts_sign': 'test-sign', 'private_key': 'test-key'}
    for _ in range(2):
        transport.send_text(credentials, '22', '测试', '0:1:11:22', 44, 'test-ticket', account_id='first')
    assert len(token_calls) == len(identity_calls) == 1
    auth = transport._send_auths['first'][1]
    auth._ms_ts = auth.identity_security_token_ts = 0
    transport.send_text(credentials, '22', '测试', '0:1:11:22', 44, 'test-ticket', account_id='first')
    assert len(token_calls) == len(identity_calls) == 2
