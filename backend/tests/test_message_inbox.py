from __future__ import annotations

import asyncio
import sys
import threading
from types import ModuleType

import pytest

from app import database
from app.services import account_center, message_inbox


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_CUSTOMER_DB', str(tmp_path / 'inbox.sqlite3'))
    monkeypatch.setenv('AI_CUSTOMER_DATA_DIR', str(tmp_path))
    database.init_db()
    from app import services

    session = ModuleType('app.services.douyin_im_session')
    session.session_status = lambda account_id: {'receive_ready': True, 'send_ready': True, 'error': '', 'synced_at': ''}
    session.load_credentials = lambda account_id: {'cookie_str': 'private-test-credential'}
    transport = ModuleType('app.services.douyin_im_transport')
    transport.send_text = lambda *args, **kwargs: {'status': 'accepted'}
    transport.close_send_auth = lambda account_id: None
    transport.TransportSendUncertain = type('TransportSendUncertain', (RuntimeError,), {})
    for name, module in [('douyin_im_session', session), ('douyin_im_transport', transport)]:
        monkeypatch.setitem(sys.modules, f'app.services.{name}', module)
        monkeypatch.setattr(services, name, module, raising=False)

    def account(name):
        result = account_center.create_account('dy', name, 'service', ['message'])
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


def _event(message_id, content='你好'):
    return {'platform_message_id': message_id, 'peer_user_id': '123456', 'conversation_id': '0:1:123456:987654', 'conversation_short_id': 22, 'content': content, 'raw_content': {'text': content}, 'direction': 'incoming'}


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
    from app.services import douyin_im_session

    account, transport = inbox
    account_id = account('客服')
    conversation = message_inbox.create_conversation(account_id, peer_user_id='123456')
    def credentials(selected_account):
        assert selected_account == account_id
        assert message_inbox._send_locks[account_id].locked()
        return {'cookie_str': 'private-test-credential'}
    monkeypatch.setattr(douyin_im_session, 'load_credentials', credentials)
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
    account_id = account('客服')
    released = []
    event_loop_thread = threading.get_ident()
    async def sync(selected_account):
        assert selected_account == account_id
        return {'send_ready': True}
    def close(selected_account):
        # 等待发送锁的操作不能阻塞异步接口所在的线程。
        assert threading.get_ident() != event_loop_thread
        assert message_inbox._send_locks[selected_account].locked()
        released.append(selected_account)
    monkeypatch.setattr(router, 'require_license', lambda: None)
    monkeypatch.setattr(router.douyin_im_session, 'sync_account', sync, raising=False)
    with monkeypatch.context() as scoped:
        scoped.setattr(transport, 'close_send_auth', close)
        assert asyncio.run(router.sync_auth(account_id)) == {'send_ready': True}
    assert released == [account_id]


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
