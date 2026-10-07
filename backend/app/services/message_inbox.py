from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app import database, protected_settings
from app.services import account_center, message_attempts, im_session, im_transport


@dataclass
class _Listener:
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    state: str = 'connecting'
    error: str = ''


_listeners: dict[str, _Listener] = {}
_send_locks: dict[str, threading.Lock] = {}
_profile_locks: dict[str, threading.Lock] = {}
_profile_checked: set[tuple[str, str]] = set()
_profile_errors: dict[str, str] = {}
_runtime_lock = threading.RLock()
_CONVERSATION_FIELDS = 'id,account_id,peer_user_id,nickname,avatar,lead_id,platform_conversation_id,last_message,last_message_at,unread_count'
HIDDEN_CONVERSATION_PREFIX = 'message_inbox_hidden:'


def _account(account_id: str, *, ready: bool = False) -> dict[str, Any]:
    """沿用账号中心的私信功能绑定；过期账号仍可阅读本地历史。"""
    account = account_center.get_account(account_id, feature='message', require_ready=ready)
    if account['platform'] not in {'dy', 'xhs'}:
        raise ValueError('私信收件箱支持抖音和小红书账号')
    return account


def _transport_for_platform(platform: str):
    """服务层选择两个真实协议实现，共用模块不反向依赖平台模块。"""
    if platform == 'dy':
        from app.services import douyin_im_transport
        return douyin_im_transport
    from app.services import xhs_im_transport
    return xhs_im_transport


async def sync_account(account_id: str) -> dict[str, Any]:
    """平台只提供认证提取函数，共用同步组件管理 Profile 和浏览器队列。"""
    account = _account(account_id, ready=True)
    from app.services import douyin_im_session
    capture = douyin_im_session.capture_context if account['platform'] == 'dy' else im_session.capture_xhs_context
    return await im_session.sync_account(account_id, capture)


def status(account_id: str) -> dict[str, Any]:
    account = _account(account_id)
    session = im_session.session_status(account_id)
    if not account['enabled'] or account['feature_status']['message']['status'] != 'ready':
        session = {**session, 'receive_ready': False, 'send_ready': False, 'error': '账号未登录或已停用，请到账号中心检查'}
    with _runtime_lock:
        listener = _listeners.get(account_id)
        return {
            **session,
            'state': listener.state if listener else 'disconnected',
            'error': (listener.error if listener else '') or session.get('error', ''),
            'profile_error': _profile_errors.get(account_id, ''),
            'history_scope': 'local',
        }


def connect_account(account_id: str) -> dict[str, Any]:
    """一个账号保持一个监听线程；页面切换不会重新建立连接。"""
    account = _account(account_id, ready=True)
    transport = _transport_for_platform(account['platform'])
    credentials = im_session.load_credentials(account_id)
    with _runtime_lock:
        existing = _listeners.get(account_id)
        if existing and existing.thread and existing.thread.is_alive():
            return status(account_id)
        listener = _Listener()
        _listeners[account_id] = listener
        # 显式重新连接时允许重试上次失败的资料查询。
        _profile_errors.pop(account_id, None)
        _profile_checked.difference_update({key for key in _profile_checked if key[0] == account_id})

        def on_state(state: str, error: str = '') -> None:
            with _runtime_lock:
                listener.state, listener.error = state, error

        def run() -> None:
            try:
                transport.receive_forever(
                    credentials,
                    lambda event: record_received(account_id, event),
                    on_state,
                    listener.stop,
                )
            except Exception as exc:
                # 保留协议层的安全提示，未知异常只显示类别，避免泄露请求凭据。
                if listener.state not in {'error', 'expired'} or not listener.error:
                    on_state('error', f'私信监听已停止（{type(exc).__name__}），请检查连接后重新连接')
            finally:
                if listener.stop.is_set():
                    on_state('disconnected')
                elif listener.state not in {'error', 'expired'}:
                    on_state('disconnected', '私信连接已关闭，请重新连接')

        listener.thread = threading.Thread(target=run, name=f'im-receive-{account_id}', daemon=True)
        listener.thread.start()
    return status(account_id)


def disconnect_account(account_id: str) -> dict[str, Any]:
    _account(account_id)
    with _runtime_lock:
        listener = _listeners.get(account_id)
        if listener:
            listener.stop.set()
            listener.state = 'disconnecting'
    if listener and listener.thread:
        listener.thread.join(timeout=3)
    with _runtime_lock:
        # 故障后线程已经退出，也应立即完成用户的主动断开。
        if listener and (not listener.thread or not listener.thread.is_alive()):
            listener.state, listener.error = 'disconnected', ''
    release_send_session(account_id)
    return status(account_id)


