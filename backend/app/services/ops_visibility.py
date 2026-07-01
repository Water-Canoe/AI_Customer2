from __future__ import annotations

import json
import math
from typing import Any

from fastapi import HTTPException

from app import database
from app.services import crawler_adapter, diagnostics


def tombstone_summary() -> dict[str, Any]:
    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT entity_type, COUNT(*) AS count, MAX(updated_at) AS latest_at
            FROM deleted_identities
            GROUP BY entity_type
            ORDER BY entity_type
            """
        ).fetchall()
        source_rows = conn.execute(
            """
            SELECT source, COUNT(*) AS count, MAX(updated_at) AS latest_at
            FROM deleted_identities
            GROUP BY source
            ORDER BY count DESC, source
            LIMIT 10
            """
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS count FROM deleted_identities").fetchone()["count"]
    by_type = {str(row["entity_type"]): int(row["count"] or 0) for row in rows}
    return {
        "total": int(total or 0),
        "accounts": int(by_type.get("author_account", 0)),
        "contents": int(by_type.get("content", 0)),
        "comments": int(by_type.get("comment", 0)),
        "by_type": database.rows_to_dicts(rows),
        "by_source": database.rows_to_dicts(source_rows),
        "latest_at": max((str(row["latest_at"] or "") for row in rows), default=""),
    }


def list_tombstones(
    entity_type: str = "",
    platform: str = "",
    source: str = "",
    query: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 100))
    where: list[str] = []
    params: list[Any] = []
    if entity_type:
        where.append("entity_type = ?")
        params.append(entity_type)
    if platform:
        where.append("platform = ?")
        params.append(platform)
    if source:
        where.append("source = ?")
        params.append(source)
    if query:
        where.append("(identifier_value LIKE ? OR source LIKE ? OR snapshot LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like, like])
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    offset = (page - 1) * page_size
    with database.connect() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) AS count FROM deleted_identities {clause}", params).fetchone()["count"] or 0)
        rows = conn.execute(
            f"""
            SELECT *
            FROM deleted_identities
            {clause}
            ORDER BY datetime(updated_at) DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, page_size, offset),
        ).fetchall()
    items = [_format_tombstone(row) for row in database.rows_to_dicts(rows)]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, math.ceil(total / page_size)) if total else 1,
    }


def task_dedup_summary(task_id: str) -> dict[str, Any]:
    task = crawler_adapter.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    outcome = task.get("outcome") if isinstance(task.get("outcome"), dict) else {}
    counts = outcome.get("counts") if isinstance(outcome.get("counts"), dict) else {}
    like = f"%{task_id}%"
    with database.connect() as conn:
        audit_rows = conn.execute(
            """
            SELECT *
            FROM deletion_audit
            WHERE detail LIKE ?
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 20
            """,
            (like,),
        ).fetchall()
        tombstone_rows = conn.execute(
            """
            SELECT entity_type, COUNT(*) AS count, MAX(updated_at) AS latest_at
            FROM deleted_identities
            WHERE snapshot LIKE ?
            GROUP BY entity_type
            ORDER BY entity_type
            """,
            (like,),
        ).fetchall()
        raw_refs = conn.execute("SELECT COUNT(*) AS count FROM raw_source_refs WHERE task_id = ?", (task_id,)).fetchone()["count"]
    audit = [_format_audit(row) for row in database.rows_to_dicts(audit_rows)]
    tombstones = database.rows_to_dicts(tombstone_rows)
    return {
        "task_id": task_id,
        "status": task.get("status") or "",
        "imported_counts": dict(counts or {}),
        "raw_refs": int(raw_refs or 0),
        "tombstone_counts": tombstones,
        "audit": audit,
        "diagnostic": diagnostics.task_diagnostics(task_id),
        "notes": [
            "重复跳过数量只能从项目库墓碑、删除审计和当前入库状态推导，不等同于 MediaCrawler 底层去重总数。",
            "已删除内容和评论会通过墓碑阻止后续重复导入；已存在但未删除的内容仍可作为评论采集入口。",
        ],
    }


def _format_tombstone(row: dict[str, Any]) -> dict[str, Any]:
    snapshot = _json(row.get("snapshot"))
    item = dict(row)
    item["snapshot_summary"] = _snapshot_summary(snapshot)
    item["snapshot"] = snapshot
    return item


def _format_audit(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["detail"] = _json(row.get("detail"))
    return item


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _snapshot_summary(snapshot: dict[str, Any]) -> str:
    for key in ("nickname", "title", "body", "desc", "description", "content_id", "comment_id", "platform_user_id"):
        value = str(snapshot.get(key) or "").strip()
        if value:
            return value[:120]
    return "无快照摘要"
