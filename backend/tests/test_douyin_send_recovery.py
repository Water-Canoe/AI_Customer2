"""离线验证发送准备重试；最终消息提交始终只允许一次。"""
from types import SimpleNamespace

import pytest
from curl_cffi import CurlError

from app.services import douyin_im_transport as transport
from app.vendor.douyin_spider.builder.header import Header
from app.vendor.douyin_spider.dy_apis import douyin_api
from app.vendor.douyin_spider.static import Response_pb2


@pytest.fixture
def sending(monkeypatch):
    credentials = {'cookie_str': 'sessionid=offline-test', 'user_id': '11',
                   'ticket': 'test-ticket', 'ts_sign': 'test-sign', 'private_key': 'test-key'}
    auth = transport._send_auth('recovery-test', credentials)
    calls = {'sdk': 0, 'identity': 0, 'send': 0, 'close': 0}
    failures = {}

    def fail(stage):
        calls[stage] += 1
        remaining = failures.get(stage, 0)
        if remaining:
            failures[stage] -= 1
            raise CurlError('sessionid=private-secret; text=private-message', code=28)

    def post(url, **kwargs):
        if '/web/r/token' in url:
            fail('sdk')
            return SimpleNamespace(headers={'x-ms-token': 'renewed-token'})
        assert url.endswith('/v1/message/send')
        fail('send')
        response = Response_pb2.Response(message='OK')
        return SimpleNamespace(status_code=200, content=response.SerializeToString())

    def get(url, **kwargs):
        fail('identity')
        return SimpleNamespace(text='{}', headers={}, json=lambda: {
            'message': 'success', 'data': {'identity_security_token': 'identity', 'device_id': '33'},
        })

    def close():
        calls['close'] += 1

    auth.http = SimpleNamespace(close=close)
    auth.cookie['runtime-cookie'] = 'keep-this'
    # 模拟曾经使用过、现在令牌已过期的账号，不等待真实的有效期。
    auth._ms_cache, auth._ms_ts = 'old-token', 0
    auth.identity_security_token, auth.identity_security_token_ts = 'old-identity', 0
    monkeypatch.setattr(douyin_api.requests, 'post', post)
    monkeypatch.setattr(douyin_api.requests, 'get', get)
    monkeypatch.setattr(Header, 'with_bd', lambda self, *args: self)
    monkeypatch.setattr(douyin_api, 'generate_a_bogus', lambda *args, **kwargs: 'offline-sign')
    monkeypatch.setattr(douyin_api.ProtoBuilder, 'build_send_message_request',
                        lambda *args, **kwargs: SimpleNamespace(SerializeToString=lambda: b'offline-payload'))

    def send(**kwargs):
        return transport.send_text(credentials, '22', 'private-message',
                                   kwargs.get('conversation_id', '0:1:11:22'),
                                   kwargs.get('conversation_short_id', 44),
                                   kwargs.get('ticket', 'test-ticket'), account_id='recovery-test')

    yield SimpleNamespace(auth=auth, calls=calls, failures=failures, send=send)
    transport.close_send_auth('recovery-test')


@pytest.mark.parametrize(('stage', 'logged_stage'), [('sdk', 'identity_token'), ('identity', 'identity_token')])
def test_idle_token_network_failure_retries_before_one_actual_send(sending, caplog, stage, logged_stage):
    sending.failures[stage] = 1
    result = sending.send()
    assert result['status'] == 'accepted'
    assert sending.calls[stage] == 2
    assert sending.calls['send'] == sending.calls['close'] == 1
    assert sending.auth.cookie['runtime-cookie'] == 'keep-this'
    assert transport._send_auths['recovery-test'][1] is sending.auth
    assert logged_stage in caplog.text and 'CurlError' in caplog.text
    assert 'private-secret' not in caplog.text and 'private-message' not in caplog.text


def test_preparation_retry_is_bounded_and_never_submits_on_failure(sending, caplog):
    sending.failures['identity'] = 2
    with pytest.raises(ValueError):
        sending.send()
    assert sending.calls['identity'] == 2 and sending.calls['send'] == 0
    assert sending.calls['close'] == 1
    assert 'retry=False' in caplog.text


