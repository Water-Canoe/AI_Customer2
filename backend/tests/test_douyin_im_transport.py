from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app.services import douyin_im_transport as transport
from app.vendor.douyin_spider.static import Live_pb2, Response_pb2


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


def _frame(*, sender: int = 22, conversation: str = '0:1:11:22', message_id: int = 99) -> bytes:
    # 构造已知协议帧验证账号隔离；不访问外网或真实账号。
    response = Response_pb2.Response()
    message = response.body.new_message_notify.message
    message.conversation_id = conversation
    message.conversation_short_id = 44
    message.server_message_id = message_id
    message.sender = sender
    message.message_type = 7
    message.content = '{"text":"测试私信"}'
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


def test_sending_requires_session_material_and_numeric_peer():
    with pytest.raises(ValueError, match='数字用户'):
        transport.send_text({}, 'MS4wLjAB', '测试')
    with pytest.raises(ValueError, match='认证材料不完整'):
        transport.send_text({'cookie_str': 'sessionid=offline-test', 'user_id': '11'}, '22', '测试')


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
        transport.send_text(credentials, '22', '测试', '0:1:11:22', 44, 'test-ticket')
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
    result = transport.send_text(credentials, '22', '测试', '0:1:11:22', 44, 'test-ticket')
    assert result['status'] == status
    assert result['platform_message_id'] == ''
