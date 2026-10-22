from __future__ import annotations

import asyncio
import json
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from app import database
from app.services import account_center, message_inbox


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_CUSTOMER_DB', str(tmp_path / 'inbox.sqlite3'))
    monkeypatch.setenv('AI_CUSTOMER_DATA_DIR', str(tmp_path))
    database.init_db()
    from app import services

    session = ModuleType('app.services.im_session')
    session.session_status = lambda account_id: {'receive_ready': True, 'send_ready': True, 'error': '', 'synced_at': ''}
    session.load_credentials = lambda account_id: {'cookie_str': 'private-test-credential'}
    session.shutdown = lambda: None
    transport = ModuleType('app.services.douyin_sdk_transport')
    transport.send_text = lambda *args, **kwargs: {'status': 'accepted'}
    transport.close_send_auth = lambda account_id: None
    transport.TransportSendUncertain = type('TransportSendUncertain', (RuntimeError,), {})
    for name, module in [('im_session', session), ('douyin_sdk_session', session), ('douyin_sdk_transport', transport)]:
        monkeypatch.setitem(sys.modules, f'app.services.{name}', module)
        monkeypatch.setattr(services, name, module, raising=False)

    monkeypatch.setattr(message_inbox, 'im_session', session)
    monkeypatch.setattr(message_inbox.license_service, 'ensure_authorized', lambda: {'authorized': True})

    def account(name, platform='dy'):
        result = account_center.create_account(platform, name, 'service', ['message'])
        account_center.set_login_status(result['id'], 'user', 'ready')
        return result['id']

    yield account, transport
    message_inbox.shutdown()
    with message_inbox._runtime_lock:
        message_inbox._listeners.clear()
        message_inbox._send_locks.clear()
        message_inbox._profile_locks.clear()
        message_inbox._profile_checked.clear()
        message_inbox._profile_errors.clear()
        message_inbox._login_sessions.clear()


def _event(message_id, content='你好'):
    return {'platform_message_id': message_id, 'peer_user_id': '123456', 'conversation_id': '0:1:123456:987654', 'conversation_short_id': 22, 'content': content, 'raw_content': {'text': content}, 'direction': 'incoming'}


def test_xhs_uses_same_inbox_storage_status_and_platform_ownership(inbox, monkeypatch):
    account, transport = inbox
    from app.services import message_workbench
    monkeypatch.setattr(message_inbox, '_transport_for_platform', lambda platform: transport)
    xhs_account, dy_account = account('小红书客服', 'xhs'), account('抖音客服')
    peer = '65abcdef1234567890abcdef'
    with database.connect() as conn:
        user = conn.execute("INSERT INTO user_accounts(platform,platform_user_id,nickname) VALUES('xhs',?,'小红书客户')", (peer,)).lastrowid
        lead = conn.execute("INSERT INTO lead_user_accounts(account_id,screening_status,follow_status,script) VALUES(?,'目标客户','未私信','首次私信')", (user,)).lastrowid
        conn.execute("INSERT INTO lead_sources(lead_account_id,source_type,keyword) VALUES(?,'comment','小红书获客')", (lead,))
    conversation = message_inbox.create_conversation(xhs_account, lead_id=lead)
    with pytest.raises(ValueError, match='平台'):
        message_inbox.create_conversation(dy_account, lead_id=lead)
    with pytest.raises(ValueError, match='小红书'):
        message_inbox.create_conversation(xhs_account, peer_user_id='123456')
    # 自动私信沿用真实收件箱存储及状态逻辑，只模拟外部协议请求。
    with pytest.raises(ValueError, match='平台'):
        asyncio.run(message_workbench.auto_message_customer(lead, account_id=dy_account))
    result = asyncio.run(message_workbench.auto_message_customer(lead, account_id=xhs_account))
    assert result['dm']['status'] == 'accepted'
    with database.connect() as conn:
        assert conn.execute('SELECT follow_status FROM lead_user_accounts WHERE id=?', (lead,)).fetchone()[0] == '已私信'
        assert conn.execute('SELECT COUNT(*) FROM message_send_attempts').fetchone()[0] == 1
    message_inbox.hide_conversation(xhs_account, conversation['id'])
    event = {'platform_message_id': 'xhs-message', 'peer_user_id': peer, 'conversation_id': peer,
             'direction': 'incoming', 'content': '客户回复', 'avatar': 'https://example.com/avatar.jpg'}
    message_inbox.record_received(xhs_account, event)
    message_inbox.record_received(xhs_account, event)
    saved = message_inbox.list_conversations(xhs_account)
    assert len(saved) == 1 and saved[0]['unread_count'] == 1 and saved[0]['lead_id'] == lead
    assert message_inbox.list_conversations(dy_account) == []
    assert len(message_inbox.list_messages(xhs_account, conversation['id'])['messages']) == 2
    with database.connect() as conn:
        assert conn.execute('SELECT follow_status FROM lead_user_accounts WHERE id=?', (lead,)).fetchone()[0] == '已回复'


@pytest.mark.parametrize('original', ['', 'not-json'])
def test_unparsed_message_preserves_original_in_local_history(inbox, original):
    account, _ = inbox
    account_id = account('异常内容测试')
    event = _event('unparsed-content', '[文本消息暂无法解析，请在抖音查看]')
    event['raw_content'] = {'unparsed_content': original}
    message_inbox.record_received(account_id, event)
    conversation = message_inbox.list_conversations(account_id)[0]
    saved = message_inbox.list_messages(account_id, conversation['id'])['messages'][0]
    assert saved['content'] == event['content']
    assert json.loads(saved['raw_content']) == {'unparsed_content': original}


def test_received_deduplication_pagination_and_account_isolation(inbox):
    account, _ = inbox
    first, second = account('客服一'), account('客服二')
    message_inbox.record_received(first, _event('m1'))
    message_inbox.record_received(first, _event('m1'))
    message_inbox.record_received(first, _event('m2', '第二条'))
    message_inbox.record_received(second, _event('m1'))
    conversation = message_inbox.list_conversations(first)[0]
    assert conversation['unread_count'] == 2
    assert 'ticket' not in conversation
    newest = message_inbox.list_messages(first, conversation['id'], limit=1)
    assert newest['has_more'] and newest['messages'][0]['content'] == '第二条'
    older = message_inbox.list_messages(first, conversation['id'], before_id=newest['messages'][0]['id'], limit=1)
    assert not older['has_more'] and older['messages'][0]['content'] == '你好'
    assert older['messages'][0]['raw_content'] == '{"text": "你好"}'
    with pytest.raises(ValueError, match='会话不存在'):
        message_inbox.list_messages(second, conversation['id'])
    with pytest.raises(ValueError, match='会话不存在'):
        message_inbox.send_message(second, conversation['id'], '不能发送')
    message_inbox.mark_read(first, conversation['id'], newest['messages'][0]['id'])
    assert message_inbox.list_conversations(first)[0]['unread_count'] == 0
    assert message_inbox.list_conversations(second)[0]['unread_count'] == 1
    message_inbox.record_received(first, _event('m3', '阅读后到达'))
    message_inbox.mark_read(first, conversation['id'], newest['messages'][0]['id'])
    assert message_inbox.list_conversations(first)[0]['unread_count'] == 1


