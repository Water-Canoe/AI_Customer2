from fastapi import APIRouter, HTTPException, Query

from app.schemas import DraftKind, DraftUpdate
from app.services import drafts


router = APIRouter(prefix='/api/drafts', tags=['drafts'])


@router.get('/{kind}')
def list_drafts(kind: DraftKind, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), search: str = ''):
    return drafts.list_drafts(kind, page, page_size, search)


@router.get('/{kind}/{key}')
def get_draft(kind: DraftKind, key: str):
    return drafts.get_draft(kind, key)


@router.put('/{kind}/{key}')
def save_draft(kind: DraftKind, key: str, payload: DraftUpdate):
    try:
        return drafts.save_draft(kind, key, payload.payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete('/{kind}/{key}')
def delete_draft(kind: DraftKind, key: str):
    return drafts.delete_draft(kind, key)
