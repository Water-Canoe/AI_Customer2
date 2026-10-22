"""按业务账号隔离的抖音 SDK 子进程；不读取或迁移旧版 Cookie 认证。"""
from __future__ import annotations

import csv
import json
import os
import queue
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from app.services import account_center
from app.services.im_transport import TransportSendUncertain, validate_peer


_bridges: dict[str, '_Bridge'] = {}
_bridges_lock = threading.RLock()
_BRIDGE_ROOT = Path(__file__).resolve().parents[2] / 'douyin-sdk'


def _data_dir(account_id: str) -> Path:
    """账号中心验证目录归属；SDK 文件与旧 im-session.dpapi 分开保存。"""
    return account_center.account_auth_path(account_id, 'user').parent / 'douyin-im-sdk'


def _secure_data_dir(path: Path) -> None:
    """SDK 凭据依靠专用目录权限保护；Windows 使用当前用户和 SYSTEM 的 ACL。"""
    path.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        path.chmod(0o700)
        return
    options = {'capture_output': True, 'text': True, 'creationflags': subprocess.CREATE_NO_WINDOW}
    identity = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], check=True, **options)
    sid = next(csv.reader([identity.stdout.strip()]))[-1]
    if not sid.startswith('S-1-'):
        raise ValueError('无法确认当前 Windows 用户身份，SDK 凭据目录未启用')
    result = subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r',
                             f'*{sid}:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'], **options)
    if result.returncode:
        raise ValueError('无法保护 SDK 凭据目录，请检查该账号目录的 Windows 权限')


def _node_executable() -> Path:
    """复用已安装和已打包的 Playwright Node，不再下载另一套运行时。"""
    import playwright
    path = Path(playwright.__file__).resolve().parent / 'driver' / ('node.exe' if os.name == 'nt' else 'node')
    if not path.is_file():
        raise ValueError('Playwright 内置 Node 缺失，请检查现有程序环境')
    return path


def _saved_identity(account_id: str) -> tuple[str, str]:
    """只读账号 ID 与更新时间；不向调用方返回 Cookie、票据或设备凭据。"""
    folder = _data_dir(account_id) / 'accounts'
    if not folder.is_dir():
        return '', ''
    values = []
    for path in folder.glob('*/account.json'):
        if path.parent.name == '_pending':
            continue
        try:
            saved = json.loads(path.read_text(encoding='utf-8'))
            uid = validate_peer('dy', saved.get('platformUid'))
            if uid != path.parent.name:
                raise ValueError('SDK 保存的账号身份与目录不一致')
            values.append((uid, str((saved.get('meta') or {}).get('updatedAt') or '')))
        except (OSError, json.JSONDecodeError):
            raise ValueError('SDK 登录材料无法读取，请检查当前账号目录') from None
    if len(values) > 1:
        raise ValueError('当前业务账号目录包含多个抖音身份，请检查账号隔离')
    return values[0] if values else ('', '')


class _RpcError(ValueError):
    """只保留桥接层已脱敏的错误码，不暴露 SDK 原始异常。"""
    def __init__(self, value: dict[str, Any]):
        super().__init__(value.get('message') or '抖音 SDK 操作失败')
        self.code = str(value.get('code') or 'operation_failed')
        self.result = value.get('result') or {}