def test_missing_profiles_are_saved_once_and_failures_preserve_conversations(inbox):
    account, transport = inbox
    account_id = account('客服')
    message_inbox.record_received(account_id, _event('profile-message'))
    calls = []
    def profile(credentials, peer):
        calls.append(peer)
        return {'peer_user_id': peer, 'nickname': '对方昵称', 'avatar': 'https://example.com/avatar.jpg'}
    transport.get_user_profile = profile
    message_inbox.refresh_conversation_profiles(account_id)
    message_inbox.refresh_conversation_profiles(account_id)
    saved = message_inbox.list_conversations(account_id)[0]
    assert (saved['nickname'], saved['avatar']) == ('对方昵称', 'https://example.com/avatar.jpg')
    assert calls == ['123456'] and saved['unread_count'] == 1
    # 其它会话查询失败后保留聊天，后续轮询不反复请求失效认证。
    message_inbox.create_conversation(account_id, peer_user_id='999')
    def fail(*args):
        calls.append('failed')
        raise ValueError('资料查询失败')
    transport.get_user_profile = fail
    message_inbox.refresh_conversation_profiles(account_id)
    message_inbox.refresh_conversation_profiles(account_id)
    assert calls == ['123456', 'failed']
    assert message_inbox.status(account_id)['profile_error'] == '资料查询失败'
    assert len(message_inbox.list_conversations(account_id)) == 2


def test_hidden_conversation_keeps_history_and_returns_only_for_new_message_or_explicit_open(inbox):
    account, _ = inbox
    account_id = account('客服')
    message_inbox.record_received(account_id, _event('before-hide'))
    conversation = message_inbox.list_conversations(account_id)[0]
    assert message_inbox.hide_conversation(account_id, conversation['id']) == {'ok': True}
    from app import views
    hidden_key = f'{message_inbox.HIDDEN_CONVERSATION_PREFIX}{account_id}:{conversation["id"]}'
    assert hidden_key not in views.get_settings()
    views.update_settings({hidden_key: '0'})
    # 每次读取都重新连接数据库，验证隐藏标记持久化且搜索也不能恢复。
    assert message_inbox.list_conversations(account_id) == []
    assert message_inbox.list_conversations(account_id, '123456') == []
    assert len(message_inbox.list_messages(account_id, conversation['id'])['messages']) == 1
    message_inbox.record_received(account_id, _event('before-hide'))
    assert message_inbox.list_conversations(account_id) == []
    message_inbox.record_received(account_id, _event('after-hide'))
    assert message_inbox.list_conversations(account_id)[0]['id'] == conversation['id']
    assert len(message_inbox.list_messages(account_id, conversation['id'])['messages']) == 2
    message_inbox.hide_conversation(account_id, conversation['id'])
    reopened = message_inbox.create_conversation(account_id, peer_user_id='123456')
    assert reopened['id'] == conversation['id']
    assert message_inbox.list_conversations(account_id)[0]['id'] == conversation['id']
    assert len(message_inbox.list_messages(account_id, conversation['id'])['messages']) == 2
    message_inbox.hide_conversation(account_id, conversation['id'])
    from app.services import maintenance
    with database.connect() as conn:
        maintenance._clear_project_database(conn)
        assert conn.execute('SELECT 1 FROM settings WHERE key=?', (hidden_key,)).fetchone() is None
        assert database.get_setting(conn, 'unreplied_reminder_days') == '3'


def test_hide_route_is_account_scoped_and_does_not_remove_messages(inbox):
    from fastapi.testclient import TestClient
    from app.main import app

    account, _ = inbox
    first, second = account('客服一'), account('客服二')
    message_inbox.record_received(first, _event('private-first'))
    message_inbox.record_received(second, _event('private-second'))
    conversation = message_inbox.list_conversations(first)[0]
    client = TestClient(app)
    path = f'/api/message-inbox/conversations/{conversation["id"]}'
    assert client.delete(path, params={'account_id': second}).status_code == 400
    assert message_inbox.list_conversations(first)
    assert client.delete(path, params={'account_id': first}).json() == {'ok': True}
    assert client.delete(path, params={'account_id': first}).json() == {'ok': True}
    assert message_inbox.list_conversations(first) == []
    assert len(message_inbox.list_conversations(second)) == 1
    assert len(message_inbox.list_messages(first, conversation['id'])['messages']) == 1


def test_new_local_customer_profile_updates_existing_conversation(inbox):
    account, _ = inbox
    account_id = account('客服')
    message_inbox.record_received(account_id, _event('before-local-profile'))
    with database.connect() as conn:
        user_id = conn.execute("INSERT INTO user_accounts(platform,platform_user_id,nickname,avatar) VALUES('dy','123456','已采集客户','https://example.com/local.jpg')").lastrowid
        conn.execute('INSERT INTO lead_user_accounts(account_id) VALUES(?)', (user_id,))
    message_inbox.record_received(account_id, _event('after-local-profile'))
    saved = message_inbox.list_conversations(account_id)[0]
    assert saved['nickname'] == '已采集客户' and saved['avatar'] == 'https://example.com/local.jpg'
    assert saved['lead_id'] is not None


def test_list_response_schedules_profile_lookup_without_returning_credentials(inbox):
    from fastapi.testclient import TestClient
    from app.main import app

    account, transport = inbox
    account_id = account('客服')
    message_inbox.record_received(account_id, _event('background-profile'))
    transport.get_user_profile = lambda *args: {'nickname': '真实联系人', 'avatar': 'https://example.com/person.jpg'}
    client = TestClient(app)
    first = client.get('/api/message-inbox/conversations', params={'account_id': account_id})
    assert first.status_code == 200 and first.json()[0]['nickname'] == ''
    second = client.get('/api/message-inbox/conversations', params={'account_id': account_id})
    assert second.json()[0]['nickname'] == '真实联系人'
    assert second.json()[0]['avatar'] == 'https://example.com/person.jpg'
    assert 'private-test-credential' not in second.text


def test_uncertain_send_is_persisted_without_automatic_retry_and_replies_share_quota(inbox):
    account, transport = inbox
    account_id = account('客服')
    conversation = message_inbox.create_conversation(account_id, peer_user_id='123456')
    calls = []

    def send(*args, **kwargs):
        calls.append(args)
        return {'status': 'uncertain', 'error': '连接超时'}

    transport.send_text = send
    result = message_inbox.send_message(account_id, conversation['id'], '请问需要了解什么？')
    assert result['status'] == 'uncertain'
    assert len(calls) == 1
    with database.connect() as conn:
        assert conn.execute('SELECT status FROM im_messages').fetchone()['status'] == 'uncertain'
        assert conn.execute('SELECT lead_account_id,status FROM message_send_attempts').fetchone()['lead_account_id'] is None
        database.set_setting(conn, 'message_daily_limit', '3')
        database.set_setting(conn, 'message_hourly_limit', '3')
    transport.send_text = lambda *args, **kwargs: {'status': 'accepted'}
    assert message_inbox.send_message(account_id, conversation['id'], '补充说明')['status'] == 'accepted'
    assert message_inbox.send_message(account_id, conversation['id'], '第三条')['status'] == 'accepted'
    with pytest.raises(RuntimeError, match='额度'):
        message_inbox.send_message(account_id, conversation['id'], '第四条')
    with database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM im_messages').fetchone()[0] == 3


