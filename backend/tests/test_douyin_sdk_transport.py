"""启动真实 Node 子进程，以离线 SDK 验证跨语言协议与账号隔离。"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app.services import douyin_sdk_transport as transport
from app.services.im_transport import TransportSendUncertain


# 假 SDK 保留真实进程、标准输入输出、异步扫码与分页；不访问抖音网络。
FAKE_SDK = r"""
import { EventEmitter } from 'node:events';
import { existsSync, mkdirSync, readFileSync, writeFileSync, appendFileSync } from 'node:fs';
import { join } from 'node:path';
export function configureLogger() {}
const info = peer => ({ conversationId: `0:1:11:${peer}`, conversationShortId: `${peer}00`, conversationType: 1, inboxType: 0, isGroup: false });
function trace(root, value) { appendFileSync(join(root, 'trace.jsonl'), JSON.stringify(value)+'\n'); }
function contact(account, peer, thread = info(peer).conversationId, short = info(peer).conversationShortId) {
  return { uid: peer, threadId: thread, conversationShortId: short, nickname: `客户${peer}`, avatar: 'https://example.com/avatar.png', secUid: `sec-${peer}`,
    async sendMsg(text) {
      trace(account.root, { op: 'send', peer, text });
      if (text === 'uncertain') throw Object.assign(new Error('sessionid=DO-NOT-LEAK'), { name: 'ImProtoTransportError', cmd: 100, stage: 'decode' });
      if (text === 'reject') throw Object.assign(new Error('cookie=DO-NOT-LEAK'), { name: 'SendMessageError', response: { clientMessageId: 'rejected-client' } });
      if (text === 'reconnect-fixture') account.fixtureReconnect();
      if (text === 'receive-restored-fixture') account.fixtureIncoming('restored-live');
      return { statusCode: 0, serverMessageId: 'send-1', clientMessageId: 'client-send-1' };
    },
    async getHistory(options) {
      trace(account.root, { op: 'history', peer, cursor: options.cursor || '' });
      const alternate = existsSync(join(account.root, 'body-variants'))
        ? ['纯文本正文', '{"content":"content字段正文"}', '{"display_name":"display字段正文"}', '[]', '{broken']
            .map((content, index) => ({ msgId: `${peer}-variant-${index}`, threadId: thread, conversationShortId: short,
              senderUid: peer, content, msgType: 7, createTime: 1720000000 })) : null;
      if (alternate) return { statusCode: 0, hasMore: false, cursor: '0', messages: alternate };
      return { statusCode: 0, hasMore: !options.cursor, cursor: options.cursor ? '0' : '1', messages: [{
        msgId: `${peer}-${options.cursor ? 'old' : 'new'}`, threadId: thread, conversationShortId: short,
        senderUid: options.cursor ? '11' : peer, content: JSON.stringify({ text: options.cursor ? '旧消息' : '新消息' }), msgType: 7,
        createTime: options.cursor ? 1720000000 : 1720000001000, clientMessageId: `client-${peer}-${options.cursor || 'first'}`,
      }] };
    },
    async refresh() { return this; },
    async getProfile() { return { uid: peer, nickname: `客户${peer}`, avatarThumb: 'https://example.com/avatar.png' }; },
  };
}
export const Stranger = { bind: (peer, thread, short, account) => contact(account, peer, thread, short) };
export function createClient({ dataDir }) {
  const client = new EventEmitter();
  client.store = { promote(pendingId, uid, session = {}, meta = {}) {
    trace(dataDir, { op: 'promote', uid });
    const folder = join(dataDir, 'accounts', uid); mkdirSync(folder, { recursive: true });
    const stored = { platformUid: uid, meta: { updatedAt: '2026-10-23T00:00:00Z', ...meta } };
    writeFileSync(join(folder, 'account.json'), JSON.stringify(stored));
    return stored;
  } };
  client.createAccount = () => {
    const account = { root: dataDir, uid: '11', imUid: '11', nickname: '测试账号', online: false, contacts: new Map(),
      async login() {
        return new Promise((resolve, reject) => {
          account.resolveLogin = resolve; account.rejectLogin = reject;
          void account.beginLogin(1).catch(error => {
            client.emit('system.login.error', { account, error }); reject(error);
          });
        });
      },
      assertLoginGeneration() {},
      async beginLogin() {
        if (existsSync(join(dataDir, 'accounts', '11', 'account.json')) && !existsSync(join(dataDir, 'force-scan'))) {
          if (existsSync(join(dataDir, 'restore-fault'))) throw new Error('Session恢复未通过校验：cookie=DO-NOT-LEAK');
          account.online = true; client.emit('system.online', { account }); account.resolveLogin(); return;
        }
        await account.auth.beginLogin();
      },
      async seedThreadShortIds() {
        if (!existsSync(join(dataDir, 'login-fault'))) return;
        const kind = readFileSync(join(dataDir, 'login-fault'), 'utf8').trim();
        if (kind === 'http') throw Object.assign(new Error('IM Cookie HTTP 503 /v2/message/get_by_user_init?token=DO-NOT-LEAK Cookie=DO-NOT-LEAK'),
          { name: 'ImProtoTransportError', cmd: 203, stage: 'http', endpoint: '/v2/message/get_by_user_init?token=DO-NOT-LEAK' });
        if (kind === 'network') throw Object.assign(new Error('request failed sessionid=DO-NOT-LEAK'),
          { name: 'ImProtoTransportError', cmd: 203, stage: 'network', endpoint: '/v2/message/get_by_user_init?token=DO-NOT-LEAK', cause: { code: 'UND_ERR_CONNECT_TIMEOUT', message: 'Cookie=DO-NOT-LEAK' } });
        if (kind === 'im') throw new Error('IM 初始化失败: status=500 Cookie=DO-NOT-LEAK; token=DO-NOT-LEAK');
        throw new TypeError('Cannot read private cookie=DO-NOT-LEAK from token=DO-NOT-LEAK');
      },
      async continueLogin() {
        if (existsSync(join(dataDir, 'block-login'))) return new Promise(() => {});
        const uid = existsSync(join(dataDir, 'scan-uid')) ? readFileSync(join(dataDir, 'scan-uid'), 'utf8').trim() : '11';
        try { client.store.promote('pending-fixture', uid); await account.seedThreadShortIds(); }
        catch (error) { client.emit('system.login.error', { account, error }); account.rejectLogin(error); throw error; }
        account.uid = account.imUid = uid;
        account.online = true; client.emit('system.online', { account }); account.resolveLogin();
        setTimeout(() => {
          const message = { threadId: '0:1:11:22', conversationShortId: '2200', senderUid: '22', serverMessageId: 'live-1',
            rawContent: '{"text":"实时消息"}', text: '实时消息', messageType: 7, createTime: '1720000001000', clientMessageId: 'live-client' };
          client.emit('message', message);
          client.emit('notice.message.batch-update', { updates: [{ conversation: info('22'), messages: [{ ...message, msgType: 7, content: message.rawContent, msgId: 'catchup-1', serverMessageId: 'catchup-1' }] }] });
        }, 30);
      },
      async logout() { account.online = false; account.rejectLogin?.(new Error('cancelled')); client.emit('system.offline', { account }); },
      markActive() {
        trace(dataDir, { op: 'activity', uid: account.uid });
        if (!existsSync(join(dataDir, 'verification-action'))) return;
        const action = readFileSync(join(dataDir, 'verification-action'), 'utf8').trim();
        if (action === 'start' && !account.fixtureChallenge) account.fixtureVerification();
        if (action === 'complete') account.fixtureChallenge?.resolve();
        if (action === 'fail') account.fixtureChallenge?.reject(new Error('private token=DO-NOT-LEAK cookie=DO-NOT-LEAK'));
      },
      fixtureVerification() {
        let resolve, reject;
        const opening = new Promise((done, fail) => { resolve = done; reject = fail; });
        const verification = { operation: 'token-beat', methods: ['captcha'],
          open: () => opening,
          async openUrl() { return 'http://127.0.0.1:12345/?token=private-fixture-challenge'; },
          cancel() { trace(dataDir, { op: 'cancel-verification' }); reject(new Error('cancel token=DO-NOT-LEAK')); },
        };
        account.fixtureChallenge = { resolve, reject };
        client.emit('system.login.verification', { account, verification });
      },
      fixtureIncoming(id) {
        setTimeout(() => client.emit('message', { threadId: '0:1:11:22', conversationShortId: '2200', senderUid: '22',
          serverMessageId: id, rawContent: '{"text":"离线夹具消息"}', text: '离线夹具消息', messageType: 7,
          createTime: '1720000001000', clientMessageId: `${id}-client` }), 30);
      },
      fixtureReconnect() {
        client.emit('system.reconnecting', { account });
        setTimeout(() => { client.emit('system.online', { account }); account.fixtureIncoming('reconnected-live'); }, 30);
      },
      pickFriend: peer => account.contacts.get(peer), pickStranger: peer => account.strangers?.get(peer),
      applyConversationInfo(value) { const peer=value.conversationId.split(':').at(-1); account.contacts.set(peer, contact(account,peer,value.conversationId,value.conversationShortId)); },
      rememberStranger(value) { account.strangers ??= new Map(); account.strangers.set(value.uid,value); },
    };
    account.auth = { async beginLogin() {
      client.emit('system.login.qrcode', { account, qrcodeBase64: 'ZmFrZS1wbmc=', expireTime: 120 });
    } };
    account.im = {
      async createPrivateConversation(ids) { trace(dataDir, { op: 'create', ids }); return { statusCode: 0, conversation: info(ids[1]) }; },
      async listThreads(options) { trace(dataDir, { op: 'threads', cursor: options.cursor }); return { statusCode: 0, hasMore: options.cursor==='0', cursor: 'threads-next', conversations: [info(options.cursor==='0'?'22':'33')] }; },
      async getRecentStrangerMessages() { return { statusCode: 0, hasMore: false, nextStrangerVersion: '0', messages: [{ ...info('44'), messages: [] }] }; },
    };
    client.account = account; return account;
  };
  client.logout = async () => { await client.account?.logout(); };
  return client;
}
"""


@pytest.fixture
def sdk_runtime(monkeypatch, tmp_path):
    """真实桥脚本加载临时假包，目录和进程都按业务账号隔离。"""
    root = tmp_path / 'sdk'
    root.mkdir()
    shutil.copyfile(transport._BRIDGE_ROOT / 'bridge.mjs', root / 'bridge.mjs')
    package = root / 'node_modules' / 'douyin-im'
    package.mkdir(parents=True)
    (package / 'package.json').write_text(json.dumps({'name': 'douyin-im', 'type': 'module', 'exports': './index.mjs'}), encoding='utf-8')
    (package / 'index.mjs').write_text(FAKE_SDK, encoding='utf-8')
    monkeypatch.setattr(transport, '_BRIDGE_ROOT', root)
    monkeypatch.setattr(transport.account_center, 'account_auth_path', lambda account_id, *_: tmp_path / account_id / 'user-profile')
    yield tmp_path
    for account_id in list(transport._bridges):
        transport.close_send_auth(account_id)


def _login(account_id='business-a'):
    """走完整跨进程扫码流程，仅二维码与服务器行为为离线夹具。"""
    events = []
    credentials = transport.login(account_id, events.append, threading.Event())
    assert credentials['account_id'] == account_id and credentials['sdk_uid'] == '11'
    assert any(value.get('type') == 'qrcode' for value in events)
    return credentials


def test_node_bridge_login_first_send_rejection_and_uncertain_are_not_retried(sdk_runtime):
    credentials = _login()
    result = transport.send_text(credentials, '22', 'hello', account_id='business-a')
    assert result['status'] == 'accepted' and result['platform_message_id'] == 'send-1'
    assert result['client_message_id'] == 'client-send-1'
    rejected = transport.send_text(credentials, '22', 'reject', account_id='business-a')
    assert rejected['status'] == 'failed'
    with pytest.raises(TransportSendUncertain) as error:
        transport.send_text(credentials, '22', 'uncertain', account_id='business-a')
    assert error.value.result['status'] == 'uncertain'
    trace = [json.loads(line) for line in (sdk_runtime / 'business-a' / 'douyin-im-sdk' / 'trace.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len([value for value in trace if value['op'] == 'create']) == 1
    assert len([value for value in trace if value.get('text') == 'uncertain']) == 1
    with pytest.raises(ValueError, match='当前业务账号'):
        transport.send_text(credentials, '22', 'wrong account', account_id='business-b')
    assert 'business-b' not in transport._bridges


def test_node_bridge_history_paginates_and_preserves_server_time_and_client_ids(sdk_runtime):
    _login()
    messages = transport.sync_history('business-a')
    assert len(messages) == 6
    assert {value['peer_user_id'] for value in messages} == {'22', '33', '44'}
    assert all(value['history'] and value['time_source'] == 'platform' for value in messages)
    assert all(value['raw_content']['client_message_id'] and value['raw_content']['platform_create_time'] for value in messages)
    assert all(value['sent_at'].endswith('Z') for value in messages)
    assert {value['direction'] for value in messages} == {'incoming', 'outgoing'}
    assert len(transport.sync_history('business-a', '33')) == 2
    profile = transport.get_user_profile({'account_id': 'business-a', 'sdk': 'douyin-im', 'sdk_uid': '11'}, '22')
    assert profile['nickname'] == '客户22'


def test_node_realtime_and_catchup_remain_distinct(sdk_runtime):
    credentials = _login()
    stop = threading.Event()
    messages, states = [], []

    def received(event):
        messages.append(event)
        if len(messages) == 2:
            stop.set()

    transport.receive_forever(credentials, received, lambda *value: states.append(value), stop)
    assert [value['history'] for value in messages] == [False, True]
    assert states[-1] == ('stopped', '')
    assert 'business-a' not in transport._bridges


def test_real_bridge_preserves_plain_and_sdk_json_history_text(sdk_runtime):
    _login()
    (sdk_runtime / 'business-a' / 'douyin-im-sdk' / 'body-variants').write_text('1', encoding='utf-8')
    messages = transport.sync_history('business-a', '22')
    assert [value['content'] for value in messages] == [
        '纯文本正文', 'content字段正文', 'display字段正文', '[]', '{broken',
    ]
    assert messages[-1]['raw_content']['unparsed_content'] == '{broken'


def test_status_has_no_process_or_network_and_restore_without_session_never_scans(sdk_runtime, monkeypatch):
    monkeypatch.setattr(transport, '_bridge', lambda *_: pytest.fail('只读状态不应启动 SDK'))
    assert transport.session_status('business-a')['saved'] is False
    assert transport.restore_session('business-a')['receive_ready'] is False
    transport.close_send_auth('missing-or-xhs')


def test_activity_only_reports_to_existing_online_account_and_never_creates_or_restores(sdk_runtime, monkeypatch):
    """活动上报不能变成隐藏登录入口，也不能误报到其它业务账号。"""
    with monkeypatch.context() as guard:
        guard.setattr(transport, '_bridge', lambda *_: pytest.fail('活动通知不能创建或恢复 SDK'))
        assert transport.mark_active('business-a') is False
        assert transport.cancel_verification('business-a') is False
        current = transport._Bridge('business-a')
        transport._bridges['business-a'] = current
        assert transport.mark_active('business-a') is False
        assert transport.cancel_verification('business-a') is False
        assert not (sdk_runtime / 'business-a' / 'douyin-im-sdk' / 'accounts').exists()
    credentials = _login()
    # 定时状态、资料、历史和普通收发不自动伪装成真实用户操作。
    transport.session_status('business-a')
    transport.sync_history('business-a', '22')
    transport.get_user_profile(credentials, '22')
    transport.send_text(credentials, '22', 'background-send', account_id='business-a')
    trace_file = sdk_runtime / 'business-a' / 'douyin-im-sdk' / 'trace.jsonl'
    assert not any(json.loads(line)['op'] == 'activity' for line in trace_file.read_text(encoding='utf-8').splitlines())
    assert transport.mark_active('business-a') is True
    assert transport.mark_active('business-b') is False
    trace = [json.loads(line) for line in trace_file.read_text(encoding='utf-8').splitlines()]
    assert [value for value in trace if value['op'] == 'activity'] == [{'op': 'activity', 'uid': '11'}]
    current.close()
    assert transport.mark_active('business-a') is False
    assert transport._bridges['business-a'] is current
    transport.close_send_auth('business-a')
    assert transport.mark_active('business-a') is False
    assert 'business-a' not in transport._bridges and 'business-b' not in transport._bridges


@pytest.mark.parametrize('outcome', ['complete', 'cancel', 'fail'])
def test_online_renewal_verification_finishes_without_stale_urls_or_destroying_receive(sdk_runtime, outcome):
    """离线验证挑战模拟结果；真实桥接与接收线程须保留原账号连接并清理临时 URL。"""
    credentials = _login()
    current = transport._bridges['business-a']
    action = sdk_runtime / 'business-a' / 'douyin-im-sdk' / 'verification-action'
    action.write_text('start', encoding='utf-8')
    states = []
    stop = threading.Event()
    listener = threading.Thread(target=transport.receive_forever,
                                args=(credentials, lambda *_: None, lambda *value: states.append(value), stop))
    listener.start()

    def wait_for(predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        pytest.fail('验证状态未在预期时间内更新')

    try:
        assert transport.mark_active('business-a') is True
        wait_for(lambda: bool(transport.session_status('business-a')['verification'].get('url')))
        status = transport.session_status('business-a')
        assert status['verification']['operation'] == 'token-beat'
        assert status['verification']['methods'] == ['captcha']
        assert status['state'] == 'connected'
        if outcome == 'cancel':
            assert transport.cancel_verification('business-a') is True
        else:
            action.write_text(outcome, encoding='utf-8')
            assert transport.mark_active('business-a') is True
        wait_for(lambda: not transport.session_status('business-a')['verification'].get('url'))
        status = transport.session_status('business-a')
        if outcome == 'fail':
            assert status['verification']['error'] and status['verification']['operation'] == 'token-beat'
            assert 'DO-NOT-LEAK' not in json.dumps(status['verification'])
        else:
            assert status['verification'] == {}
        assert status['state'] == 'connected' and transport._bridges['business-a'] is current
        assert listener.is_alive() and not any(value[0] == 'error' for value in states)
        assert transport.cancel_verification('business-a') is False
        assert transport.send_text(credentials, '22', 'after-verification', account_id='business-a')['status'] == 'accepted'
    finally:
        stop.set()
        listener.join(timeout=5)
    assert not listener.is_alive()


def test_offline_bridge_reconnect_and_fresh_process_restore_preserve_send_receive_without_rescanning(sdk_runtime):
    """网络仅由夹具触发事件；验证真实 Node/Python 重连分派、重启与已存身份恢复。"""
    credentials = _login()
    first_pid = transport._bridges['business-a'].process.pid
    assert transport.send_text(credentials, '22', 'before-reconnect', account_id='business-a')['status'] == 'accepted'
    assert transport.send_text(credentials, '22', 'reconnect-fixture', account_id='business-a')['status'] == 'accepted'
    messages, states = [], []
    stop = threading.Event()

    def after_reconnect(event):
        messages.append(event)
        if event['platform_message_id'] == 'reconnected-live':
            stop.set()

    deadline = threading.Timer(5, stop.set)
    deadline.start()
    try:
        transport.receive_forever(credentials, after_reconnect, lambda *value: states.append(value), stop)
    finally:
        deadline.cancel()
    assert ('reconnecting', '私信连接中断，正在自动重连') in states
    assert any(value['platform_message_id'] == 'reconnected-live' and not value['history'] for value in messages)
    assert 'business-a' not in transport._bridges
    # 同一持久化身份在全新 Node 进程恢复，无二维码、无新正式凭据提升。
    assert transport.send_text(credentials, '22', 'after-restart', account_id='business-a')['status'] == 'accepted'
    restored = transport._bridges['business-a']
    assert restored.process.pid != first_pid
    assert not any(value['event'] == 'qrcode' for value in list(restored.login_events.queue))
    assert transport.send_text(credentials, '22', 'receive-restored-fixture', account_id='business-a')['status'] == 'accepted'
    stop = threading.Event()
    restored_messages = []

    def after_restart(event):
        restored_messages.append(event)
        stop.set()

    deadline = threading.Timer(5, stop.set)
    deadline.start()
    try:
        transport.receive_forever(credentials, after_restart, lambda *_: None, stop)
    finally:
        deadline.cancel()
    assert restored_messages[0]['platform_message_id'] == 'restored-live'
    trace = [json.loads(line) for line in (sdk_runtime / 'business-a' / 'douyin-im-sdk' / 'trace.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len([value for value in trace if value['op'] == 'promote']) == 1
    assert not any(value['op'] == 'activity' for value in trace)


def test_cancelling_pending_scan_stops_real_node_promptly(sdk_runtime):
    directory = sdk_runtime / 'business-a' / 'douyin-im-sdk'
    directory.mkdir(parents=True)
    (directory / 'block-login').write_text('1', encoding='utf-8')
    stop = threading.Event()
    errors = []

    def scanning():
        try:
            transport.login('business-a', lambda value: stop.set() if value['type'] == 'qrcode' else None, stop)
        except ValueError as error:
            errors.append(str(error))

    worker = threading.Thread(target=scanning)
    worker.start()
    worker.join(timeout=6)
    assert not worker.is_alive() and errors
    assert 'business-a' not in transport._bridges


def test_bridge_rejects_conflicting_platform_identity_before_send(sdk_runtime):
    credentials = _login()
    wrong = {**credentials, 'sdk_uid': '99'}
    with pytest.raises(ValueError, match='账号身份不一致'):
        transport.send_text(wrong, '22', 'never-send', account_id='business-a')


def test_wrong_scan_is_rejected_before_credentials_are_written_and_original_can_rescan(sdk_runtime):
    _login()
    transport.close_send_auth('business-a')
    folder = sdk_runtime / 'business-a' / 'douyin-im-sdk'
    original = (folder / 'accounts' / '11' / 'account.json').read_bytes()
    (folder / 'force-scan').write_text('1', encoding='utf-8')
    (folder / 'scan-uid').write_text('99', encoding='utf-8')
    with pytest.raises(ValueError, match='原绑定不一致'):
        transport.login('business-a', lambda *_: None, threading.Event(), expected_uid='11')
    assert not (folder / 'accounts' / '99').exists()
    assert (folder / 'accounts' / '11' / 'account.json').read_bytes() == original
    (folder / 'scan-uid').write_text('11', encoding='utf-8')
    restored = transport.login('business-a', lambda *_: None, threading.Event(), expected_uid='11')
    assert restored['sdk_uid'] == '11'


def test_saved_session_restore_failure_does_not_block_explicit_rescan(sdk_runtime):
    _login()
    transport.close_send_auth('business-a')
    folder = sdk_runtime / 'business-a' / 'douyin-im-sdk'
    (folder / 'restore-fault').write_text('1', encoding='utf-8')
    # 后台恢复失败只能报告错误，不能私自切换到扫码。
    status = transport.restore_session('business-a')
    assert status['receive_ready'] is False and '恢复已保存会话' in status['error']
    current = transport._bridges['business-a']
    assert not any(value['event'] == 'qrcode' for value in list(current.events.queue))
    # 用户点击扫码必须执行新认证，不能再次困在恢复探针中。
    events = []
    credentials = transport.login('business-a', events.append, threading.Event())
    assert credentials['sdk_uid'] == '11'
    assert any(value.get('type') == 'qrcode' for value in events)


def test_explicit_rescan_of_online_instance_performs_new_authentication(sdk_runtime):
    _login()
    current = transport._bridges['business-a']
    events = []
    credentials = transport.login('business-a', events.append, threading.Event(), expected_uid='11')
    assert credentials['sdk_uid'] == '11' and transport._bridges['business-a'] is current
    assert any(value.get('type') == 'qrcode' for value in events)
    trace = [json.loads(line) for line in (sdk_runtime / 'business-a' / 'douyin-im-sdk' / 'trace.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len([value for value in trace if value['op'] == 'promote']) == 2


def test_saved_identity_guards_wrong_rescan_even_before_host_marker_exists(sdk_runtime):
    _login()
    transport.close_send_auth('business-a')
    folder = sdk_runtime / 'business-a' / 'douyin-im-sdk'
    original = (folder / 'accounts' / '11' / 'account.json').read_bytes()
    (folder / 'scan-uid').write_text('99', encoding='utf-8')
    with pytest.raises(ValueError, match='原绑定不一致'):
        transport.login('business-a', lambda *_: None, threading.Event())
    assert not (folder / 'accounts' / '99').exists()
    assert (folder / 'accounts' / '11' / 'account.json').read_bytes() == original


def _raw_login_fault(tmp_path, kind):
    """直接读取真实 Node 的 RPC 错误，验证 Python 未消费的结构化诊断。"""
    folder = tmp_path / 'diagnostic-business' / 'douyin-im-sdk'
    folder.mkdir(parents=True)
    (folder / 'login-fault').write_text(kind, encoding='utf-8')
    process = subprocess.Popen(
        [str(transport._node_executable()), str(transport._BRIDGE_ROOT / 'bridge.mjs'),
         f'--data-dir={folder}', '--account-id=diagnostic-business'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    events = []
    try:
        assert json.loads(process.stdout.readline())['event'] == 'ready'
        process.stdin.write(json.dumps({'id': 'login', 'account_id': 'diagnostic-business',
                                       'method': 'login', 'params': {'interactive': True}}) + '\n')
        process.stdin.flush()
        while True:
            value = json.loads(process.stdout.readline())
            if value.get('id') == 'login':
                return value['error'], events
            events.append(value)
    finally:
        process.stdin.close()
        process.wait(timeout=5)


@pytest.mark.parametrize(('kind', 'error_type', 'detail'), [
    ('http', 'ImProtoTransportError', {'http_status': 503, 'im_command': 203, 'transport_stage': 'http'}),
    ('network', 'ImProtoTransportError', {'error_code': 'UND_ERR_CONNECT_TIMEOUT', 'im_command': 203, 'transport_stage': 'network'}),
    ('im', 'Error', {'im_status': 500}),
    ('javascript', 'TypeError', {}),
])
def test_real_node_login_diagnostics_expose_stage_and_codes_without_sensitive_messages(sdk_runtime, kind, error_type, detail):
    error, events = _raw_login_fault(sdk_runtime, kind)
    diagnostics = error['diagnostics']
    assert diagnostics['phase'] == 'im_init' and diagnostics['error_type'] == error_type
    assert all(diagnostics.get(key) == value for key, value in detail.items())
    assert '初始化私信会话' in error['message'] and error_type in error['message']
    if kind in {'http', 'network'}:
        assert diagnostics['endpoint'] == '/v2/message/get_by_user_init'
    failures = [value['data'] for value in events if value['event'] == 'state' and value['data'].get('state') == 'error']
    # system.login.error、continueLogin 的拒绝和最终 RPC 使用同一个 formatter。
    assert len(failures) >= 2
    assert all(value['error'] == error['message'] and value['diagnostics'] == diagnostics for value in failures)
    serialized = json.dumps({'rpc': error, 'states': failures}, ensure_ascii=False)
    assert 'DO-NOT-LEAK' not in serialized and 'token=' not in serialized and 'cookie=' not in serialized.lower()


def test_history_missing_or_mismatched_participants_is_rejected():
    with pytest.raises(ValueError, match='当前账号'):
        transport._validate_message({'conversation_id': '0:1:33:22', 'peer_user_id': '22', 'sender_user_id': '22'}, '11')
    with pytest.raises(ValueError, match='业务账号'):
        transport._validate_message({'sdk_uid': '99', 'conversation_id': '0:1:11:22', 'peer_user_id': '22', 'sender_user_id': '22'}, '11')


def test_installed_sdk_starts_real_bridge_without_login_or_network(tmp_path):
    """实际 npm 包与现有 Playwright Node 必须可以导入，状态读取不能自动登录。"""
    process = subprocess.Popen(
        [str(transport._node_executable()), str(transport._BRIDGE_ROOT / 'bridge.mjs'),
         f'--data-dir={tmp_path}', '--account-id=offline-smoke'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    try:
        ready = json.loads(process.stdout.readline())
        assert ready['event'] == 'ready'
        process.stdin.write(json.dumps({'id': 'status', 'account_id': 'offline-smoke', 'method': 'status'}) + '\n')
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
        assert response['result']['state'] == 'disconnected' and response['result']['saved_uid'] == ''
    finally:
        process.stdin.close()
        process.wait(timeout=5)


def test_installed_sdk_renewal_uses_real_activity_throttle_and_stops_with_account(tmp_path):
    """虚拟时钟只替代时间；续期与退出实现直接导入锁定的真实 SDK，不访问网络。"""
    library = transport._BRIDGE_ROOT / 'node_modules' / 'douyin-im' / 'lib'
    script = tmp_path / 'renewal-lifecycle.test.mjs'
    source = r"""
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PassportTokenBeat } from '__TOKEN_BEAT__';
import { BaseAccount } from '__ACCOUNT__';