def release_send_session(account_id: str) -> None:
    """等待当前发送结束再释放认证；断开、认证同步和账号维护共用。"""
    from app.services import douyin_im_transport, xhs_im_transport

    with _runtime_lock:
        lock = _send_locks.setdefault(account_id, threading.Lock())
    with lock:
        douyin_im_transport.close_send_auth(account_id)
        xhs_im_transport.close_send_auth(account_id)


def shutdown() -> None:
    """应用退出时关闭监听和发送认证；历史记录已经逐条写入数据库。"""
    with _runtime_lock:
        listeners = list(_listeners.values())
        for listener in listeners:
            listener.stop.set()
        send_accounts = list(_send_locks)
    for listener in listeners:
        if listener.thread:
            listener.thread.join(timeout=3)
    for account_id in send_accounts:
        release_send_session(account_id)


def active_accounts() -> list[str]:
    """维护操作可检查活跃连接，避免覆盖正在使用的认证材料。"""
    with _runtime_lock:
        return sorted({account_id for account_id, listener in _listeners.items() if listener.thread and listener.thread.is_alive()} | {account_id for locks in (_send_locks, _profile_locks) for account_id, lock in locks.items() if lock.locked()})


def recover_pending_sends() -> None:
    """进程中断的发送结果未知，禁止把它们标为失败后自动重发。"""
    with database.connect() as conn:
        conn.execute("UPDATE im_messages SET status='uncertain',error='应用退出时未取得发送结果，请核实后再操作' WHERE status='sending'")


def _conversation(conn: Any, account_id: str, conversation_id: int) -> dict[str, Any]:
    row = conn.execute('SELECT * FROM im_conversations WHERE id=? AND account_id=?', (conversation_id, account_id)).fetchone()
    if not row:
        raise ValueError('该账号下的会话不存在')
    return database.row_to_dict(row) or {}


def _public_conversation(conn: Any, account_id: str, conversation_id: int) -> dict[str, Any]:
    return database.row_to_dict(conn.execute(f'SELECT {_CONVERSATION_FIELDS} FROM im_conversations WHERE id=? AND account_id=?', (conversation_id, account_id)).fetchone()) or {}


def _peer_for_lead(conn: Any, lead_id: int, platform: str) -> dict[str, Any]:
    row = conn.execute('SELECT ua.* FROM user_accounts ua JOIN lead_user_accounts lead ON lead.account_id=ua.id WHERE lead.id=?', (lead_id,)).fetchone()
    if not row or row['platform'] != platform:
        raise ValueError('客户平台与所选私信账号不一致')
    peer = str(row['platform_user_id'])
    if platform == 'dy' and (not peer.isascii() or not peer.isdecimal()):
        raw = json.loads(row['raw_payload'] or '{}')
        peer = str(raw.get('uid') or raw.get('user_id') or '')
    im_transport.validate_peer(platform, peer)
    return {'peer_user_id': peer, 'nickname': row['nickname'], 'avatar': row['avatar']}


def create_conversation(account_id: str, *, lead_id: int | None = None, peer_user_id: str = '', nickname: str = '') -> dict[str, Any]:
    """建立本地单聊；首次发送时才向平台创建或查询对应会话。"""
    account = _account(account_id)
    with database.connect() as conn:
        peer = _peer_for_lead(conn, lead_id, account['platform']) if lead_id is not None else {'peer_user_id': peer_user_id.strip(), 'nickname': nickname.strip(), 'avatar': ''}
        im_transport.validate_peer(account['platform'], peer['peer_user_id'])
        conn.execute(
            '''INSERT INTO im_conversations(account_id,peer_user_id,nickname,avatar,lead_id) VALUES(?,?,?,?,?)
               ON CONFLICT(account_id,peer_user_id) DO UPDATE SET
               lead_id=COALESCE(excluded.lead_id,im_conversations.lead_id),
               nickname=CASE WHEN excluded.nickname<>'' THEN excluded.nickname ELSE im_conversations.nickname END,
               avatar=CASE WHEN excluded.avatar<>'' THEN excluded.avatar ELSE im_conversations.avatar END''',
            (account_id, peer['peer_user_id'], peer['nickname'], peer['avatar'], lead_id),
        )
        row = conn.execute('SELECT id FROM im_conversations WHERE account_id=? AND peer_user_id=?', (account_id, peer['peer_user_id'])).fetchone()
        # 主动打开仍使用原会话；只恢复列表显示，不重建聊天记录。
        conn.execute('DELETE FROM settings WHERE key=?', (f'{HIDDEN_CONVERSATION_PREFIX}{account_id}:{row["id"]}',))
        return _public_conversation(conn, account_id, int(row['id']))