def test_listener_reuses_connection_and_shutdown_preserves_messages(inbox):
    account, transport = inbox
    account_id = account('客服')
    started = threading.Event()
    calls = []
    released = []
    transport.close_send_auth = released.append

    def receive(credentials, on_message, on_state, stop_event):
        calls.append(credentials)
        on_state('connected')
        on_message(_event('received-live'))
        started.set()
        stop_event.wait(5)

    transport.receive_forever = receive
    message_inbox.connect_account(account_id)
    assert started.wait(2)
    message_inbox.connect_account(account_id)
    assert len(calls) == 1
    assert 'private-test-credential' not in str(message_inbox.status(account_id))
    message_inbox.disconnect_account(account_id)
    assert released == [account_id]
    assert message_inbox.status(account_id)['state'] == 'disconnected'
    assert account_id not in message_inbox.active_accounts()
    assert len(calls) == 1
    assert message_inbox.list_conversations(account_id)[0]['unread_count'] == 1


@pytest.mark.parametrize(('reported_state', 'safe_error', 'expected_error'), [
    ('expired', '私信登录已失效，请同步账号认证', '私信登录已失效，请同步账号认证'),
    ('error', '私信消息保存失败，监听已停止', '私信消息保存失败，监听已停止'),
    ('connecting', '', '私信监听已停止（RuntimeError），请检查连接后重新连接'),
])
def test_listener_preserves_safe_failure_and_manual_disconnect(inbox, reported_state, safe_error, expected_error):
    account, transport = inbox
    account_id = account('客服')
    calls = []

    def receive(credentials, on_message, on_state, stop_event):
        calls.append(credentials)
        on_state(reported_state, safe_error)
        raise RuntimeError('wss://private.invalid/?token=private-test-credential')

    transport.receive_forever = receive
    message_inbox.connect_account(account_id)
    listener = message_inbox._listeners[account_id]
    listener.thread.join(timeout=2)
    assert not listener.thread.is_alive()
    result = message_inbox.status(account_id)
    assert result['state'] == ('expired' if reported_state == 'expired' else 'error')
    assert result['error'] == expected_error
    assert 'private-test-credential' not in str(result)
    assert message_inbox.disconnect_account(account_id)['state'] == 'disconnected'
    assert account_id not in message_inbox.active_accounts()
    assert len(calls) == 1


def test_send_credentials_and_shutdown_use_same_account_lock(inbox, monkeypatch):
    from app.services import im_session

    account, transport = inbox
    account_id = account('客服')
    conversation = message_inbox.create_conversation(account_id, peer_user_id='123456')
    def credentials(selected_account):
        assert selected_account == account_id
        assert message_inbox._send_locks[account_id].locked()
        return {'cookie_str': 'private-test-credential'}
    monkeypatch.setattr(im_session, 'load_credentials', credentials)
    def send(material, peer, text, **kwargs):
        assert kwargs['account_id'] == account_id
        assert message_inbox._send_locks[account_id].locked()
        return {'status': 'accepted'}
    transport.send_text = send
    assert message_inbox.send_message(account_id, conversation['id'], '测试')['status'] == 'accepted'
    released = []
    def close(selected_account):
        assert message_inbox._send_locks[selected_account].locked()
        released.append(selected_account)
    transport.close_send_auth = close
    message_inbox.shutdown()
    assert released == [account_id]


def test_sync_auth_releases_sender_off_event_loop(inbox, monkeypatch):
    from app.routers import message_inbox as router

    account, transport = inbox
    account_id = account('客服', 'xhs')
    released = []
    event_loop_thread = threading.get_ident()
    async def sync(selected_account, capture):
        assert selected_account == account_id
        return {'send_ready': True}
    def close(selected_account):
        # 等待发送锁的操作不能阻塞异步接口所在的线程。
        assert threading.get_ident() != event_loop_thread
        assert message_inbox._send_locks[selected_account].locked()
        released.append(selected_account)
    monkeypatch.setattr(router, 'require_license', lambda: None)
    monkeypatch.setattr(message_inbox.im_session, 'capture_xhs_context', object(), raising=False)
    monkeypatch.setattr(message_inbox.im_session, 'sync_account', sync, raising=False)
    with monkeypatch.context() as scoped:
        scoped.setattr(transport, 'close_send_auth', close)
        assert asyncio.run(router.sync_auth(account_id)) == {'send_ready': True}
    assert released == [account_id]


def test_startup_switch_connects_all_ready_supported_accounts(inbox, monkeypatch):
    from app import views

    account, transport = inbox
    first, second = account('抖音客服'), account('小红书客服', 'xhs')
    disabled, expired = account('停用账号'), account('登录失效')
    account('快手账号', 'ks')
    account_center.update_account(disabled, {'enabled': False})
    account_center.set_login_status(expired, 'user', 'expired')
    account_center.create_account('dy', '只采集', 'operations', ['acquisition'])
    calls = []
    released = []
    monkeypatch.setattr(message_inbox.license_service, 'ensure_authorized', lambda: {})
    monkeypatch.setattr(message_inbox, '_transport_for_platform', lambda platform: transport)
    xhs_capture = object()
    monkeypatch.setattr(message_inbox.im_session, 'capture_xhs_context', xhs_capture, raising=False)

    async def sync(account_id, capture):
        assert capture is xhs_capture
        calls.append(account_id)
        return {'receive_ready': True}

    def restore(account_id):
        assert account_id == first
        calls.append(account_id)
        return {'receive_ready': True}

    transport.receive_forever = lambda credentials, on_message, on_state, stop: (on_state('connected'), stop.wait(5))
    transport.close_send_auth = released.append
    transport.restore_session = restore
    monkeypatch.setattr(message_inbox.im_session, 'sync_account', sync, raising=False)
    assert views.get_settings()['message_inbox_auto_connect'] is False
    asyncio.run(message_inbox.auto_connect_accounts())
    assert calls == [] and message_inbox.active_accounts() == []
    assert views.update_settings({'message_inbox_auto_connect': True})['message_inbox_auto_connect'] is True
    asyncio.run(message_inbox.auto_connect_accounts())
    assert set(calls) == {first, second}
    assert set(released) == {first, second}
    assert message_inbox.active_accounts() == sorted([first, second])
    # 再执行也不能覆盖已有连接或重新同步正在使用的账号。
    asyncio.run(message_inbox.auto_connect_accounts())
    assert len(calls) == 2