// SDK 的每个用例使用独立虚拟时钟，时间推进不会发出真实平台请求。
test('idle boot pauses after ten minutes and never renews without activity', t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] });
  const scenes = [];
  const renewal = new PassportTokenBeat(async scene => { scenes.push(scene); return { message: 'success' }; }, () => assert.fail('unexpected expiry'));
  renewal.start();
  assert.deepEqual(scenes, ['boot']);
  t.mock.timers.tick(600_000);
  t.mock.timers.tick(1_800_000);
  assert.deepEqual(scenes, ['boot']);
  renewal.stop();
});

test('actual activity resumes, is throttled for ten seconds, and permits only the next polling', t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] });
  const scenes = [];
  const renewal = new PassportTokenBeat(async scene => { scenes.push(scene); return { message: 'success' }; }, () => assert.fail('unexpected expiry'));
  renewal.start();
  t.mock.timers.tick(600_000);
  renewal.activity();
  assert.deepEqual(scenes, ['boot', 'active']);
  for (let index = 0; index < 20; index++) renewal.activity();
  t.mock.timers.tick(9_999);
  renewal.activity();
  // 同一节流窗口的活动不会设置下一次 polling，SDK 会自行暂停。
  t.mock.timers.tick(590_001);
  assert.deepEqual(scenes, ['boot', 'active']);
  renewal.activity();
  t.mock.timers.tick(10_000);
  renewal.activity();
  t.mock.timers.tick(590_000);
  assert.deepEqual(scenes, ['boot', 'active', 'active', 'polling']);
  t.mock.timers.tick(600_000);
  assert.deepEqual(scenes, ['boot', 'active', 'active', 'polling']);
  renewal.stop();
});

