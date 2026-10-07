from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from app.api_dependencies import require_license
from app.services import message_inbox, douyin_im_session


router = APIRouter(prefix='/api/message-inbox', tags=['message-inbox'])


class ConversationCreate(BaseModel):
    account_id: str = Field(min_length=1)
    lead_id: int | None = Field(default=None, gt=0)
    peer_user_id: str = ''
    nickname: str = Field(default='', max_length=100)


class MessageCreate(BaseModel):
    account_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=2000)


def _call(function, *args, **kwargs):
    """已知业务错误返回给页面；未知异常由应用统一记录。"""
    try:
        return function(*args, **kwargs)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get('/accounts/{account_id}/status')
def account_status(account_id: str):
    return _call(message_inbox.status, account_id)


@router.post('/accounts/{account_id}/connect')
def connect_account(account_id: str):
    require_license()
    return _call(message_inbox.connect_account, account_id)


@router.post('/accounts/{account_id}/disconnect')
def disconnect_account(account_id: str):
    return _call(message_inbox.disconnect_account, account_id)


@router.post('/accounts/{account_id}/sync-auth')
async def sync_auth(account_id: str):
    require_license()
    if account_id in message_inbox.active_accounts():
        raise HTTPException(status_code=400, detail='请先断开该账号的私信连接，再同步认证')
    try:
        return await douyin_im_session.sync_account(account_id)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get('/conversations')
def conversations(background_tasks: BackgroundTasks, account_id: str = Query(min_length=1), query: str = ''):
    rows = _call(message_inbox.list_conversations, account_id, query)
    if any(not row['nickname'] or not row['avatar'] for row in rows):
        background_tasks.add_task(message_inbox.refresh_conversation_profiles, account_id)
    return rows


@router.post('/conversations')
def create_conversation(payload: ConversationCreate):
    return _call(message_inbox.create_conversation, payload.account_id, lead_id=payload.lead_id, peer_user_id=payload.peer_user_id, nickname=payload.nickname)


@router.get('/conversations/{conversation_id}/messages')
def messages(conversation_id: int, account_id: str = Query(min_length=1), before_id: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100)):
    return _call(message_inbox.list_messages, account_id, conversation_id, before_id=before_id, limit=limit)


@router.post('/conversations/{conversation_id}/read')
def mark_read(conversation_id: int, account_id: str = Query(min_length=1), up_to_id: int = Query(gt=0)):
    return _call(message_inbox.mark_read, account_id, conversation_id, up_to_id)


@router.post('/conversations/{conversation_id}/messages')
def send_message(conversation_id: int, payload: MessageCreate):
    require_license()
    return _call(message_inbox.send_message, payload.account_id, conversation_id, payload.text)
