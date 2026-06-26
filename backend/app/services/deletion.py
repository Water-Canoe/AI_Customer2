from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app import database


ENTITY_BY_LIBRARY = {
    "contents": ("content", "contents"),
    "comments": ("comment", "comments"),
    "competitor_candidates": ("account", "user_accounts"),
    "competitors": ("account", "user_accounts"),
    "lead_customers": ("lead", "lead_user_accounts"),
    "target_customers": ("lead", "lead_user_accounts"),
}

RAW_DELETE_ALLOWLIST = {
    "douyin_aweme",
    "douyin_aweme_comment",
    "dy_creator",
    "xhs_note",
    "xhs_note_comment",
    "xhs_creator",
    "kuaishou_video",
    "kuaishou_video_comment",
}


def delete_library_row(library: str, row_id: int, hard: bool | None = None) -> dict[str, Any]:
    if library not in ENTITY_BY_LIBRARY:
        raise HTTPException(status_code=404, detail="未知数据表")
    entity_type, table = ENTITY_BY_LIBRARY[library]
    if library == "target_customers" and hard is not True:
        return soft_hide_target(row_id)
    return hard_delete(entity_type, table, row_id)


def soft_hide_target(lead_id: int) -> dict[str, Any]:
    with database.connect() as conn:
        current = conn.execute("SELECT follow_status FROM lead_user_accounts WHERE id = ?", (lead_id,)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="目标客户不存在")
        conn.execute(
            "UPDATE lead_user_accounts SET hidden = 1, follow_status = '已移出', updated_at = datetime('now', 'localtime') WHERE id = ?",
            (lead_id,),
        )
        conn.execute(
            "INSERT INTO lead_status_events(lead_account_id, from_status, to_status, note) VALUES(?, ?, '已移出', '普通删除隐藏目标客户')",
            (lead_id, str(current["follow_status"])),
        )
        conn.execute(
            "INSERT INTO deletion_audit(entity_type, entity_id, hard_delete, detail) VALUES('lead', ?, 0, ?)",
            (lead_id, json.dumps({"action": "soft_hide"}, ensure_ascii=False)),
        )
    return {"ok": True, "mode": "soft_hide"}


def hard_delete(entity_type: str, table: str, entity_id: int) -> dict[str, Any]:
    with database.connect() as conn:
        refs = conn.execute(
            "SELECT * FROM raw_source_refs WHERE entity_type = ? AND entity_id = ?",
            (entity_type if entity_type != "lead" else "account", _lead_account_id(conn, entity_id) if entity_type == "lead" else entity_id),
        ).fetchall()
        if not refs:
            raise HTTPException(status_code=409, detail="缺少 MediaCrawler 底层映射，不能执行双库硬删除")
        raw_db = database.get_setting(conn, "media_crawler_db_path", str(database.DEFAULT_MEDIA_CRAWLER_DB)).strip().strip('"').strip("'")

    raw_path = Path(raw_db)
    if not raw_path.exists():
        raise HTTPException(status_code=409, detail=f"MediaCrawler SQLite 不存在：{raw_db}")

    raw_conn = sqlite3.connect(raw_path)
    try:
        for ref in refs:
            raw_table = str(ref["raw_table"])
            if raw_table not in RAW_DELETE_ALLOWLIST:
                raise HTTPException(status_code=409, detail=f"未允许删除的底层表：{raw_table}")
            raw_conn.execute(f"DELETE FROM {raw_table} WHERE rowid = ?", (str(ref["raw_pk"]),))
        raw_conn.commit()
    finally:
        raw_conn.close()

    with database.connect() as conn:
        _delete_project_entity(conn, entity_type, table, entity_id)
        conn.execute(
            "INSERT INTO deletion_audit(entity_type, entity_id, hard_delete, detail) VALUES(?, ?, 1, ?)",
            (entity_type, entity_id, json.dumps({"raw_refs": len(refs)}, ensure_ascii=False)),
        )
    return {"ok": True, "mode": "hard_delete", "raw_refs": len(refs)}


def _lead_account_id(conn, lead_id: int) -> int:
    row = conn.execute("SELECT account_id FROM lead_user_accounts WHERE id = ?", (lead_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="客户不存在")
    return int(row["account_id"])


def _delete_project_entity(conn, entity_type: str, table: str, entity_id: int) -> None:
    if entity_type == "lead":
        active_sources = conn.execute(
            "SELECT COUNT(*) AS c FROM lead_sources WHERE lead_account_id = ? AND active = 1",
            (entity_id,),
        ).fetchone()["c"]
        if int(active_sources) > 1:
            conn.execute("UPDATE lead_sources SET active = 0 WHERE lead_account_id = ? LIMIT 1", (entity_id,))
            conn.execute(
                "UPDATE lead_user_accounts SET follow_status = '已移出', updated_at = datetime('now', 'localtime') WHERE id = ?",
                (entity_id,),
            )
            return
        account_id = _lead_account_id(conn, entity_id)
        conn.execute("DELETE FROM lead_user_accounts WHERE id = ?", (entity_id,))
        conn.execute("DELETE FROM user_accounts WHERE id = ?", (account_id,))
        return
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (entity_id,))


def delete_task(task_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        task = conn.execute("SELECT * FROM crawl_jobs WHERE id = ?", (task_id,)).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        refs = conn.execute("SELECT * FROM raw_source_refs WHERE task_id = ?", (task_id,)).fetchall()
        raw_db = database.get_setting(conn, "media_crawler_db_path", str(database.DEFAULT_MEDIA_CRAWLER_DB)).strip().strip('"').strip("'")
        if not refs:
            raise HTTPException(status_code=409, detail="此任务没有底层映射，不能执行任务硬删除")

    raw_conn = sqlite3.connect(raw_db)
    try:
        for ref in refs:
            raw_table = str(ref["raw_table"])
            if raw_table not in RAW_DELETE_ALLOWLIST:
                raise HTTPException(status_code=409, detail=f"未允许删除的底层表：{raw_table}")
            raw_conn.execute(f"DELETE FROM {raw_table} WHERE rowid = ?", (str(ref["raw_pk"]),))
        raw_conn.commit()
    finally:
        raw_conn.close()

    with database.connect() as conn:
        conn.execute("DELETE FROM comments WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM contents WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM account_sources WHERE task_id = ?", (task_id,))
        conn.execute("UPDATE lead_sources SET active = 0 WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM crawl_jobs WHERE id = ?", (task_id,))
        conn.execute(
            "INSERT INTO deletion_audit(entity_type, entity_id, hard_delete, detail) VALUES('task', 0, 1, ?)",
            (json.dumps({"task_id": task_id, "raw_refs": len(refs)}, ensure_ascii=False),),
        )
    return {"ok": True, "mode": "task_hard_delete", "raw_refs": len(refs)}
