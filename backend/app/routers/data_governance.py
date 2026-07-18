"""删除墓碑与批量操作预览等数据治理入口。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.schemas import BulkActionPreview
from app.services import bulk_actions, ops_visibility


router = APIRouter(prefix="/api", tags=["data-governance"])


@router.get("/tombstones/summary")
def get_tombstone_summary() -> dict[str, object]:
    return ops_visibility.tombstone_summary()


@router.get("/tombstones")
def get_tombstones(
    entity_type: str = Query(default=""),
    platform: str = Query(default=""),
    source: str = Query(default=""),
    query: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    return ops_visibility.list_tombstones(
        entity_type=entity_type,
        platform=platform,
        source=source,
        query=query,
        page=page,
        page_size=page_size,
    )


@router.post("/bulk-actions/preview")
def bulk_action_preview(payload: BulkActionPreview) -> dict[str, object]:
    return bulk_actions.preview_bulk_action(payload)