def test_startup_failure_is_visible_without_blocking_other_accounts(inbox, monkeypatch):
    account, transport = inbox
    failed, incomplete, valid = account('同步失败'), account('材料不足'), account('正常账号', 'xhs')
    with database.connect() as conn:
        database.set_setting(conn, 'message_inbox_auto_connect', 'true')
    monkeypatch.setattr(message_inbox.license_service, 'ensure_authorized', lambda: {})
    monkeypatch.setattr(message_inbox, '_transport_for_platform', lambda platform: transport)

    async def sync(account_id):
        if account_id == failed:
            raise RuntimeError('private-cookie-secret')
        return {'receive_ready': account_id != incomplete, 'error': '认证材料不完整'}

    transport.receive_forever = lambda credentials, on_message, on_state, stop: (on_state('connected'), stop.wait(5))
    monkeypatch.setattr(message_inbox, 'sync_account', sync)
    asyncio.run(message_inbox.auto_connect_accounts())
    assert message_inbox.status(failed)['state'] == 'error'
    assert 'private-cookie-secret' not in str(message_inbox.status(failed))
    assert message_inbox.status(incomplete)['error'] == '认证材料不完整'
    assert message_inbox.active_accounts() == [valid]


def test_sync_blocks_same_account_operations_and_cancellation_releases_state(inbox, monkeypatch):
    account, _ = inbox
    account_id = account('待同步账号', 'xhs')
    conversation = message_inbox.create_conversation(account_id, peer_user_id='65abcdef1234567890abcdef')
    monkeypatch.setattr(message_inbox.im_session, 'capture_xhs_context', object(), raising=False)

    async def run():
        entered = asyncio.Event()

        async def capture(selected_account, callback):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(message_inbox.im_session, 'sync_account', capture, raising=False)
        task = asyncio.create_task(message_inbox.sync_account(account_id))
        await asyncio.wait_for(entered.wait(), 1)
        assert message_inbox.status(account_id)['state'] == 'syncing'
        assert not message_inbox.status(account_id)['send_ready']
        assert account_id in message_inbox.active_accounts()
        from app.services import data_management
        with pytest.raises(ValueError, match='私信'):
            data_management.ensure_idle('备份')
        for operation in (lambda: message_inbox.connect_account(account_id), lambda: message_inbox.disconnect_account(account_id), lambda: message_inbox.send_message(account_id, conversation['id'], '不得发送')):
            with pytest.raises(ValueError, match='同步'):
                operation()
        with pytest.raises(ValueError, match='同步'):
            await message_inbox.sync_account(account_id)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert message_inbox.status(account_id)['state'] == 'disconnected'
        assert account_id not in message_inbox.active_accounts()
        assert message_inbox.list_messages(account_id, conversation['id'])['messages'] == []

    asyncio.run(run())


def test_startup_requires_license_before_sync(inbox, monkeypatch):
    account, _ = inbox
    account_id = account('未授权账号')
    with database.connect() as conn:
        database.set_setting(conn, 'message_inbox_auto_connect', 'true')
    monkeypatch.setattr(message_inbox.license_service, 'ensure_authorized', lambda: (_ for _ in ()).throw(ValueError('请先配置授权')))

    async def forbidden_sync(selected_account):
        pytest.fail('未授权时不得启动认证浏览器')

    monkeypatch.setattr(message_inbox, 'sync_account', forbidden_sync)
    asyncio.run(message_inbox.auto_connect_accounts())
    assert message_inbox.status(account_id)['error'] == '请先配置授权'
    assert not message_inbox.active_accounts()


def test_lifespan_does_not_wait_for_sync_and_cancels_it_before_shutdown(inbox, monkeypatch):
    from app import main

    events = []
    for module, name in [(main.database, 'init_db'), (main.license_service, 'check_license'), (main.job_queue, 'start'), (main.automation_workbench, 'start_scheduler')]:
        monkeypatch.setattr(module, name, lambda: None)
    for module, name in [(main.automation_workbench, 'stop_scheduler'), (main.job_queue, 'shutdown'), (main.inbox_service, 'shutdown'), (main.profile_manager, 'shutdown')]:
        monkeypatch.setattr(module, name, lambda label=name: events.append(label))

    async def run():
        entered = asyncio.Event()

        async def startup():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                events.append('sync:cancelled')

        monkeypatch.setattr(main.inbox_service, 'auto_connect_accounts', startup)
        async with main.lifespan(main.app):
            await asyncio.wait_for(entered.wait(), 1)
            assert not events

    asyncio.run(run())
    assert events == ['sync:cancelled', 'stop_scheduler', 'shutdown', 'shutdown', 'shutdown']


def test_disabled_binding_cannot_send_and_restart_keeps_uncertain_result(inbox):
    account, _ = inbox
    account_id = account('客服')
    conversation = message_inbox.create_conversation(account_id, peer_user_id='123456')
    with database.connect() as conn:
        conn.execute("INSERT INTO im_messages(account_id,conversation_id,direction,content,status) VALUES(?,?,'outgoing','中断发送','sending')", (account_id, conversation['id']))
    message_inbox.recover_pending_sends()
    assert message_inbox.list_messages(account_id, conversation['id'])['messages'][0]['status'] == 'uncertain'
    account_center.set_feature_status(account_id, 'message', 'expired')
    with pytest.raises(ValueError, match='未登录'):
        message_inbox.send_message(account_id, conversation['id'], '不发送')
    assert message_inbox.list_messages(account_id, conversation['id'])['messages']


@pytest.mark.parametrize('follow_status,expected,event_count', [('已私信', '已回复', 1), ('未回复', '已回复', 1), ('已成交', '已成交', 0), ('非客户', '非客户', 0)])
def test_incoming_reply_advances_only_waiting_status_without_changing_screening(inbox, follow_status, expected, event_count):
    account, _ = inbox
    account_id = account('客服')
    with database.connect() as conn:
        user_id = conn.execute("INSERT INTO user_accounts(platform,platform_user_id,nickname) VALUES('dy','123456','客户')").lastrowid
        lead_id = conn.execute("INSERT INTO lead_user_accounts(account_id,screening_status,follow_status,reason) VALUES(?,'目标客户',?,'保留分析原因')", (user_id, follow_status)).lastrowid
    message_inbox.record_received(account_id, _event('reply-1'))
    message_inbox.record_received(account_id, _event('reply-1'))
    message_inbox.record_received(account_id, _event('reply-2'))
    with database.connect() as conn:
        lead = conn.execute('SELECT * FROM lead_user_accounts WHERE id=?', (lead_id,)).fetchone()
        assert lead['follow_status'] == expected
        assert lead['screening_status'] == '目标客户' and lead['reason'] == '保留分析原因'
        assert conn.execute('SELECT COUNT(*) FROM lead_status_events WHERE lead_account_id=?', (lead_id,)).fetchone()[0] == event_count


def test_sec_uid_is_not_guessed_as_numeric_message_recipient(inbox):
    account, _ = inbox
    account_id = account('客服')
    with database.connect() as conn:
        user_id = conn.execute("INSERT INTO user_accounts(platform,platform_user_id,sec_uid) VALUES('dy','MS4wLjABAA-test','MS4wLjABAA-test')").lastrowid
        lead_id = conn.execute('INSERT INTO lead_user_accounts(account_id) VALUES(?)', (user_id,)).lastrowid
    with pytest.raises(ValueError, match='数字用户 ID'):
        message_inbox.create_conversation(account_id, lead_id=lead_id)
    with database.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM im_conversations').fetchone()[0] == 0


