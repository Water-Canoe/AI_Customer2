from __future__ import annotations

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
    assert message_inbox.status(account_id)['state'] == 'disconnected'
    assert message_inbox.list_conversations(account_id)[0]['unread_count'] == 1


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
