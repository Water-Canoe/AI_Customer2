from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from fastapi import HTTPException

from app import database


def create_ai_job(target_type: str, target_id: int, run_now: bool = True) -> dict[str, Any]:
    job_id = f"{target_type}-{target_id}-{int(time.time() * 1000)}"
    with database.connect() as conn:
        payload = build_input_payload(conn, target_type, target_id)
        conn.execute(
            """
            INSERT INTO analysis_jobs(id, target_type, target_id, status, input_payload)
            VALUES(?, ?, ?, 'pending', ?)
            """,
            (job_id, target_type, target_id, json.dumps(payload, ensure_ascii=False)),
        )
    if run_now:
        return run_ai_job(job_id)
    return get_ai_job(job_id)


def create_batch_jobs(target_type: str, target_ids: list[int], run_now: bool = True) -> list[dict[str, Any]]:
    return [create_ai_job(target_type, target_id, run_now) for target_id in target_ids]


def list_ai_jobs() -> list[dict[str, Any]]:
    with database.connect() as conn:
        rows = conn.execute("SELECT * FROM analysis_jobs ORDER BY created_at DESC").fetchall()
        return database.rows_to_dicts(rows)


def get_ai_job(job_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="AI任务不存在")
        return database.row_to_dict(row) or {}


def retry_ai_job(job_id: str) -> dict[str, Any]:
    return run_ai_job(job_id)


def run_ai_job(job_id: str) -> dict[str, Any]:
    # 配置缺失也要先落库为 failed，再返回明确错误，便于页面重试。
    missing_config_error = ""
    with database.connect() as conn:
        job = conn.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="AI任务不存在")
        target_type = str(job["target_type"])
        target_id = int(job["target_id"])
        payload = build_input_payload(conn, target_type, target_id)
        base_url = database.get_setting(conn, "ai_base_url")
        api_key = database.get_setting(conn, "ai_api_key")
        model = database.get_setting(conn, "ai_model")
        if not base_url or not api_key or not model:
            missing_config_error = "请先配置 AI Base URL、API Key 和模型名"
            _mark_failed(conn, job_id, "请先在设置中配置 OpenAI 兼容 Base URL、API Key 和模型名")
        else:
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'running', error = '', input_payload = ?, updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (json.dumps(payload, ensure_ascii=False), job_id),
            )

    if missing_config_error:
        raise HTTPException(status_code=400, detail=missing_config_error)

    try:
        output = call_openai_compatible(base_url, api_key, model, target_type, payload)
        parsed = parse_json_from_text(output)
    except HTTPException:
        raise
    except Exception as exc:
        with database.connect() as conn:
            _mark_failed(conn, job_id, f"AI分析失败：{exc}")
        raise HTTPException(status_code=502, detail=f"AI分析失败：{exc}") from exc

    with database.connect() as conn:
        apply_ai_result(conn, target_type, target_id, parsed)
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status = 'succeeded', output_payload = ?, updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (json.dumps(parsed, ensure_ascii=False), job_id),
        )
    return get_ai_job(job_id)


def build_input_payload(conn, target_type: str, target_id: int) -> dict[str, Any]:
    icp = json.loads(database.get_setting(conn, "icp_profile", "{}") or "{}")
    if target_type == "competitor":
        account = conn.execute("SELECT * FROM user_accounts WHERE id = ?", (target_id,)).fetchone()
        if not account:
            raise HTTPException(status_code=404, detail="竞品账号不存在")
        contents = conn.execute(
            """
            SELECT title, description, content_url, like_count, comment_count
            FROM contents
            WHERE author_account_id = ?
            ORDER BY updated_at DESC
            LIMIT 10
            """,
            (target_id,),
        ).fetchall()
        return {"icp": icp, "account": database.row_to_dict(account), "contents": database.rows_to_dicts(contents)}
    if target_type == "lead":
        lead = conn.execute(
            """
            SELECT lua.*, ua.platform, ua.nickname, ua.profile_url, ua.signature
            FROM lead_user_accounts lua
            JOIN user_accounts ua ON ua.id = lua.account_id
            WHERE lua.id = ?
            """,
            (target_id,),
        ).fetchone()
        if not lead:
            raise HTTPException(status_code=404, detail="线索客户不存在")
        evidence = conn.execute(
            """
            SELECT c.body AS comment_body, ct.title, ct.description, ct.content_url, src.source_type
            FROM lead_sources src
            LEFT JOIN comments c ON c.id = src.comment_id
            LEFT JOIN contents ct ON ct.id = src.content_id
            WHERE src.lead_account_id = ? AND src.active = 1
            ORDER BY src.created_at DESC
            LIMIT 20
            """,
            (target_id,),
        ).fetchall()
        return {"icp": icp, "lead": database.row_to_dict(lead), "evidence": database.rows_to_dicts(evidence)}
    if target_type == "content":
        content = conn.execute("SELECT * FROM contents WHERE id = ?", (target_id,)).fetchone()
        if not content:
            raise HTTPException(status_code=404, detail="内容不存在")
        return {"icp": icp, "content": database.row_to_dict(content)}
    raise HTTPException(status_code=400, detail="不支持的 AI 分析类型")