def test_manual_send_preserves_analysis_and_uncertain_batch_cannot_be_retried(inbox):
    import asyncio
    from app.services import message_workbench

    account, transport = inbox
    account_id = account('客服')
    with database.connect() as conn:
        user_id = conn.execute("INSERT INTO user_accounts(platform,platform_user_id) VALUES('dy','123456')").lastrowid
        lead_id = conn.execute("INSERT INTO lead_user_accounts(account_id,screening_status,follow_status,reason) VALUES(?,'目标客户','未私信','原分析')", (user_id,)).lastrowid
    conversation = message_inbox.create_conversation(account_id, lead_id=lead_id)
    message_inbox.send_message(account_id, conversation['id'], '人工首次触达')
    with database.connect() as conn:
        lead = conn.execute('SELECT * FROM lead_user_accounts WHERE id=?', (lead_id,)).fetchone()
        assert (lead['follow_status'], lead['screening_status'], lead['reason']) == ('已私信', '目标客户', '原分析')
        conn.execute("UPDATE lead_user_accounts SET follow_status='未私信' WHERE id=?", (lead_id,))
        conn.execute("DELETE FROM message_send_attempts WHERE lead_account_id=?", (lead_id,))
        conn.execute("INSERT INTO message_batches(id,account_id) VALUES('uncertain-batch',?)", (account_id,))
        item_id = conn.execute("INSERT INTO message_batch_items(batch_id,lead_account_id,script) VALUES('uncertain-batch',?,'批次消息')", (lead_id,)).lastrowid
        item = dict(conn.execute('SELECT * FROM message_batch_items WHERE id=?', (item_id,)).fetchone())
        batch = dict(conn.execute("SELECT * FROM message_batches WHERE id='uncertain-batch'").fetchone())
    transport.send_text = lambda *args, **kwargs: {'status': 'uncertain'}
    asyncio.run(message_workbench._run_batch_item(item, batch))
    with database.connect() as conn:
        assert conn.execute('SELECT status FROM message_batch_items WHERE id=?', (item_id,)).fetchone()[0] == 'uncertain'
        conn.execute("UPDATE message_batches SET status='succeeded' WHERE id='uncertain-batch'")
    with pytest.raises(ValueError, match='没有可重试'):
        message_workbench.retry_auto_message_batch('uncertain-batch')


def test_license_watchdog_stops_silent_receivers_on_both_platforms(inbox, monkeypatch):
    account, transport = inbox
    ids = [account('抖音客服'), account('小红书客服', 'xhs')]
    started = [threading.Event(), threading.Event()]
    revoked = threading.Event()
    receivers = []
    failures = []
    monkeypatch.setattr(message_inbox, 'LICENSE_CHECK_INTERVAL', 0.01)
    monkeypatch.setattr(message_inbox, '_transport_for_platform', lambda _: transport)

    def authorize():
        if revoked.is_set():
            failures.append(True)
            raise ValueError('授权租约已过期')
        return {'authorized': True}

    def receive(credentials, on_message, on_state, stop):
        index = len(receivers)
        receivers.append(stop)
        on_state('connected')
        started[index].set()
        stop.wait(2)
        # 平台迟到的状态和消息不能覆盖授权错误或继续入库。
        on_state('reconnecting')
        on_message(_event(f'late-{index}'))

    monkeypatch.setattr(message_inbox.license_service, 'ensure_authorized', authorize)
    transport.receive_forever = receive
    message_inbox.connect_account(ids[0])
    watchdog = message_inbox._license_watchdog
    message_inbox.connect_account(ids[1])
    assert message_inbox._license_watchdog is watchdog
    assert all(event.wait(1) for event in started)
    revoked.set()
    for account_id in ids:
        listener = message_inbox._listeners[account_id]
        listener.thread.join(timeout=2)
        assert not listener.thread.is_alive()
        result = message_inbox.status(account_id)
        assert result['state'] == 'error' and '授权租约已过期' in result['error']
        assert message_inbox.list_conversations(account_id) == []
    assert len(failures) == 1
    message_inbox.shutdown()
    assert not watchdog.is_alive()


def test_disconnect_one_account_keeps_authorization_watchdog_for_others(inbox, monkeypatch):
    account, transport = inbox
    first, second = account('客服一'), account('客服二')
    started = threading.Event()

    def receive(credentials, on_message, on_state, stop):
        on_state('connected')
        started.set()
        stop.wait(2)

    transport.receive_forever = receive
    message_inbox.connect_account(first)
    assert started.wait(1)
    message_inbox.connect_account(second)
    message_inbox.disconnect_account(first)
    assert message_inbox._license_watchdog.is_alive()
    assert not message_inbox._license_watchdog_stop.is_set()

    def reject():
        raise ValueError('授权已撤销')

    monkeypatch.setattr(message_inbox.license_service, 'ensure_authorized', reject)
    message_inbox._check_receiver_authorization()
    message_inbox._listeners[second].thread.join(timeout=2)
    assert message_inbox.status(first)['state'] == 'disconnected'
    assert '授权已撤销' in message_inbox.status(second)['error']
    assert second not in message_inbox.active_accounts()


def test_unlicensed_connection_does_not_start_receiver(inbox, monkeypatch):
    account, transport = inbox
    account_id = account('不可连接')
    calls = []
    transport.receive_forever = lambda *args: calls.append(args)

    def reject():
        raise ValueError('授权已失效')

    monkeypatch.setattr(message_inbox.license_service, 'ensure_authorized', reject)
    with pytest.raises(ValueError, match='授权已失效'):
        message_inbox.connect_account(account_id)
    assert calls == [] and account_id not in message_inbox._listeners


@pytest.fixture
def login_runtime(inbox, monkeypatch):
    """模拟外部登录和协议，实际运行本地暂停、认证切换与接收线程。"""
    from app.services import platform_login

    account, transport = inbox
    material, invalidated, synced, received, callbacks = {}, [], [], {}, {}
    monkeypatch.setattr(message_inbox, '_transport_for_platform', lambda platform: transport)
    monkeypatch.setattr(message_inbox.im_session, 'capture_xhs_context', object(), raising=False)

    def credentials(account_id):
        cookie = material.get(account_id, 'old')
        if not cookie:
            raise ValueError('请先同步该账号的私信认证')
        return {'account_id': account_id, 'cookie_str': cookie}

    def invalidate(account_id):
        invalidated.append(account_id)
        material[account_id] = ''

    async def sync(account_id, capture):
        return restore(account_id)

    def restore(account_id):
        synced.append(account_id)
        material[account_id] = 'new'
        return {'receive_ready': True, 'send_ready': True}

    def receive(credentials, on_message, on_state, stop):
        account_id = credentials['account_id']
        callbacks[account_id] = on_message
        on_state('connected')
        received.setdefault(account_id, threading.Event()).set()
        stop.wait(20)

    def connect(account_id):
        message_inbox.connect_account(account_id)
        assert received.setdefault(account_id, threading.Event()).wait(2)
        return message_inbox._listeners[account_id]

    async def login(*args, **kwargs):
        return {'success': True}

    monkeypatch.setattr(message_inbox.im_session, 'load_credentials', credentials)
    monkeypatch.setattr(message_inbox.im_session, 'invalidate_credentials', invalidate, raising=False)
    monkeypatch.setattr(message_inbox.im_session, 'sync_account', sync, raising=False)
    monkeypatch.setattr(message_inbox.im_session, 'session_status', lambda account_id: {
        'receive_ready': bool(material.get(account_id, 'old')), 'send_ready': bool(material.get(account_id, 'old')), 'error': '',
    })
    monkeypatch.setattr(platform_login, 'login_account', login)
    transport.receive_forever = receive
    transport.restore_session = restore
    with database.connect() as conn:
        database.set_setting(conn, 'message_hourly_limit', '100')
        database.set_setting(conn, 'message_daily_limit', '100')
    return SimpleNamespace(account=account, transport=transport, material=material,
                           invalidated=invalidated, synced=synced, connect=connect, callbacks=callbacks)