def test_reopened_http_session_reseeds_current_account_cookies():
    auth = transport.DouyinAuth()
    auth.perepare_auth('sessionid=offline-test')
    try:
        first = auth.ensure_http_session()
        auth.cookie['runtime-cookie'] = 'renewed-value'
        auth.ensure_http_session()
        auth.close()
        second = auth.ensure_http_session()
        assert first is not second
        assert second.cookies.get('sessionid') == 'offline-test'
        assert second.cookies.get('runtime-cookie') == 'renewed-value'
    finally:
        auth.close()


def test_conversation_preparation_network_failure_is_safe_to_retry(sending, monkeypatch, caplog):
    calls = []

    def create(*args):
        calls.append(True)
        if len(calls) == 1:
            raise CurlError('private-secret', code=56)
        return '0:1:11:22', 44, 'test-ticket'

    monkeypatch.setattr(transport.DouyinAPI, 'create_conversation', create)
    assert sending.send(conversation_id='', conversation_short_id=0, ticket='')['status'] == 'accepted'
    assert len(calls) == 2 and sending.calls['send'] == 1
    assert 'conversation' in caplog.text and 'private-secret' not in caplog.text


def test_retry_after_conversation_created_keeps_one_conversation_and_message_id(sending, monkeypatch):
    created, message_ids = [], []
    raw_send = transport.DouyinAPI._send_message_raw

    def create(*args):
        created.append(True)
        return '0:1:11:22', 44, 'test-ticket'

    def send(*args, **kwargs):
        message_ids.append(kwargs['client_message_id'])
        return raw_send(*args, **kwargs)

    monkeypatch.setattr(transport.DouyinAPI, 'create_conversation', create)
    monkeypatch.setattr(transport.DouyinAPI, '_send_message_raw', send)
    sending.failures['identity'] = 1
    assert sending.send(conversation_id='', conversation_short_id=0, ticket='')['status'] == 'accepted'
    assert len(created) == 1 and len(message_ids) == 2
    assert message_ids[0] == message_ids[1] and sending.calls['send'] == 1


def test_final_send_timeout_is_uncertain_without_retry(sending):
    sending.failures['send'] = 1
    with pytest.raises(transport.TransportSendUncertain):
        sending.send()
    assert sending.calls['send'] == 1 and sending.calls['close'] == 0


def test_invalid_serialization_is_preparation_failure_without_submission(sending, monkeypatch, caplog):
    def broken_payload():
        raise ValueError('private-message')

    monkeypatch.setattr(douyin_api.ProtoBuilder, 'build_send_message_request',
                        lambda *args, **kwargs: SimpleNamespace(SerializeToString=broken_payload))
    with pytest.raises(ValueError):
        sending.send()
    assert sending.calls['send'] == 0
    assert 'message_payload' in caplog.text and 'private-message' not in caplog.text


@pytest.mark.parametrize('error', [RuntimeError('private-secret'), CurlError('private-secret', code=60)])
def test_auth_refusal_and_certificate_failure_are_not_retried(sending, monkeypatch, caplog, error):
    def rejected(*args, **kwargs):
        sending.calls['identity'] += 1
        raise error

    monkeypatch.setattr(douyin_api.DouyinAPI, 'get_identity_security_token', rejected)
    with pytest.raises(ValueError):
        sending.send()
    assert sending.calls['identity'] == 1 and sending.calls['send'] == 0
    assert sending.calls['close'] == 0 and 'private-secret' not in caplog.text


@pytest.mark.parametrize('status', [403, 503])
def test_final_http_response_does_not_trigger_resend(sending, monkeypatch, status):
    def post(url, **kwargs):
        sending.calls['send'] += 1
        return SimpleNamespace(status_code=status)

    sending.auth._ms_ts = transport.time.time()
    sending.auth.identity_security_token_ts = transport.time.time()
    monkeypatch.setattr(douyin_api.requests, 'post', post)
    if status == 503:
        with pytest.raises(transport.TransportSendUncertain):
            sending.send()
    else:
        assert sending.send()['status'] == 'failed'
    assert sending.calls['send'] == 1 and sending.calls['close'] == 0
