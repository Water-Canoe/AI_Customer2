from __future__ import annotations

import json
import sys
import threading
from contextlib import nullcontext
from types import ModuleType

import pytest

from app import database, protected_settings
from app.services import account_center, douyin_sdk_session as session


@pytest.fixture
def sdk_session(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_CUSTOMER_DB', str(tmp_path / 'sdk-session.sqlite3'))
    monkeypatch.setenv('AI_CUSTOMER_DATA_DIR', str(tmp_path))
    database.init_db()
    account = account_center.create_account('dy', '扫码测试', 'operations', ['message', 'acquisition'])
    transport = ModuleType('app.services.douyin_sdk_transport')
    transport.session_status = lambda account_id: {'saved': True, 'uid': '12345', 'state': 'offline'}
    transport.login = lambda account_id, event, stop, **kwargs: {'sdk_uid': '12345'}
    import app.services as services
    monkeypatch.setitem(sys.modules, 'app.services.douyin_sdk_transport', transport)
    monkeypatch.setattr(services, 'douyin_sdk_transport', transport, raising=False)
    session._logins.clear()
    yield account['id'], transport
    session.shutdown()
    session._logins.clear()


def _guard(*args):
    # 测试扫码自身行为；账号互斥的并发检查由收件箱测试覆盖。
    return nullcontext()


def _wait(account_id):
    session._logins[account_id].thread.join(timeout=3)
    assert not session._logins[account_id].thread.is_alive()


def test_login_saves_bound_marker_and_only_marks_messaging_ready(sdk_session):
    account_id, _ = sdk_session
    browser_status = account_center.get_account(account_id)['feature_status']['acquisition']['status']
    old_auth = account_center.account_auth_path(account_id, 'user').parent / 'im-session.dpapi'
    old_auth.write_text('unchanged-rollback-auth', encoding='utf-8')
    session.start_login(account_id, _guard)
    _wait(account_id)
    assert session.login_status(account_id)['state'] == 'online'
    assert session.session_status(account_id)['send_ready']
    marker = session.load_credentials(account_id)
    assert marker['sdk_uid'] == '12345' and marker['account_id'] == account_id
    saved = session._marker_path(account_id).read_text(encoding='utf-8')
    assert saved.startswith(protected_settings.PREFIX) and '12345' not in saved
    assert old_auth.read_text(encoding='utf-8') == 'unchanged-rollback-auth'
    account = account_center.get_account(account_id)
    assert account['feature_status']['message']['status'] == 'ready'
    assert account['feature_status']['acquisition']['status'] == browser_status


def test_rescan_cannot_replace_an_existing_account_identity(sdk_session):
    account_id, transport = sdk_session
    session.start_login(account_id, _guard)
    _wait(account_id)
    original = session._marker_path(account_id).read_text(encoding='utf-8')
    def foreign_login(account_id, event, stop, *, expected_uid):
        assert expected_uid == '12345'
        return {'sdk_uid': '99999'}

    transport.login = foreign_login
    session.start_login(account_id, _guard)
    _wait(account_id)
    assert session.login_status(account_id)['state'] == 'error'
    assert '原绑定' in session.login_status(account_id)['error']
    assert session._marker_path(account_id).read_text(encoding='utf-8') == original
    transport.session_status = lambda _: {'saved': True, 'uid': '99999', 'state': 'offline'}
    assert not session.session_status(account_id)['send_ready']


def test_qrcode_remains_in_memory_and_cancel_stops_login(sdk_session):
    account_id, transport = sdk_session
    shown = threading.Event()

    def login(account_id, event, stop, **kwargs):
        event({'event': 'qrcode', 'image_base64': 'fake-qr'})
        shown.set()
        assert stop.wait(timeout=3)
        raise ValueError('用户取消扫码')

    transport.login = login
    session.start_login(account_id, _guard)
    assert shown.wait(timeout=3)
    assert session.login_status(account_id)['image_base64'] == 'fake-qr'
    assert not session._marker_path(account_id).exists()
    session.cancel_login(account_id)
    _wait(account_id)
    assert session.login_status(account_id) == {
        'state': 'cancelled', 'image_base64': '', 'verification_url': '', 'error': '',
    }


def test_marker_from_another_business_account_is_rejected(sdk_session):
    account_id, _ = sdk_session
    value = {'account_id': 'foreign', 'sdk': 'douyin-im', 'sdk_uid': '12345'}
    session._marker_path(account_id).write_text(
        protected_settings.protect(json.dumps(value)), encoding='utf-8')
    status = session.session_status(account_id)
    assert not status['receive_ready'] and '不匹配' in status['error']
    assert 'sdk_uid' not in status and 'account_id' not in status