def _background(action):
    results, errors = [], []

    def run():
        try:
            results.append(action())
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, results, errors


@pytest.mark.parametrize('inflight_status', ['accepted', 'uncertain'])
def test_login_drains_sender_and_resumes_only_its_account(login_runtime, monkeypatch, inflight_status):
    from app.services import content_publish, platform_login

    runtime = login_runtime
    first, second = runtime.account('登录账号'), runtime.account('继续接收账号')
    original_first, original_second = runtime.connect(first), runtime.connect(second)
    conversation = message_inbox.create_conversation(first, peer_user_id='123456')
    other_conversation = message_inbox.create_conversation(second, peer_user_id='123456')
    sending, finish_send, paused, logging_in, finish_login = [threading.Event() for _ in range(5)]
    sent = []
    original_release = message_inbox.release_send_session

    def release(account_id):
        paused.set()
        original_release(account_id)

    def send(credentials, peer, text, **kwargs):
        sent.append((kwargs['account_id'], text, credentials['cookie_str']))
        if text == '在途消息':
            sending.set()
            assert finish_send.wait(3)
            return {'status': inflight_status}
        return {'status': 'accepted'}

    async def login(*args, **kwargs):
        logging_in.set()
        assert await asyncio.to_thread(finish_login.wait, 3)
        return {'success': True}

    monkeypatch.setattr(message_inbox, 'release_send_session', release)
    monkeypatch.setattr(platform_login, 'login_account', login)
    runtime.transport.send_text = send
    flight = _background(lambda: message_inbox.send_message(first, conversation['id'], '在途消息'))
    workers = [flight]
    try:
        assert sending.wait(2)
        login_job = _background(lambda: content_publish.run_account_login(first, 'user'))
        workers.append(login_job)
        assert paused.wait(2)
        assert message_inbox.status(first)['state'] == 'logging_in'
        assert first in message_inbox.busy_accounts()
        pending = _background(lambda: message_inbox.send_message(first, conversation['id'], '排队消息'))
        workers.append(pending)
        assert not logging_in.wait(0.05)
        assert not pending[1] and len(sent) == 1
        assert message_inbox.send_message(second, other_conversation['id'], '其他账号')['status'] == 'accepted'
        runtime.callbacks[second](_event('other-during-login'))
        assert message_inbox.status(second)['state'] == 'connected'
        assert message_inbox._listeners[second] is original_second
        finish_send.set()
        assert logging_in.wait(2)
        assert not original_first.thread.is_alive()
        assert runtime.invalidated == []
        for operation in (lambda: message_inbox.connect_account(first), lambda: message_inbox.disconnect_account(first)):
            with pytest.raises(ValueError, match='登录'):
                operation()
        with pytest.raises(ValueError, match='同步|私信'):
            asyncio.run(message_inbox.sync_account(first))
    finally:
        finish_send.set()
        finish_login.set()
        for thread, _, _ in workers:
            thread.join(3)
    assert all(not thread.is_alive() and not errors for thread, _, errors in workers), [errors for _, _, errors in workers]
    assert flight[1][0]['status'] == inflight_status
    assert pending[1][0]['status'] == 'accepted'
    assert [item for item in sent if item[0] == first] == [(first, '在途消息', 'old'), (first, '排队消息', 'new')]
    assert runtime.synced == [first]
    assert message_inbox._listeners[first] is not original_first
    assert message_inbox._listeners[second] is original_second and original_second.thread.is_alive()
    assert message_inbox.status(first)['state'] == 'connected'
    assert message_inbox._login_sessions == {}


@pytest.mark.parametrize('platform', ['dy', 'xhs'])
def test_login_syncs_without_enabling_previously_disconnected_receiver(login_runtime, platform):
    from app.services import content_publish

    runtime = login_runtime
    account_id = runtime.account('未连接账号', platform)
    assert content_publish.run_account_login(account_id, 'user')['success']
    assert runtime.synced == [account_id]
    assert runtime.invalidated == ([account_id] if platform == 'xhs' else [])
    state = message_inbox.status(account_id)
    assert state['state'] == 'disconnected' and state['send_ready']
    assert message_inbox._listeners[account_id].thread is None


@pytest.mark.parametrize('failure', ['login', 'cancel', 'sync', 'incomplete'])
def test_login_failure_releases_pause_without_reusing_old_credentials(login_runtime, monkeypatch, failure):
    from app.services import content_publish, platform_login

    runtime = login_runtime
    account_id = runtime.account('异常登录', 'xhs')
    receiver = runtime.connect(account_id)
    cancelled = []

    async def login(*args, **kwargs):
        if failure == 'cancel':
            cancelled.append(True)
        return {'success': failure not in {'login', 'cancel'}, 'message': '测试登录失败'}

    async def sync(*args):
        if failure == 'sync':
            raise RuntimeError('private-cookie-secret')
        return {'receive_ready': True, 'send_ready': False, 'error': '缺少发送认证'}

    monkeypatch.setattr(platform_login, 'login_account', login)
    monkeypatch.setattr(message_inbox.im_session, 'sync_account', sync)
    with pytest.raises((ValueError, RuntimeError)):
        content_publish.run_account_login(account_id, 'user', cancel_check=lambda: bool(cancelled))
    assert not receiver.thread.is_alive() and message_inbox._login_sessions == {}
    assert account_id not in message_inbox.busy_accounts()
    state = message_inbox.status(account_id)
    assert state['state'] == 'error' and not state['send_ready']
    assert 'private-cookie-secret' not in state['error']
    login_status = account_center.get_account(account_id)['login_status']['user']['status']
    assert login_status == ('ready' if failure in {'sync', 'incomplete'} else 'expired' if failure == 'cancel' else 'error')
    conversation = message_inbox.create_conversation(account_id, peer_user_id='65abcdef1234567890abcdef')
    with pytest.raises(ValueError):
        message_inbox.send_message(account_id, conversation['id'], '不得沿用旧认证')
    assert message_inbox.list_messages(account_id, conversation['id'])['messages'] == []