def list_conversations(account_id: str, query: str = '') -> list[dict[str, Any]]:
    _account(account_id)
    with database.connect() as conn:
        return database.rows_to_dicts(conn.execute(
            f'''SELECT {_CONVERSATION_FIELDS} FROM im_conversations WHERE account_id=?
                AND NOT EXISTS(SELECT 1 FROM settings WHERE key=? || im_conversations.id)
                AND (?='' OR instr(nickname,?)>0 OR instr(last_message,?)>0 OR instr(peer_user_id,?)>0)
                ORDER BY last_message_id DESC,id DESC''',
            (account_id, f'{HIDDEN_CONVERSATION_PREFIX}{account_id}:', query, query, query, query),
        ).fetchall())


def hide_conversation(account_id: str, conversation_id: int) -> dict[str, Any]:
    """仅从当前账号的会话列表移除，保留会话和全部消息。"""
    _account(account_id)
    with database.connect() as conn:
        _conversation(conn, account_id, conversation_id)
        database.set_setting(conn, f'{HIDDEN_CONVERSATION_PREFIX}{account_id}:{conversation_id}', '1')
    return {'ok': True}


def refresh_conversation_profiles(account_id: str) -> None:
    """响应返回后补查缺失资料，不阻塞列表或私信监听，不随轮询重复查询。"""
    with _runtime_lock:
        lock = _profile_locks.setdefault(account_id, threading.Lock())
        if _profile_errors.get(account_id) or not lock.acquire(blocking=False):
            return
    try:
        if not status(account_id)['receive_ready']:
            return
        with database.connect() as conn:
            peers = [row['peer_user_id'] for row in conn.execute("SELECT peer_user_id FROM im_conversations WHERE account_id=? AND (nickname='' OR avatar='')", (account_id,))]
        with _runtime_lock:
            peers = [peer for peer in peers if (account_id, peer) not in _profile_checked]
        if not peers:
            return
        credentials = im_session.load_credentials(account_id)
        transport = _transport_for_platform(_account(account_id)['platform'])
        for peer in peers:
            with _runtime_lock:
                if (account_id, peer) in _profile_checked:
                    continue
                _profile_checked.add((account_id, peer))
            profile = transport.get_user_profile(credentials, peer)
            with database.connect() as conn:
                conn.execute('UPDATE im_conversations SET nickname=?,avatar=? WHERE account_id=? AND peer_user_id=?', (profile['nickname'], profile['avatar'], account_id, peer))
    except ValueError as exc:
        with _runtime_lock:
            _profile_errors[account_id] = str(exc)
    finally:
        lock.release()


def list_messages(account_id: str, conversation_id: int, *, before_id: int = 0, limit: int = 50) -> dict[str, Any]:
    _account(account_id)
    if not 1 <= limit <= 100 or before_id < 0:
        raise ValueError('消息分页参数无效')
    with database.connect() as conn:
        _conversation(conn, account_id, conversation_id)
        rows = database.rows_to_dicts(conn.execute(
            '''SELECT * FROM im_messages WHERE account_id=? AND conversation_id=? AND (?=0 OR id<?)
               ORDER BY id DESC LIMIT ?''', (account_id, conversation_id, before_id, before_id, limit + 1),
        ).fetchall())
    return {'messages': list(reversed(rows[:limit])), 'has_more': len(rows) > limit, 'history_scope': 'local'}


def mark_read(account_id: str, conversation_id: int, up_to_id: int) -> dict[str, Any]:
    """只清除工作台未读数量，不声称已经同步平台已读回执。"""
    _account(account_id)
    with database.connect() as conn:
        _conversation(conn, account_id, conversation_id)
        # 只确认本次已展示的消息，读取与确认之间到达的新消息仍保持未读。
        conn.execute("UPDATE im_conversations SET unread_count=(SELECT COUNT(*) FROM im_messages WHERE conversation_id=? AND direction='incoming' AND id>?) WHERE id=? AND account_id=?", (conversation_id, up_to_id, conversation_id, account_id))
    return {'ok': True}