def call_openai_compatible(base_url: str, api_key: str, model: str, target_type: str, payload: dict[str, Any]) -> str:
    endpoint = base_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = f"{endpoint}/chat/completions"
    system_prompt = (
        "你是一个谨慎的AI拓客分析助手。必须只输出JSON，不要输出解释文字。"
        "判断时结合ICP画像，宁可保守，也不要把证据不足的对象判为目标。"
    )
    if target_type == "competitor":
        user_prompt = (
            "判断账号是否为竞品账号。输出格式："
            '{"is_competitor": true, "reason": "..."}。\n'
            f"输入：{json.dumps(payload, ensure_ascii=False)}"
        )
    else:
        user_prompt = (
            "判断是否为目标客户。输出格式："
            '{"is_customer": true, "intention": "低/中/高", "reason": "...", '
            '"pain_points": ["..."], "suggested_action": "...", "script": "..."}。\n'
            f"输入：{json.dumps(payload, ensure_ascii=False)}"
        )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AI接口返回 HTTP {exc.code}: {detail}") from exc
    data = json.loads(raw)
    return data["choices"][0]["message"]["content"]


def parse_json_from_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    match = re.search(r"\{.*\}", stripped, re.S)
    if not match:
        raise ValueError("AI输出中没有找到JSON对象")
    return json.loads(match.group(0))


def apply_ai_result(conn, target_type: str, target_id: int, result: dict[str, Any]) -> None:
    if target_type == "competitor":
        is_competitor = bool(result.get("is_competitor"))
        status = "竞品" if is_competitor else "非竞品"
        role = "competitor" if is_competitor else "competitor_candidate"
        conn.execute(
            "UPDATE user_accounts SET competitor_status = ?, account_role = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (status, role, target_id),
        )
        return
    if target_type in ("lead", "content"):
        is_customer = bool(result.get("is_customer"))
        lead_id = target_id
        if target_type == "content":
            content = conn.execute("SELECT author_account_id FROM contents WHERE id = ?", (target_id,)).fetchone()
            if not content or not content["author_account_id"]:
                raise ValueError("内容缺少作者账号，无法转为目标客户")
            conn.execute("INSERT OR IGNORE INTO lead_user_accounts(account_id) VALUES(?)", (int(content["author_account_id"]),))
            lead_id = int(conn.execute("SELECT id FROM lead_user_accounts WHERE account_id = ?", (int(content["author_account_id"]),)).fetchone()["id"])
        current = conn.execute("SELECT follow_status FROM lead_user_accounts WHERE id = ?", (lead_id,)).fetchone()
        old_status = str(current["follow_status"]) if current else ""
        new_status = "未私信" if is_customer else "无需跟进"
        conn.execute(
            """
            UPDATE lead_user_accounts
            SET screening_status = ?, follow_status = ?, intention = ?, reason = ?,
                pain_points = ?, suggested_action = ?, script = ?, hidden = 0,
                updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (
                "目标客户" if is_customer else "非客户",
                new_status,
                str(result.get("intention", "")),
                str(result.get("reason", "")),
                json.dumps(result.get("pain_points", []), ensure_ascii=False),
                str(result.get("suggested_action", "")),
                str(result.get("script", "")),
                lead_id,
            ),
        )
        conn.execute(
            "INSERT INTO lead_status_events(lead_account_id, from_status, to_status, note) VALUES(?, ?, ?, ?)",
            (lead_id, old_status, new_status, "AI分析结果"),
        )


def _mark_failed(conn, job_id: str, error: str) -> None:
    conn.execute(
        """
        UPDATE analysis_jobs
        SET status = 'failed', error = ?, updated_at = datetime('now', 'localtime')
        WHERE id = ?
        """,
        (error, job_id),
    )
