"""两个平台共享认证组件的账号隔离与小红书同步检查。"""
import asyncio
from types import SimpleNamespace

import pytest

from app import database, protected_settings
from app.services import account_center, im_session


def test_xhs_sync_reuses_account_profile_and_protects_http_only_cookie(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_CUSTOMER_DB', str(tmp_path / 'im.sqlite3'))
    monkeypatch.setenv('AI_CUSTOMER_DATA_DIR', str(tmp_path))
    database.init_db()
    account = account_center.create_account('xhs', '小红书测试', 'service', ['message'])
    account_center.set_login_status(account['id'], 'user', 'ready')
    visited, closed = [], []
    class Context:
        pages = []
        async def new_page(self):
            async def goto(url, **kwargs):
                visited.append(url)
            return SimpleNamespace(goto=goto)
        async def cookies(self, url):
            assert url == 'https://www.xiaohongshu.com/'
            return [{'name': 'a1', 'value': 'test-a1'}, {'name': 'web_session', 'value': 'private-session', 'httpOnly': True}]
        async def close(self):
            closed.append(True)
    from app.publish_engine import browser
    async def launch(**kwargs):
        assert kwargs['headless'] is True
        assert kwargs['account_file'] == account_center.profile_path(account['id'], 'message')
        return Context()
    monkeypatch.setattr(browser, 'launch_publish_context', launch)
    state = asyncio.run(im_session.sync_account(account['id'], im_session.capture_xhs_context))
    assert visited == ['https://www.xiaohongshu.com/'] and closed == [True]
    assert state['receive_ready'] and state['send_ready'] and 'cookie_str' not in state
    path = im_session._session_path(account['id'])
    stored = path.read_text(encoding='utf-8')
    assert stored.startswith(protected_settings.PREFIX) and 'private-session' not in stored
    assert 'web_session=private-session' in im_session.load_credentials(account['id'])['cookie_str']
    other = account_center.create_account('xhs', '另一账号', 'service', ['message'])
    # 即便错放加密文件，也不能把另一个账号的凭据用于收发。
    im_session._session_path(other['id']).write_text(stored, encoding='utf-8')
    with pytest.raises(ValueError, match='不匹配'):
        im_session.load_credentials(other['id'])


def test_invalidating_one_account_preserves_other_credentials_profile_and_messages(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_CUSTOMER_DB', str(tmp_path / 'invalidate.sqlite3'))
    monkeypatch.setenv('AI_CUSTOMER_DATA_DIR', str(tmp_path))
    database.init_db()
    first, second = [account_center.create_account('xhs', name, 'service', ['message'])['id'] for name in ('重新登录', '保持在线')]
    for account_id in (first, second):
        im_session.save_credentials(account_id, {'cookie_str': 'a1=test; web_session=' + account_id})
    profile_marker = account_center.profile_path(first, 'message', require_ready=False) / 'profile-marker.txt'
    profile_marker.write_text('keep', encoding='utf-8')
    with database.connect() as conn:
        conversation = conn.execute("INSERT INTO im_conversations(account_id,peer_user_id) VALUES(?,'65abcdef1234567890abcdef')", (first,)).lastrowid
        conn.execute("INSERT INTO im_messages(account_id,conversation_id,direction,content,status) VALUES(?,?,'incoming','保留历史','received')", (first, conversation))
    im_session.invalidate_credentials(first)
    assert not im_session._session_path(first).exists()
    assert im_session.load_credentials(second)['cookie_str'].endswith(second)
    assert profile_marker.read_text(encoding='utf-8') == 'keep'
    with database.connect() as conn:
        assert conn.execute('SELECT content FROM im_messages WHERE account_id=?', (first,)).fetchone()[0] == '保留历史'


def test_cancelled_sync_closes_browser_and_releases_queue(tmp_path, monkeypatch):
    from app.publish_engine import browser
    from app.services import browser_queue

    monkeypatch.setenv('AI_CUSTOMER_DB', str(tmp_path / 'cancel.sqlite3'))
    monkeypatch.setenv('AI_CUSTOMER_DATA_DIR', str(tmp_path))
    database.init_db()
    account = account_center.create_account('xhs', '取消同步', 'service', ['message'])
    account_center.set_login_status(account['id'], 'user', 'ready')
    closed = []

    async def run():
        entered = asyncio.Event()

        async def goto(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        async def close():
            closed.append(True)

        async def launch(**kwargs):
            return SimpleNamespace(pages=[SimpleNamespace(goto=goto)], close=close)

        monkeypatch.setattr(browser, 'launch_publish_context', launch)
        task = asyncio.create_task(im_session.sync_account(account['id'], im_session.capture_xhs_context))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert closed == [True] and not browser_queue._BROWSER_LOCK.locked()


def test_signed_xhs_runtime_uses_bundled_node_without_extra_dependencies(monkeypatch):
    from app.vendor.spider_xhs.im import XHSLiveAPI
    from app.vendor.spider_xhs.xhs_utils.xhs_pc import XHSPcAuth
    from app.vendor.spider_xhs.xhs_utils.xhs_pc.params import generate_request_params
    monkeypatch.setattr(XHSLiveAPI, '_request', lambda *args, **kwargs: {'success': True, 'code': 0, 'data': {'user_id': '64abcdef1234567890abcdef'}})
    auth = XHSPcAuth.from_cookie('a1=' + 'a' * 52 + '; web_session=test-session', dsl='1791432000000')
    try:
        path = '/api/im/web/chat/get_unread'
        context = auth.next_sign_context(path)
        headers, cookies, body = generate_request_params(auth.cookies, path, '', 'GET',
            user_id=auth.user_id, b1=auth.current_b1(context['now']), dsl_pair=auth.dsl_pair,
            doc_cookie=auth.sign_cookie, tier=context['tier'], sign_context=context)
        assert headers['x-s'] and headers['x-s-common'] and cookies['web_session'] == 'test-session'
    finally:
        auth.close()