def record_received(account_id: str, event: dict[str, Any]) -> None:
    """逐条持久化推送，以平台消息 ID 去重；本地历史不补造远程旧消息。"""
    account = _account(account_id, ready=True)
    peer = str(event.get('peer_user_id') or '')
    message_id = str(event.get('platform_message_id') or '')
    im_transport.validate_peer(account['platform'], peer)
    if not message_id or not event.get('conversation_id'):
        raise ValueError('私信推送缺少消息或会话标识')
    if event.get('direction') == 'outgoing':
        # 本地发出的消息由发送入口保存，跳过本人推送避免重复气泡。
        return
    with database.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute('SELECT 1 FROM im_messages WHERE account_id=? AND platform_message_id=?', (account_id, message_id)).fetchone()
        if existing:
            return
        lead = conn.execute('SELECT lead.id,ua.nickname,ua.avatar FROM lead_user_accounts lead JOIN user_accounts ua ON ua.id=lead.account_id WHERE ua.platform=? AND ua.platform_user_id=?', (account['platform'], peer)).fetchone()
        conn.execute(
            '''INSERT INTO im_conversations(account_id,peer_user_id,nickname,avatar,lead_id,platform_conversation_id,conversation_short_id)
               VALUES(?,?,?,?,?,?,?) ON CONFLICT(account_id,peer_user_id) DO UPDATE SET
               platform_conversation_id=excluded.platform_conversation_id,conversation_short_id=excluded.conversation_short_id,
               nickname=CASE WHEN excluded.nickname<>'' THEN excluded.nickname ELSE im_conversations.nickname END,
               avatar=CASE WHEN excluded.avatar<>'' THEN excluded.avatar ELSE im_conversations.avatar END,
               lead_id=COALESCE(im_conversations.lead_id,excluded.lead_id)''',
            (account_id, peer, lead['nickname'] if lead else str(event.get('nickname') or ''), lead['avatar'] if lead else str(event.get('avatar') or ''), lead['id'] if lead else None, str(event['conversation_id']), int(event.get('conversation_short_id') or 0)),
        )
        conversation_id = int(conn.execute('SELECT id FROM im_conversations WHERE account_id=? AND peer_user_id=?', (account_id, peer)).fetchone()['id'])
        # 仅去重后真实新增的消息恢复列表，重放旧推送不会打扰用户。
        conn.execute('DELETE FROM settings WHERE key=?', (f'{HIDDEN_CONVERSATION_PREFIX}{account_id}:{conversation_id}',))
        content = str(event.get('content') or '')
        sent_at = str(event.get('sent_at') or datetime.now().astimezone().isoformat(timespec='seconds'))
        cursor = conn.execute(
            "INSERT INTO im_messages(account_id,conversation_id,platform_message_id,direction,content,message_type,raw_content,status,sent_at) VALUES(?,?,?,'incoming',?,?,?,'received',?)",
            (account_id, conversation_id, message_id, content, str(event.get('message_type') or 'text'), json.dumps(event.get('raw_content') or {}, ensure_ascii=False), sent_at),
        )
        conn.execute('UPDATE im_conversations SET last_message=?,last_message_at=?,last_message_id=?,unread_count=unread_count+1 WHERE id=?', (content[:500], sent_at, cursor.lastrowid, conversation_id))
        linked_lead = conn.execute('SELECT lead_id FROM im_conversations WHERE id=?', (conversation_id,)).fetchone()['lead_id']
        if linked_lead is not None:
            # 收到消息只推进等待回复状态，保留筛选结论及成交/非客户等人工判断。
            row = conn.execute('SELECT follow_status,hidden FROM lead_user_accounts WHERE id=?', (linked_lead,)).fetchone()
            if row and not row['hidden'] and row['follow_status'] in {'已私信', '未回复'}:
                conn.execute("UPDATE lead_user_accounts SET follow_status='已回复',manual_follow_status=1,updated_at=datetime('now','localtime') WHERE id=?", (linked_lead,))
                conn.execute("INSERT INTO lead_status_events(lead_account_id,from_status,to_status,note) VALUES(?,?,'已回复','私信收件箱收到客户新消息')", (linked_lead, row['follow_status']))


def send_to_customer(account_id: str, lead_id: int, text: str, *, reserve_attempt: bool = True) -> dict[str, Any]:
    conversation = create_conversation(account_id, lead_id=lead_id)
    return send_message(account_id, int(conversation['id']), text, reserve_attempt=reserve_attempt)