def test_account_rename_and_queued_login_keep_current_connection(login_runtime, monkeypatch):
    from fastapi import HTTPException
    from app.routers import accounts
    from app.schemas import PlatformAccountUpdate
    from app.services import job_handlers, job_queue

    runtime = login_runtime
    account_id = runtime.account('原名称')
    receiver = runtime.connect(account_id)
    monkeypatch.setattr(accounts, 'require_license', lambda: None)
    job_queue.configure_handlers(job_handlers)
    updated = accounts.update_account(account_id, PlatformAccountUpdate(name='新名称', enabled=True, features=['message']))
    assert updated['name'] == '新名称'
    with pytest.raises(HTTPException) as error:
        accounts.update_account(account_id, PlatformAccountUpdate(enabled=False))
    assert error.value.status_code == 409
    job = accounts.login_account(account_id, 'user')
    assert job['status'] == 'queued'
    assert account_center.get_account(account_id)['login_status']['user']['status'] == 'ready'
    job_queue.request_cancel(job['id'])
    assert account_center.get_account(account_id)['login_status']['user']['status'] == 'ready'
    assert message_inbox._listeners[account_id] is receiver and receiver.thread.is_alive()
    assert runtime.invalidated == runtime.synced == []


@pytest.mark.parametrize('mode', ['single', 'batch'])
@pytest.mark.parametrize('cancelled', [False, True])
def test_automatic_messages_wait_for_new_login_credentials(login_runtime, monkeypatch, mode, cancelled):
    from app.services import content_publish, message_workbench, platform_login

    runtime = login_runtime
    account_id = runtime.account('自动私信账号')
    with database.connect() as conn:
        user = conn.execute("INSERT INTO user_accounts(platform,platform_user_id,nickname) VALUES('dy','123456','待发送客户')").lastrowid
        lead = conn.execute("INSERT INTO lead_user_accounts(account_id,screening_status,follow_status,script) VALUES(?,'目标客户','未私信','自动话术')", (user,)).lastrowid
        conn.execute("INSERT INTO lead_sources(lead_account_id,source_type,keyword) VALUES(?,'manual','自动测试')", (lead,))
    batch = message_workbench.create_auto_message_batch('dy', '自动测试', 1, 0, 0, account_id=account_id) if mode == 'batch' else None
    logging_in, finish_login = threading.Event(), threading.Event()
    cancel_send = threading.Event()
    sent = []

    async def login(*args, **kwargs):
        logging_in.set()
        assert await asyncio.to_thread(finish_login.wait, 3)
        return {'success': True}

    def send(credentials, *args, **kwargs):
        sent.append(credentials['cookie_str'])
        return {'status': 'accepted'}

    monkeypatch.setattr(platform_login, 'login_account', login)
    runtime.transport.send_text = send
    login_job = _background(lambda: content_publish.run_account_login(account_id, 'user'))
    workers = [login_job]
    try:
        assert logging_in.wait(2)
        operation = message_workbench.run_auto_message_batch(batch['id']) if batch else message_workbench.auto_message_customer(lead, account_id=account_id, cancel_check=cancel_send.is_set)
        sender = _background(lambda: asyncio.run(operation))
        workers.append(sender)
        assert account_center.get_account(account_id)['login_status']['user']['status'] == 'checking'
        sender[0].join(0.05)
        assert sender[0].is_alive() and sent == []
        if cancelled:
            if batch:
                message_workbench.cancel_auto_message_batch(batch['id'])
            else:
                cancel_send.set()
            sender[0].join(2)
            assert not sender[0].is_alive() and sent == []
    finally:
        finish_login.set()
        for thread, _, _ in workers:
            thread.join(3)
    assert all(not thread.is_alive() for thread, _, _ in workers)
    assert not login_job[2]
    if cancelled and not batch:
        assert len(sender[2]) == 1 and isinstance(sender[2][0], ValueError)
    else:
        assert not sender[2], sender[2]
    assert sent == ([] if cancelled else ['new'])
    if batch:
        assert message_workbench.get_auto_message_batch(batch['id'])['status'] == ('cancelled' if cancelled else 'succeeded')
    with database.connect() as conn:
        assert conn.execute('SELECT follow_status FROM lead_user_accounts WHERE id=?', (lead,)).fetchone()[0] == ('未私信' if cancelled else '已私信')


def test_creator_login_does_not_pause_independent_user_session(login_runtime, monkeypatch):
    from unittest.mock import AsyncMock
    from app.publish_engine import service as engine
    from app.services import content_publish

    runtime = login_runtime
    account_id = account_center.create_account('dy', '发布与私信', 'brand', ['message', 'publish'])['id']
    account_center.set_login_status(account_id, 'user', 'ready')
    receiver = runtime.connect(account_id)
    monkeypatch.setattr(engine, 'login_account', AsyncMock(return_value={'success': True}))
    assert content_publish.run_account_login(account_id, 'creator')['success']
    assert message_inbox._listeners[account_id] is receiver and receiver.thread.is_alive()
    assert runtime.invalidated == runtime.synced == []


def test_disabled_account_login_does_not_enable_private_messages(login_runtime):
    from app.services import content_publish

    runtime = login_runtime
    account_id = runtime.account('停用待登录')
    account_center.update_account(account_id, {'enabled': False})
    assert content_publish.run_account_login(account_id, 'user')['success']
    assert runtime.invalidated == runtime.synced == []
    assert message_inbox.status(account_id)['state'] == 'disconnected'
    assert not message_inbox.status(account_id)['send_ready']


def _history(message_id, content='旧消息', *, sent_at='2026-10-22T10:00:00+00:00', **values):
    """历史事件与推送共用字段，额外保留平台时间和客户端去重标识。"""
    return {**_event(message_id, content), 'history': True, 'sent_at': sent_at, **values}


def test_imported_history_sorts_pages_and_never_restores_hidden_or_unread(inbox):
    account, _ = inbox
    account_id = account('历史翻页')
    message_inbox.record_received(account_id, {**_event('live', '最新消息'), 'sent_at': '2026-10-22T20:00:00+08:00'})
    conversation = message_inbox.list_conversations(account_id)[0]
    live = message_inbox.list_messages(account_id, conversation['id'])['messages'][0]
    message_inbox.hide_conversation(account_id, conversation['id'])
    # 比实时消息晚入库的旧历史仍按真实时间排列，时区不同也能比较。
    for event in [_history('oldest', '最早', sent_at='2026-10-22T09:00:00Z', direction='outgoing'),
                  _history('old', '较早'), _history('middle', '中间', sent_at='2026-10-22T11:00:00Z')]:
        assert message_inbox.record_history(account_id, event)
    assert message_inbox.list_conversations(account_id) == []
    newest = message_inbox.list_messages(account_id, conversation['id'], limit=2)
    assert [item['content'] for item in newest['messages']] == ['中间', '最新消息']
    older = message_inbox.list_messages(account_id, conversation['id'], before_id=newest['messages'][0]['id'], limit=2)
    assert [item['content'] for item in older['messages']] == ['最早', '较早']
    assert newest['has_more'] and not older['has_more'] and newest['history_scope'] == 'platform'
    message_inbox.mark_read(account_id, conversation['id'], live['id'])
    with database.connect() as conn:
        saved = conn.execute('SELECT * FROM im_conversations WHERE id=?', (conversation['id'],)).fetchone()
        assert saved['last_message'] == '最新消息' and saved['last_message_id'] == live['id']
        assert saved['unread_count'] == 0
        outgoing = conn.execute("SELECT * FROM im_messages WHERE platform_message_id='oldest'").fetchone()
        assert outgoing['direction'] == 'outgoing' and outgoing['status'] == 'accepted'
        assert json.loads(outgoing['raw_content'])[message_inbox.HISTORY_MARKER] is True