class OfflineAccount extends BaseAccount {
  async beginLogin() { this.completeLogin(); }
  cancelLogin() {}
}

test('public markActive allows reconnecting but logout aborts renewal and ignores late responses', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] });
  const account = new OfflineAccount();
  const requests = [];
  await account.login();
  account.startSessionRenewal((scene, signal) => new Promise(resolve => requests.push({ scene, signal, resolve })));
  account.setAccountState('reconnecting');
  assert.equal(account.online, true);
  account.markActive();
  t.mock.timers.tick(600_000);
  assert.deepEqual(requests.map(value => value.scene), ['boot', 'polling']);
  await account.logout();
  assert.equal(account.online, false);
  assert.ok(requests.every(value => value.signal.aborted));
  assert.throws(() => account.markActive(), /账号未上线/);
  for (const value of requests) value.resolve({ message: 'error', data: { error_code: 401 } });
  await Promise.resolve();
  t.mock.timers.tick(1_800_000);
  assert.equal(requests.length, 2);
  assert.equal(account.state, 'offline');
});

test('an explicit expired response stops the real renewal controller', async t => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] });
  let requests = 0, expired = 0;
  const renewal = new PassportTokenBeat(async () => { requests++; return { message: 'error', data: { error_code: 401 } }; }, () => expired++);
  renewal.start();
  await Promise.resolve();
  assert.equal(expired, 1);
  renewal.activity();
  t.mock.timers.tick(1_800_000);
  assert.equal(requests, 1);
});
"""
    source = source.replace('__TOKEN_BEAT__', (library / 'desktop' / 'token-beat.js').as_uri())
    source = source.replace('__ACCOUNT__', (library / 'base' / 'account.js').as_uri())
    script.write_text(source, encoding='utf-8')
    result = subprocess.run([str(transport._node_executable()), '--test', str(script)], capture_output=True,
                            text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
