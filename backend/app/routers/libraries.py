"""业务数据表的查询、编辑与删除入口。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app import views
from app.schemas import TableUpdate
from app.services import deletion


router = APIRouter(prefix="/api", tags=["tables"])


@router.get("/tables/{library}")
def list_table(
    library: str,
    status: str = Query(default=""),
    keyword: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    try:
        return views.list_library(library, status=status, keyword=keyword, page=page, page_size=page_size)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="未知数据表") from exc


@router.patch("/tables/{library}/{row_id}")
def update_table_row(library: str, row_id: int, payload: TableUpdate) -> dict[str, object]:
    return views.update_library_row(library, row_id, payload.values)


@router.delete("/tables/{library}/{row_id}")
def delete_table_row(
    library: str,
    row_id: int,
    hard: bool | None = Query(default=None),
) -> dict[str, object]:
    return deletion.delete_library_row(library, row_id, hard)
