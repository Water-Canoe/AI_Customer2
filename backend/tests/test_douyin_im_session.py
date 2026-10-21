from __future__ import annotations

import asyncio
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app import database, protected_settings
from app.services import account_center, douyin_im_session as session


def _key() -> str:
    return ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()


def _snapshot(key: str, sign: str = 'sign-1.signature') -> dict:
    # 模拟上游明确使用的 data 双层 JSON，而非真实账号凭据。
    return {'origins': [{'origin': 'https://www.douyin.com', 'localStorage': [
        {'name': 'security-sdk/s_sdk_sign_data_key/web_protect', 'value': json.dumps({'data': json.dumps({'ticket': 'test-ticket', 'ts_sign': sign})})},
        {'name': 'security-sdk/s_sdk_crypt_sdk', 'value': json.dumps({'data': json.dumps({'ec_privateKey': key})})},
    ]}]}


def test_snapshot_extracts_matching_credentials_without_reusing_another_login():
    key = _key()
    cookies = [{'name': 'sessionid', 'value': 'new-login'}, {'name': 'bd_ticket_guard_ts_sign_id', 'value': 'sign-1'}]
    captured = session.credentials_from_snapshot(cookies, _snapshot(key))
    assert captured['ticket'] == 'test-ticket'
    assert captured['private_key'] == key
    result = session.credentials_from_snapshot(cookies, {'origins': []})
    assert 'private_key' not in result and 'ticket' not in result
    mismatched = session.credentials_from_snapshot(cookies, _snapshot(key, 'other.signature'))
    assert 'ticket' not in mismatched and 'private_key' not in mismatched
    foreign = _snapshot(key)
    foreign['origins'][0]['origin'] = 'https://example.com'
    assert 'private_key' not in session.credentials_from_snapshot(cookies, foreign)


def test_ambiguous_private_keys_do_not_enable_sending():
    snapshot = _snapshot(_key())
    snapshot['origins'][0]['localStorage'][1]['value'] = json.dumps([{'ec_privateKey': _key()}, {'ec_privateKey': _key()}])
    result = session.credentials_from_snapshot([{'name': 'sessionid', 'value': 'login'}], snapshot)
    assert 'ticket' in result and 'private_key' not in result


def test_snapshot_separates_web_protect_ticket_from_login_and_account_records():
    # 新登录会同时留下不同场景的票据；私信只使用 web_protect，不猜选最新时间。
    key = _key()
    snapshot = _snapshot(key)
    login_record = {'ticket': 'login-ticket', 'ts_sign': 'login-sign.signature', 'create_time': 9999999999}
    snapshot['origins'][0]['localStorage'].extend([
        {'name': 'security-sdk/s_sdk_sign_data_key/login', 'value': json.dumps({'data': json.dumps(login_record)})},
        {'name': 'user_info_passport_new', 'value': json.dumps({'ticket_guard': login_record})},
        {'name': 'unrelated-cache', 'value': json.dumps({'ec_privateKey': _key()})},
    ])
    result = session.credentials_from_snapshot([{'name': 'sessionid', 'value': 'login'}], snapshot)
    assert result['ticket'] == 'test-ticket'
    assert result['ts_sign'] == 'sign-1.signature'
    assert result['private_key'] == key


def test_login_ticket_alone_does_not_enable_private_message_sending():
    snapshot = _snapshot(_key())
    snapshot['origins'][0]['localStorage'][0]['name'] = 'security-sdk/s_sdk_sign_data_key/login'
    result = session.credentials_from_snapshot([{'name': 'sessionid', 'value': 'login'}], snapshot)
    assert 'ticket' not in result and 'ts_sign' not in result


def test_capture_protects_credentials_and_status_does_not_expose_them(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_CUSTOMER_DB', str(tmp_path / 'session.sqlite3'))
    monkeypatch.setenv('AI_CUSTOMER_DATA_DIR', str(tmp_path))
    database.init_db()
    account = account_center.create_account('dy', '认证测试', 'service', ['message'])
    key = _key()

    class Context:
        async def cookies(self, url):
            assert url == 'https://www.douyin.com/'
            return [{'name': 'sessionid', 'value': 'private-session'}]

        async def storage_state(self, **kwargs):
            assert kwargs == {'indexed_db': True}
            return _snapshot(key)

    status = asyncio.run(session.capture_context(account['id'], Context()))
    assert status['receive_ready'] and status['send_ready']
    assert all(field not in status for field in ('cookie_str', 'private_key', 'ticket', 'ts_sign'))
    saved = session._session_path(account['id']).read_text(encoding='utf-8')
    assert saved.startswith(protected_settings.PREFIX)
    assert 'private-session' not in saved and key not in saved
    decoded = session.load_credentials(account['id'])
    assert decoded['private_key'] == key
    decoded['account_id'] = 'different-account'
    session._session_path(account['id']).write_text(protected_settings.protect(json.dumps(decoded)), encoding='utf-8')
    assert not session.session_status(account['id'])['receive_ready']
    with pytest.raises(ValueError, match='不匹配'):
        session.load_credentials(account['id'])