def send_message(account_id: str, conversation_id: int, text: str, *, reserve_attempt: bool = True) -> dict[str, Any]:
    """人工和自动任务复用发送入口；不确定结果保存下来，不自动重发。"""
    text = text.strip()
    if not text or len(text) > 2000:
        raise ValueError('私信内容不能为空，且不能超过2000字')
    with _runtime_lock:
        lock = _send_locks.setdefault(account_id, threading.Lock())
    # 同一账号的协议会话认证与发送按顺序执行，不占用浏览器队列。
    with lock:
        # 排队后重新检查账号并读取认证，不能沿用等待前的登录材料。
        account = _account(account_id, ready=True)
        transport = _transport_for_platform(account['platform'])
        session = im_session.session_status(account_id)
        if not session.get('send_ready'):
            raise ValueError(session.get('error') or '私信发送认证未就绪，请到账号中心同步认证')
        credentials = im_session.load_credentials(account_id)
        with database.connect() as conn:
            conversation = _conversation(conn, account_id, conversation_id)
        attempt_id = message_attempts.reserve_message_attempt(conversation['lead_id'], 'inbox', str(conversation_id), allow_repeat=True) if reserve_attempt else None
        sent_at = datetime.now().astimezone().isoformat(timespec='seconds')
        with database.connect() as conn:
            cursor = conn.execute("INSERT INTO im_messages(account_id,conversation_id,direction,content,status,sent_at) VALUES(?,?,'outgoing',?,'sending',?)", (account_id, conversation_id, text, sent_at))
            local_id = int(cursor.lastrowid)
            conn.execute('UPDATE im_conversations SET last_message=?,last_message_at=?,last_message_id=? WHERE id=?', (text[:500], sent_at, local_id, conversation_id))
        try:
            result = transport.send_text(credentials, conversation['peer_user_id'], text, conversation_id=conversation['platform_conversation_id'], conversation_short_id=conversation['conversation_short_id'], ticket=protected_settings.unprotect(conversation['ticket']), account_id=account_id)
        except transport.TransportSendUncertain as exc:
            result = exc.result
        except ValueError:
            result = {'status': 'failed', 'error': '发送前的账号认证或会话准备失败，请检查账号状态'}
        except Exception:
            result = {'status': 'uncertain', 'error': '未取得明确发送结果，请核实后再操作'}
        send_status = str(result.get('status') or 'uncertain')
        if send_status not in {'accepted', 'failed', 'uncertain'}:
            send_status = 'uncertain'
        error = str(result.get('error') or '')[:2000]
        with database.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('UPDATE im_messages SET status=?,error=?,platform_message_id=?,client_message_id=? WHERE id=?', (send_status, error, str(result.get('platform_message_id') or ''), str(result.get('client_message_id') or ''), local_id))
            ticket = protected_settings.protect(str(result['ticket'])) if result.get('ticket') else conversation['ticket']
            conn.execute('UPDATE im_conversations SET platform_conversation_id=?,conversation_short_id=?,ticket=? WHERE id=? AND account_id=?', (str(result.get('conversation_id') or conversation['platform_conversation_id']), int(result.get('conversation_short_id') or conversation['conversation_short_id']), ticket, conversation_id, account_id))
            message = database.row_to_dict(conn.execute('SELECT * FROM im_messages WHERE id=?', (local_id,)).fetchone()) or {}
            if reserve_attempt and send_status == 'accepted' and conversation['lead_id'] is not None:
                lead_id = conversation['lead_id']
                row = conn.execute('SELECT follow_status,hidden FROM lead_user_accounts WHERE id=?', (lead_id,)).fetchone()
                # 人工发送只推进待触达客户，不覆盖成交或其它人工结论。
                if row and not row['hidden'] and row['follow_status'] in {'待筛选', '未私信'}:
                    conn.execute("UPDATE lead_user_accounts SET follow_status='已私信',manual_follow_status=1,updated_at=datetime('now','localtime') WHERE id=?", (lead_id,))
                    conn.execute("INSERT INTO lead_status_events(lead_account_id,from_status,to_status,note) VALUES(?,?,'已私信','私信收件箱提交消息')", (lead_id, row['follow_status']))
        if attempt_id is not None:
            message_attempts.finish_message_attempt(attempt_id, 'succeeded' if send_status == 'accepted' else send_status, error)
        return message