class _Bridge:
    """一个业务账号独占一个进程；读线程分发响应，接收线程处理业务回调。"""
    def __init__(self, account_id: str):
        self.account_id = account_id
        self.state = 'disconnected'
        self.verification: dict[str, Any] = {}
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.login_events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.pending: dict[str, queue.Queue] = {}
        self.lock = threading.RLock()
        self.closed = False
        directory = _data_dir(account_id)
        _secure_data_dir(directory)
        script = _BRIDGE_ROOT / 'bridge.mjs'
        if not script.is_file() or not (_BRIDGE_ROOT / 'node_modules' / 'douyin-im' / 'package.json').is_file():
            raise ValueError('抖音 SDK 依赖未安装，请按技术文档安装 backend/douyin-sdk 中锁定的依赖')
        try:
            self.process = subprocess.Popen(
                [str(_node_executable()), str(script), f'--data-dir={directory}', f'--account-id={account_id}'],
                cwd=str(_BRIDGE_ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', bufsize=1,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except OSError:
            raise ValueError('无法启动抖音 SDK，请检查现有 Node 运行时') from None
        threading.Thread(target=self._read, name=f'dy-sdk-rpc-{account_id}', daemon=True).start()
        threading.Thread(target=self._drain_errors, name=f'dy-sdk-stderr-{account_id}', daemon=True).start()

    def _drain_errors(self) -> None:
        """持续排空子进程诊断，避免管道堵塞；不记录可能含凭据的上游异常。"""
        for _ in self.process.stderr:
            pass

    def _read(self) -> None:
        """响应按请求 ID 分发，账号事件只进入当前账号的队列。"""
        try:
            for line in self.process.stdout:
                value = json.loads(line)
                if 'event' in value:
                    if value.get('account_id') != self.account_id:
                        raise ValueError('SDK 进程返回了其它业务账号的事件')
                    if value['event'] == 'state':
                        self.state = str(value.get('data', {}).get('state') or 'error')
                    if value['event'] == 'verification':
                        self.verification = {**value.get('data', {}), 'error': ''}
                    elif value['event'] == 'verification_failed':
                        self.verification = {**value.get('data', {}), 'url': ''}
                    elif value['event'] == 'verification_finished':
                        self.verification = {**self.verification, 'url': ''} if self.verification.get('error') else {}
                    if value['event'] in {'ready', 'qrcode', 'verification', 'verification_failed', 'verification_finished', 'state'}:
                        self.login_events.put(value)
                    self.events.put(value)
                else:
                    with self.lock:
                        pending = self.pending.get(str(value.get('id')))
                    if pending:
                        pending.put(value)
        except (OSError, ValueError):
            pass
        finally:
            self.state = 'disconnected'
            self.verification = {}
            self.events.put({'event': 'closed', 'data': {}, 'account_id': self.account_id})
            with self.lock:
                for pending in self.pending.values():
                    pending.put({'error': {'code': 'process_closed', 'message': '抖音 SDK 连接已停止，请重新连接'}})
            self.close()

    def request(self, method: str, params: dict | None = None, *, timeout: float = 90,
                on_event: Callable[[dict], None] | None = None, stop_event: threading.Event | None = None):
        """轮询等待仅用于可取消的进程通信；同一 RPC 失败后绝不自动重发。"""
        request_id = uuid.uuid4().hex
        response: queue.Queue = queue.Queue()
        with self.lock:
            if self.closed or self.process.poll() is not None:
                raise _RpcError({'code': 'process_closed', 'message': '抖音 SDK 连接未启动'})
            self.pending[request_id] = response
            try:
                self.process.stdin.write(json.dumps({'id': request_id, 'account_id': self.account_id,
                                                    'method': method, 'params': params or {}}, ensure_ascii=False) + '\n')
                self.process.stdin.flush()
            except OSError:
                self.pending.pop(request_id, None)
                raise _RpcError({'code': 'process_closed', 'message': '抖音 SDK 连接已停止'}) from None
        deadline = time.monotonic() + timeout
        def report_login_events() -> None:
            # 登录响应可能与二维码同一批到达；返回前也要分发，实时消息始终留给监听器。
            if on_event:
                while True:
                    try:
                        value = self.login_events.get_nowait()
                    except queue.Empty:
                        break
                    on_event({'type': value['event'], **value.get('data', {})})
        try:
            while True:
                if stop_event and stop_event.is_set():
                    self.close()
                    raise ValueError('用户取消抖音 SDK 登录')
                report_login_events()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _RpcError({'code': 'rpc_timeout', 'message': '抖音 SDK 操作超时，请检查网络或重新连接'})
                try:
                    value = response.get(timeout=min(0.1, remaining))
                except queue.Empty:
                    continue
                if 'error' in value:
                    raise _RpcError(value['error'])
                report_login_events()
                if stop_event and stop_event.is_set():
                    self.close()
                    raise ValueError('用户取消抖音 SDK 登录')
                return value.get('result')
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def close(self) -> None:
        """关闭标准输入以取消 SDK 生命周期；有网络在途时最多等两秒再终止进程。"""
        with self.lock:
            if self.closed:
                return
            self.closed = True
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)


def _bridge(account_id: str) -> _Bridge:
    """发送、资料和接收复用同账号进程；其它业务账号绝不复用该实例。"""
    with _bridges_lock:
        current = _bridges.get(account_id)
        if current is None or current.closed or current.process.poll() is not None:
            current = _Bridge(account_id)
            _bridges[account_id] = current
        return current


def close_send_auth(account_id: str) -> None:
    """调用方先停止该账号监听并等待发送锁，再释放 SDK 进程。"""
    with _bridges_lock:
        current = _bridges.pop(account_id, None)
    if current:
        current.close()


def mark_active(account_id: str) -> bool:
    """向已有账号进程报告真实用户操作；未连接时不创建进程或恢复认证。"""
    with _bridges_lock:
        current = _bridges.get(account_id)
    if current is None or current.closed or current.process.poll() is not None:
        return False
    return bool(current.request('activity', timeout=5).get('reported'))


def cancel_verification(account_id: str) -> bool:
    """只取消现有账号的安全验证页，不主动退出账号或覆盖已保存认证。"""
    with _bridges_lock:
        current = _bridges.get(account_id)
    if current is None or current.closed or current.process.poll() is not None:
        return False
    return bool(current.request('cancel-verification', timeout=5).get('cancelled'))


def session_status(account_id: str) -> dict[str, Any]:
    """只检查本地身份与已有进程，不创建进程、不发网络请求。"""
    try:
        uid, updated_at = _saved_identity(account_id)
        with _bridges_lock:
            current = _bridges.get(account_id)
        running = current and not current.closed and current.process.poll() is None
        state = current.state if running else 'disconnected'
        return {'saved': bool(uid), 'uid': uid, 'sdk_uid': uid, 'state': state, 'synced_at': updated_at,
                'verification': dict(current.verification) if running else {},
                'receive_ready': bool(uid), 'send_ready': bool(uid),
                'error': '' if uid else '请先点击“扫码登录私信”，完成首次登录'}
    except ValueError as exc:
        return {'saved': False, 'uid': '', 'sdk_uid': '', 'state': 'error', 'synced_at': '', 'verification': {},
                'receive_ready': False, 'send_ready': False, 'error': str(exc)}


def _credentials(account_id: str, credentials: dict[str, Any]) -> str:
    """标记只证明绑定身份；旧 Cookie 格式不能静默转换到新 SDK。"""
    if credentials.get('sdk') != 'douyin-im' or str(credentials.get('account_id') or '') != account_id:
        raise ValueError('请先为当前业务账号完成抖音私信 SDK 登录')
    return validate_peer('dy', credentials.get('sdk_uid'))


def restore_session(account_id: str) -> dict[str, Any]:
    """只恢复已保存的 SDK 登录；过期时返回未就绪，等待用户明确扫码。"""
    status = session_status(account_id)
    if not status['saved']:
        return status
    try:
        value = _bridge(account_id).request('login', {'interactive': False, 'expected_uid': status['uid']})
        if value.get('sdk_uid') != status['uid']:
            raise ValueError('SDK 恢复的身份与当前业务账号不一致')
        return {**session_status(account_id), 'state': 'connected', 'receive_ready': True, 'send_ready': True, 'error': ''}
    except ValueError as exc:
        return {**status, 'receive_ready': False, 'send_ready': False, 'error': str(exc), 'state': 'error'}


def login(account_id: str, on_event: Callable[[dict], None], stop_event: threading.Event,
          *, expected_uid: str = '') -> dict[str, Any]:
    """交给账号界面扫码；固定 SDK 的实例落盘钩子在写凭据前核对原绑定 UID。"""
    expected_uid = validate_peer('dy', expected_uid) if expected_uid else ''
    current = _bridge(account_id)
    try:
        result = current.request('login', {'interactive': True, 'expected_uid': expected_uid}, timeout=360,
                                 on_event=on_event, stop_event=stop_event)
        return {'account_id': account_id, 'sdk': 'douyin-im', 'sdk_uid': validate_peer('dy', result.get('sdk_uid')),
                'nickname': str(result.get('nickname') or '')}
    except BaseException:
        close_send_auth(account_id)
        raise


def _online(account_id: str, credentials: dict[str, Any], stop_event: threading.Event | None = None) -> _Bridge:
    """每次从可信身份标记验证账号，SDK 的网络恢复仍由其自身生命周期负责。"""
    uid = _credentials(account_id, credentials)
    current = _bridge(account_id)
    current.request('login', {'interactive': False, 'expected_uid': uid}, stop_event=stop_event)
    return current


def _validate_message(event: dict, own_uid: str) -> dict:
    """进程输出仍需校验参与者，避免错误桥接把消息写入其它客户。"""
    peer = validate_peer('dy', event.get('peer_user_id'))
    participants = str(event.get('conversation_id') or '').split(':')
    if event.get('sdk_uid') and str(event['sdk_uid']) != own_uid:
        raise ValueError('SDK 返回的消息账号与当前业务账号不一致')
    sender_identity = str(event.get('own_user_id') or own_uid)
    if len(participants) != 4 or participants[:2] != ['0', '1'] or peer not in participants[2:] or sender_identity not in participants[2:]:
        raise ValueError('SDK 返回的私信会话与当前账号不一致')
    if str(event.get('sender_user_id')) not in {sender_identity, own_uid, peer}:
        raise ValueError('SDK 返回的消息发送者与当前会话不一致')
    return event


def receive_forever(credentials: dict[str, Any], on_message: Callable[[dict], None],
                    on_state: Callable[[str, str], None], stop_event: threading.Event) -> None:
    """阻塞消费当前账号事件；回补消息保留 history 标记供服务层只入库。"""
    account_id = str(credentials.get('account_id') or '')
    uid = _credentials(account_id, credentials)
    on_state('connecting', '')
    try:
        current = _online(account_id, credentials, stop_event)
        on_state('connected', '')
        while not stop_event.is_set():
            try:
                value = current.events.get(timeout=0.1)
            except queue.Empty:
                continue
            kind, data = value.get('event'), value.get('data') or {}
            if kind == 'message':
                on_message(_validate_message(data, uid))
            elif kind == 'state':
                on_state(str(data.get('state') or 'error'), str(data.get('error') or ''))
            elif kind == 'closed':
                if not stop_event.is_set():
                    raise ValueError('抖音 SDK 连接已停止，请重新连接')
                break
    finally:
        close_send_auth(account_id)
        if stop_event.is_set():
            on_state('stopped', '')


def send_text(credentials: dict[str, Any], peer_user_id: str, text: str,
              conversation_id: str = '', conversation_short_id: int = 0,
              ticket: str = '', *, account_id: str) -> dict[str, Any]:
    """保持旧传输接口；SDK 对最终发送结果未知时不自动重试。"""
    peer = validate_peer('dy', peer_user_id)
    text = str(text).strip()
    if not text or len(text) > 2000:
        raise ValueError('私信内容不能为空且不能超过2000字')
    uid = _credentials(account_id, credentials)
    if peer == uid:
        raise ValueError('不能向当前账号自己发送私信')
    if conversation_id:
        _validate_message({'conversation_id': conversation_id, 'peer_user_id': peer, 'sender_user_id': uid}, uid)
    current = _online(account_id, credentials)
    try:
        result = current.request('send', {'expected_uid': uid, 'peer_user_id': peer, 'text': text,
                                          'conversation_id': conversation_id})
    except _RpcError as exc:
        if exc.code in {'send_uncertain', 'process_closed', 'rpc_timeout'}:
            raise TransportSendUncertain({**exc.result, 'status': 'uncertain', 'platform_message_id': ''}) from None
        raise
    try:
        _validate_message({**result, 'peer_user_id': peer, 'sender_user_id': result.get('own_user_id') or uid}, uid)
    except ValueError:
        raise TransportSendUncertain({'status': 'uncertain', 'platform_message_id': ''}) from None
    return result


def get_user_profile(credentials: dict[str, Any], peer_user_id: str) -> dict[str, str]:
    """只接受 SDK 返回的同 UID 昵称与头像，不拿其它账号资料补齐。"""
    account_id = str(credentials.get('account_id') or '')
    peer = validate_peer('dy', peer_user_id)
    value = _online(account_id, credentials).request('profile', {'peer_user_id': peer})
    if value.get('peer_user_id') != peer:
        raise ValueError('SDK 返回的资料与当前客户不一致')
    return value


def sync_history(account_id: str, peer_user_id: str = '') -> list[dict[str, Any]]:
    """显式分页读取平台历史；返回统一事件，调用方负责去重入库和刷新界面。"""
    status = session_status(account_id)
    if not status['saved']:
        raise ValueError(status['error'])
    peer = validate_peer('dy', peer_user_id) if peer_user_id else ''
    current = _online(account_id, {'account_id': account_id, 'sdk': 'douyin-im', 'sdk_uid': status['uid']})
    messages = current.request('history', {'peer_user_id': peer}, timeout=1800)
    return [_validate_message({**message, 'history': True}, status['uid']) for message in messages]