def test_historical_reply_does_not_change_customer_follow_status(inbox):
    account, _ = inbox
    account_id = account('历史客户状态')
    with database.connect() as conn:
        user = conn.execute("INSERT INTO user_accounts(platform,platform_user_id) VALUES('dy','123456')").lastrowid
        lead = conn.execute("INSERT INTO lead_user_accounts(account_id,follow_status) VALUES(?,'已私信')", (user,)).lastrowid
    message_inbox.record_history(account_id, _history('old-reply'))
    with database.connect() as conn:
        assert conn.execute('SELECT follow_status FROM lead_user_accounts WHERE id=?', (lead,)).fetchone()[0] == '已私信'
        assert conn.execute('SELECT COUNT(*) FROM lead_status_events').fetchone()[0] == 0
    message_inbox.record_received(account_id, _event('new-reply'))
    with database.connect() as conn:
        assert conn.execute('SELECT follow_status FROM lead_user_accounts WHERE id=?', (lead,)).fetchone()[0] == '已回复'


def test_history_reconciles_outgoing_client_id_per_account_without_resending(inbox):
    account, transport = inbox
    first, second = account('历史客服一'), account('历史客服二')
    sent = []

    def send(*args, **kwargs):
        sent.append(kwargs['account_id'])
        return {'status': 'uncertain', 'client_message_id': 'same-client-id'}

    transport.send_text = send
    conversations = [message_inbox.create_conversation(item, peer_user_id='123456') for item in (first, second)]
    originals = [message_inbox.send_message(item, conversation['id'], '已提交的内容') for item, conversation in zip((first, second), conversations)]
    for account_id, conversation, original in zip((first, second), conversations, originals):
        event = _history('same-server-id', '已提交的内容', direction='outgoing', client_message_id='same-client-id')
        assert not message_inbox.record_history(account_id, event)
        assert not message_inbox.record_history(account_id, event)
        messages = message_inbox.list_messages(account_id, conversation['id'])['messages']
        assert len(messages) == 1 and messages[0]['id'] == original['id']
        assert messages[0]['status'] == 'accepted' and messages[0]['platform_message_id'] == 'same-server-id'
    assert sent == [first, second]


def test_history_sync_checks_ownership_license_platform_and_account_lock(inbox, monkeypatch):
    account, transport = inbox
    first, second, xhs = account('历史同步一'), account('历史同步二'), account('小红书历史', 'xhs')
    conversation = message_inbox.create_conversation(first, peer_user_id='123456')
    calls = []

    def sync(account_id, peer):
        assert message_inbox._send_locks[account_id].locked()
        calls.append((account_id, peer))
        return [_history('remote-history')]

    transport.sync_history = sync
    assert message_inbox.sync_history(first, conversation['id']) == {'imported': 1, 'total': 1}
    assert message_inbox.sync_history(first) == {'imported': 0, 'total': 1}
    assert calls == [(first, '123456'), (first, '')]
    with pytest.raises(ValueError, match='会话不存在'):
        message_inbox.sync_history(second, conversation['id'])
    with pytest.raises(ValueError, match='仅抖音'):
        message_inbox.sync_history(xhs)

    def reject():
        raise ValueError('授权无效')

    monkeypatch.setattr(message_inbox.license_service, 'ensure_authorized', reject)
    with pytest.raises(ValueError, match='授权无效'):
        message_inbox.sync_history(first)
    assert len(calls) == 2 and not message_inbox._send_locks[first].locked()


def test_history_cursor_is_account_scoped_and_same_time_uses_id(inbox):
    account, _ = inbox
    first, second = account('相同时间一'), account('相同时间二')
    for message_id in ['a', 'b', 'c']:
        message_inbox.record_history(first, _history(message_id))
    message_inbox.record_history(second, _history('a'))
    conversation = message_inbox.list_conversations(first)[0]
    other = message_inbox.list_conversations(second)[0]
    latest = message_inbox.list_messages(first, conversation['id'], limit=1)['messages'][0]
    assert latest['platform_message_id'] == 'c'
    assert message_inbox.list_messages(first, conversation['id'], before_id=latest['id'], limit=1)['messages'][0]['platform_message_id'] == 'b'
    with pytest.raises(ValueError, match='分页游标'):
        message_inbox.list_messages(second, other['id'], before_id=latest['id'])


@pytest.mark.parametrize('values', [{'sent_at': ''}, {'sent_at': 'invalid'}, {'time_source': 'received'}])
def test_history_requires_real_platform_time_before_saving(inbox, values):
    account, _ = inbox
    account_id = account('平台历史时间')
    with pytest.raises(ValueError, match='平台发送时间'):
        message_inbox.record_history(account_id, _history('no-time', **values))
    assert message_inbox.list_conversations(account_id) == []


def test_receiver_routes_sdk_catchup_without_marking_it_unread(inbox):
    account, transport = inbox
    account_id = account('重连补拉')
    received = threading.Event()

    def receive(credentials, on_message, on_state, stop):
        on_state('connected')
        on_message(_history('catchup'))
        on_message({**_event('live-after-catchup'), 'sent_at': '2026-10-22T12:00:00Z'})
        received.set()
        stop.wait(5)

    transport.receive_forever = receive
    message_inbox.connect_account(account_id)
    assert received.wait(2)
    conversation = message_inbox.list_conversations(account_id)[0]
    assert conversation['unread_count'] == 1
    assert len(message_inbox.list_messages(account_id, conversation['id'])['messages']) == 2


def test_dy_restores_sdk_off_loop_without_browser_auth(inbox, monkeypatch):
    account, transport = inbox
    account_id = account('恢复独立 SDK')
    loop_thread = threading.get_ident()

    def restore(selected):
        assert selected == account_id and threading.get_ident() != loop_thread
        return {'receive_ready': True, 'send_ready': True, 'sdk_uid': '987654'}

    async def no_browser(*args):
        raise AssertionError('SDK 恢复不能打开旧浏览器认证')

    transport.restore_session = restore
    monkeypatch.setattr(message_inbox.im_session, 'sync_account', no_browser, raising=False)
    assert asyncio.run(message_inbox.sync_account(account_id))['sdk_uid'] == '987654'


def test_browser_login_without_sdk_session_stays_successful(login_runtime, monkeypatch):
    from app.services import content_publish

    runtime = login_runtime
    account_id = runtime.account('仅网页登录')
    runtime.material[account_id] = ''
    assert content_publish.run_account_login(account_id, 'user')['success']
    assert runtime.invalidated == runtime.synced == []
    assert message_inbox.status(account_id)['state'] == 'disconnected'
