from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from fastapi import HTTPException

from app import database
from app.services import deletion


PROMPT_VERSIONS = {
    "competitor": "competitor_v1",
    "lead": "lead_v2",
    "content": "content_v2",
}

DEFAULT_ICP_PROFILE = {
    "product": "",
    "company_name": "",
    "industry": "",
    "roles": "",
    "pain_points": "",
    "high_intent_words": "",
    "value_proposition": "",
    "excluded_audience": "",
}


def create_ai_job(
    target_type: str,
    target_id: int,
    run_now: bool = True,
    *,
    auto_delete: bool | None = None,
) -> dict[str, Any]:
    job_id = f"{target_type}-{target_id}-{int(time.time() * 1000)}"
    with database.connect() as conn:
        payload = build_input_payload(conn, target_type, target_id)
        conn.execute(
            """
            INSERT INTO analysis_jobs(id, target_type, target_id, status, input_payload, auto_delete)
            VALUES(?, ?, ?, 'pending', ?, ?)
            """,
            (job_id, target_type, target_id, json.dumps(payload, ensure_ascii=False), -1 if auto_delete is None else int(auto_delete)),
        )
    if run_now:
        return run_ai_job(job_id)
    return get_ai_job(job_id)


def create_batch_jobs(target_type: str, target_ids: list[int], run_now: bool = True) -> list[dict[str, Any]]:
    jobs = [create_ai_job(target_type, target_id, run_now=False) for target_id in target_ids]
    if run_now and jobs:
        run_ai_jobs_parallel([str(job["id"]) for job in jobs])
        return [get_ai_job(str(job["id"])) for job in jobs]
    return jobs


def ai_analysis_concurrency() -> int:
    with database.connect() as conn:
        value = database.get_setting(conn, "ai_analysis_concurrency", "3")
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = 3
    return max(1, min(count, 10))


def run_ai_jobs_parallel(job_ids: list[str], max_workers: int | None = None) -> dict[str, Any]:
    unique_ids = list(dict.fromkeys(str(job_id) for job_id in job_ids if str(job_id).strip()))
    worker_count = max_workers or ai_analysis_concurrency()
    if not unique_ids:
        return {"total": 0, "succeeded": 0, "failed": 0, "errors": []}

    def run_one(job_id: str) -> dict[str, Any]:
        # 网络中断或模型偶发输出非 JSON 时，仅重试当前失败项，避免重跑整个批次。
        for attempt in range(2):
            try:
                job = run_ai_job(job_id)
                return {"job_id": job_id, "ok": True, "job": job}
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, ensure_ascii=False)
                if exc.status_code < 500 or attempt == 1:
                    return {"job_id": job_id, "ok": False, "reason": detail}
            except Exception as exc:
                if attempt == 1:
                    return {"job_id": job_id, "ok": False, "reason": str(exc)}
            time.sleep(0.5)
        raise RuntimeError("AI 任务重试状态异常")

    results: list[dict[str, Any]] = []
    if worker_count == 1 or len(unique_ids) == 1:
        results = [run_one(job_id) for job_id in unique_ids]
    else:
        with ThreadPoolExecutor(max_workers=min(worker_count, len(unique_ids))) as executor:
            future_map = {executor.submit(run_one, job_id): job_id for job_id in unique_ids}
            for future in as_completed(future_map):
                results.append(future.result())
    errors = [{"job_id": item["job_id"], "reason": item.get("reason", "")} for item in results if not item.get("ok")]
    auto_deleted = sum(1 for item in results if item.get("ok") and item.get("job", {}).get("auto_delete_result"))
    return {
        "total": len(unique_ids),
        "succeeded": len(unique_ids) - len(errors),
        "failed": len(errors),
        "auto_deleted": auto_deleted,
        "errors": errors,
    }


def run_auto_lead_analysis_for_task(task_id: str) -> dict[str, Any]:
    prepared = prepare_auto_lead_analysis_jobs(task_id)
    job_ids = [str(value) for value in prepared["job_ids"]]
    errors = list(prepared["errors"])
    summary = run_ai_jobs_parallel(job_ids) if job_ids else {"succeeded": 0, "failed": 0, "auto_deleted": 0, "errors": []}
    return {
        "task_id": task_id,
        "target_count": prepared["target_count"],
        "succeeded": int(summary.get("succeeded", 0)),
        "failed": len(errors) + int(summary.get("failed", 0)),
        "auto_deleted": int(summary.get("auto_deleted", 0)),
        "errors": errors + [{"lead_id": str(item.get("job_id", "")), "reason": str(item.get("reason", ""))} for item in summary.get("errors", [])],
    }


def prepare_auto_lead_analysis_jobs(task_id: str, *, auto_delete: bool | None = None) -> dict[str, Any]:
    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT lua.id
            FROM lead_sources ls
            JOIN lead_user_accounts lua ON lua.id = ls.lead_account_id
            WHERE ls.task_id = ?
              AND ls.active = 1
              AND lua.hidden = 0
              AND lua.follow_status = '待筛选'
            ORDER BY lua.updated_at DESC, lua.id DESC
            """,
            (task_id,),
        ).fetchall()
        lead_ids = [int(row["id"]) for row in rows]

    job_ids: list[str] = []
    errors: list[dict[str, str]] = []
    for lead_id in lead_ids:
        try:
            job = _find_or_create_lead_job(lead_id, auto_delete=auto_delete)
            job_ids.append(str(job["id"]))
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, ensure_ascii=False)
            errors.append({"lead_id": str(lead_id), "reason": detail})
        except Exception as exc:
            errors.append({"lead_id": str(lead_id), "reason": str(exc)})
    return {
        "task_id": task_id,
        "target_count": len(lead_ids),
        "job_ids": job_ids,
        "errors": errors,
    }


def _find_or_create_lead_job(lead_id: int, *, auto_delete: bool | None = None) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM analysis_jobs
            WHERE target_type = 'lead'
              AND target_id = ?
              AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (lead_id,),
        ).fetchone()
    if row:
        if auto_delete is not None:
            with database.connect() as conn:
                conn.execute("UPDATE analysis_jobs SET auto_delete = ? WHERE id = ?", (int(auto_delete), row["id"]))
        return get_ai_job(str(row["id"]))
    if auto_delete is None:
        return create_ai_job("lead", lead_id, run_now=False)
    return create_ai_job("lead", lead_id, run_now=False, auto_delete=auto_delete)


def list_ai_jobs() -> list[dict[str, Any]]:
    with database.connect() as conn:
        rows = conn.execute("SELECT * FROM analysis_jobs ORDER BY created_at DESC").fetchall()
        return database.rows_to_dicts(rows)


def ai_workbench(
    tab: str = "competitors",
    keyword: str = "",
    status: str = "",
    result: str = "",
    page: int = 1,
    page_size: int = 10,
) -> dict[str, Any]:
    if tab not in {"competitors", "leads", "failed", "history"}:
        raise ValueError("未知 AI 工作台标签")
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 10)))
    with database.connect() as conn:
        # 查询后只返回当前页；筛选仍逐行复用 Python 规则，避免 Unicode 和结果判断变化。
        if tab == "competitors":
            query = _workbench_competitors_query()
        elif tab == "leads":
            query = _workbench_leads_query()
        else:
            query = _workbench_history_query(tab)
        total, items = _workbench_page(conn, query, tab, keyword, status, result, page, page_size)
        rows = [_workbench_item(item, tab) for item in items]
    return {
        "summary": _ai_workbench_summary(),
        "tab": tab,
        "items": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


def _matches_workbench_item(item: dict[str, Any], tab: str, keyword: str, status: str, result: str) -> bool:
    status_value = item.get("error_category") if tab == "failed" else item.get("status") if tab == "history" else item.get("analysis_status")
    result_value = "" if tab in {"failed", "history"} else item.get("result_label")
    if status and status_value != status:
        return False
    if result and result_value != result:
        return False
    keyword = str(keyword or "").strip().lower()
    if not keyword:
        return True
    fields = (
        "id", "job_id", "nickname", "target_name", "signature", "comment_samples",
        "content_samples", "source_keywords", "source_account_names", "result_reason",
        "reason", "error", "output_summary",
    )
    return any(keyword in str(item.get(field) or "").lower() for field in fields)


def _ai_workbench_summary() -> dict[str, int]:
    with database.connect() as conn:
        competitor_pending = int(
            conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM user_accounts ua
                WHERE ua.competitor_status NOT IN ('竞品', '非竞品')
                  AND (
                    ua.account_role IN ('competitor_candidate', 'competitor')
                    OR EXISTS (SELECT 1 FROM account_sources src WHERE src.account_id = ua.id AND src.active = 1)
                    OR EXISTS (SELECT 1 FROM contents c WHERE c.author_account_id = ua.id AND COALESCE(c.source_keyword, '') <> '')
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM analysis_jobs aj
                    WHERE aj.target_type = 'competitor' AND aj.target_id = ua.id
                      AND aj.status IN ('pending', 'running', 'failed')
                      AND aj.updated_at = (
                        SELECT MAX(latest.updated_at) FROM analysis_jobs latest
                        WHERE latest.target_type = 'competitor' AND latest.target_id = ua.id
                      )
                  )
                """
            ).fetchone()["count"]
        )
        lead_pending = int(
            conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM lead_user_accounts lua
                WHERE lua.hidden = 0
                  AND COALESCE(lua.reason, '') = ''
                  AND COALESCE(lua.script, '') = ''
                  AND lua.screening_status NOT IN ('目标客户', '非客户')
                  AND NOT EXISTS (
                    SELECT 1 FROM analysis_jobs aj
                    WHERE aj.target_type = 'lead' AND aj.target_id = lua.id
                      AND aj.status IN ('pending', 'running', 'failed')
                      AND aj.updated_at = (
                        SELECT MAX(latest.updated_at) FROM analysis_jobs latest
                        WHERE latest.target_type = 'lead' AND latest.target_id = lua.id
                      )
                  )
                """
            ).fetchone()["count"]
        )
        job_counts = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status = 'succeeded' AND date(updated_at) = date('now', 'localtime') THEN 1 ELSE 0 END) AS succeeded_today
            FROM analysis_jobs
            """
        ).fetchone()
    return {
        "competitor_pending": competitor_pending,
        "lead_pending": lead_pending,
        "running": int(job_counts["running"] or 0),
        "failed": int(job_counts["failed"] or 0),
        "succeeded_today": int(job_counts["succeeded_today"] or 0),
        "concurrency": ai_analysis_concurrency(),
    }


def delete_workbench_non_competitors(target_ids: list[int]) -> dict[str, Any]:
    account_ids = _unique_positive_ids(target_ids)
    deleted = 0
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    for account_id in account_ids:
        with database.connect() as conn:
            row = conn.execute(
                "SELECT id, nickname, competitor_status FROM user_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
        if not row:
            skipped.append({"target_id": account_id, "reason": "账号不存在或已删除"})
            continue
        if str(row["competitor_status"] or "") != "非竞品":
            skipped.append({"target_id": account_id, "name": row["nickname"], "reason": "账号不是非竞品"})
            continue
        try:
            result = deletion.delete_overview_account(account_id)
            deleted += 1
            details.append({"target_id": account_id, "name": row["nickname"], "result": result})
        except Exception as exc:
            reason = exc.detail if isinstance(exc, HTTPException) else str(exc)
            failed.append({"target_id": account_id, "name": row["nickname"], "reason": reason})
    return {
        "ok": True,
        "mode": "ai_workbench_non_competitors_delete",
        "requested": len(account_ids),
        "deleted": deleted,
        "skipped": skipped,
        "failed": failed,
        "details": details,
    }


def delete_workbench_non_customers(target_ids: list[int]) -> dict[str, Any]:
    lead_ids = _unique_positive_ids(target_ids)
    deleted = 0
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    for lead_id in lead_ids:
        with database.connect() as conn:
            row = conn.execute(
                """
                SELECT lua.id, ua.nickname, lua.screening_status, lua.follow_status
                FROM lead_user_accounts lua
                JOIN user_accounts ua ON ua.id = lua.account_id
                WHERE lua.id = ? AND lua.hidden = 0
                """,
                (lead_id,),
            ).fetchone()
        if not row:
            skipped.append({"target_id": lead_id, "reason": "客户线索不存在或已删除"})
            continue
        if str(row["screening_status"] or "") != "非客户" and str(row["follow_status"] or "") not in ("非客户", "无需跟进"):
            skipped.append({"target_id": lead_id, "name": row["nickname"], "reason": "客户线索不是非客户"})
            continue
        try:
            result = deletion.delete_lead_customer(lead_id, source="ai_workbench_non_customer_delete")
            deleted += 1
            details.append({"target_id": lead_id, "name": row["nickname"], "result": result})
        except Exception as exc:
            reason = exc.detail if isinstance(exc, HTTPException) else str(exc)
            failed.append({"target_id": lead_id, "name": row["nickname"], "reason": reason})
    return {
        "ok": True,
        "mode": "ai_workbench_non_customers_delete",
        "requested": len(lead_ids),
        "deleted": deleted,
        "skipped": skipped,
        "failed": failed,
        "details": details,
    }


def _unique_positive_ids(values: list[int]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item <= 0 or item in seen:
            continue
        result.append(item)
        seen.add(item)
    return result


_WORKBENCH_COMPETITOR_WHERE = """
    ua.account_role IN ('competitor_candidate', 'competitor')
    OR EXISTS (SELECT 1 FROM account_sources src WHERE src.account_id = ua.id AND src.active = 1)
    OR EXISTS (
        SELECT 1 FROM contents c
        WHERE c.author_account_id = ua.id AND COALESCE(c.source_keyword, '') <> ''
    )
"""


def _workbench_competitors_query() -> str:
    return f"""
        SELECT ua.*,
               aj.id AS job_id, aj.status AS job_status, aj.error AS job_error,
               aj.updated_at AS job_updated_at, aj.output_payload AS job_output_payload,
               (
                   SELECT COUNT(*)
                   FROM contents c
                   WHERE c.author_account_id = ua.id
               ) AS content_count,
               (
                   SELECT COUNT(*)
                   FROM comments cm
                   JOIN contents c ON c.id = cm.content_id
                   WHERE c.author_account_id = ua.id
               ) AS comment_count,
               (
                   SELECT GROUP_CONCAT(DISTINCT keyword)
                   FROM (
                       SELECT NULLIF(account_sources.keyword, '') AS keyword
                       FROM account_sources
                       WHERE account_sources.account_id = ua.id AND account_sources.active = 1
                       UNION
                       SELECT NULLIF(contents.source_keyword, '') AS keyword
                       FROM contents
                       WHERE contents.author_account_id = ua.id
                   )
                   WHERE keyword IS NOT NULL
               ) AS source_keywords,
               (
                   SELECT MAX(updated_at)
                   FROM contents c
                   WHERE c.author_account_id = ua.id
               ) AS latest_content_at
        FROM user_accounts ua
        LEFT JOIN analysis_jobs aj ON aj.id = (
            SELECT latest.id FROM analysis_jobs latest
            WHERE latest.target_type = 'competitor' AND latest.target_id = ua.id
            ORDER BY datetime(latest.updated_at) DESC, datetime(latest.created_at) DESC
            LIMIT 1
        )
        WHERE {_WORKBENCH_COMPETITOR_WHERE}
        """


def _workbench_leads_query() -> str:
    return """
        SELECT lua.id,
               lua.account_id,
               lua.screening_status,
               lua.follow_status,
               lua.intention,
               lua.reason,
               lua.suggested_action,
               lua.script,
               lua.hidden,
               lua.created_at,
               lua.updated_at,
               ua.platform,
               ua.platform_user_id,
               ua.nickname,
               ua.signature,
               ua.profile_url,
               aj.id AS job_id, aj.status AS job_status, aj.error AS job_error,
               aj.updated_at AS job_updated_at, aj.output_payload AS job_output_payload,
               COUNT(DISTINCT ls.id) AS source_count,
               GROUP_CONCAT(DISTINCT c.body) AS comment_samples,
               GROUP_CONCAT(DISTINCT COALESCE(NULLIF(ct.title, ''), NULLIF(ct.description, ''))) AS content_samples,
               GROUP_CONCAT(DISTINCT source_ua.nickname) AS source_account_names,
               MAX(COALESCE(c.updated_at, ct.updated_at, ls.created_at, lua.updated_at)) AS latest_evidence_at
        FROM lead_user_accounts lua
        JOIN user_accounts ua ON ua.id = lua.account_id
        -- 先关联单条最新分析，再展开来源，避免每条证据重复查询分析历史。
        LEFT JOIN analysis_jobs aj ON aj.id = (
            SELECT latest.id FROM analysis_jobs latest
            WHERE latest.target_type = 'lead' AND latest.target_id = lua.id
            ORDER BY datetime(latest.updated_at) DESC, datetime(latest.created_at) DESC
            LIMIT 1
        )
        LEFT JOIN lead_sources ls ON ls.lead_account_id = lua.id AND ls.active = 1
        LEFT JOIN comments c ON c.id = ls.comment_id
        LEFT JOIN contents ct ON ct.id = ls.content_id
        LEFT JOIN user_accounts source_ua ON source_ua.id = COALESCE(ls.source_account_id, ct.author_account_id)
        WHERE lua.hidden = 0
        GROUP BY lua.id
        """


def _workbench_history_query(tab: str) -> str:
    status_clause = "aj.status = 'failed'" if tab == "failed" else "aj.status IN ('succeeded', 'failed')"
    return f"""
        SELECT aj.*,
               CASE aj.target_type
                   WHEN 'competitor' THEN COALESCE(NULLIF(ua.nickname, ''), '竞品账号 #' || aj.target_id)
                   WHEN 'lead' THEN COALESCE(NULLIF(lead_ua.nickname, ''), '客户线索 #' || aj.target_id)
                   WHEN 'content' THEN COALESCE(NULLIF(ct.title, ''), '内容 #' || aj.target_id)
                   ELSE aj.target_type || ' #' || aj.target_id
               END AS target_name,
               CASE aj.target_type
                   WHEN 'competitor' THEN CASE WHEN ua.id IS NULL THEN '对象已删除'
                       ELSE COALESCE(NULLIF(ua.signature, ''), ua.competitor_status, '') END
                   WHEN 'lead' THEN CASE WHEN lead_ua.id IS NULL THEN '对象已删除'
                       ELSE COALESCE(NULLIF((
                           SELECT GROUP_CONCAT(c.body)
                           FROM lead_sources ls JOIN comments c ON c.id = ls.comment_id
                           WHERE ls.lead_account_id = lua.id AND ls.active = 1
                       ), ''), NULLIF(lead_ua.signature, ''), lua.follow_status, '') END
                   WHEN 'content' THEN CASE WHEN ct.id IS NULL THEN '对象已删除' ELSE ct.description END
                   ELSE ''
               END AS target_summary,
               COALESCE(ua.profile_url, lead_ua.profile_url, ct.content_url, '') AS target_url,
               COALESCE(ua.platform, lead_ua.platform, ct.platform, '') AS platform,
               COALESCE(ua.id, lead_ua.id, ct.id) IS NOT NULL AS target_has_platform
        FROM analysis_jobs aj
        LEFT JOIN user_accounts ua ON aj.target_type = 'competitor' AND ua.id = aj.target_id
        LEFT JOIN lead_user_accounts lua ON aj.target_type = 'lead' AND lua.id = aj.target_id
        LEFT JOIN user_accounts lead_ua ON lead_ua.id = lua.account_id
        LEFT JOIN contents ct ON aj.target_type = 'content' AND ct.id = aj.target_id
        WHERE {status_clause}
    """


def _workbench_item(row: dict[str, Any], tab: str) -> dict[str, Any]:
    """复用展示和筛选规则；数据库标量函数仅调用此纯数据转换，不额外查询。"""
    item = dict(row)
    if tab in {"competitors", "leads"}:
        target_type = "competitor" if tab == "competitors" else "lead"
        job = {"status": item.get("job_status") or ""}
        output = _json_payload(item.pop("job_output_payload", ""))
        item.update({
            "target_type": target_type,
            "target_id": int(item["id"]),
            "analysis_status": _competitor_analysis_status(item, job) if tab == "competitors" else _lead_analysis_status(item, job),
            "result_label": _competitor_result_label(item, output) if tab == "competitors" else _lead_result_label(item, output),
            "result_reason": str(output.get("reason") or item.get("competitor_reason" if tab == "competitors" else "reason") or ""),
        })
        for key in ("job_id", "job_status", "job_error", "job_updated_at"):
            item[key] = item.get(key) or ""
        if tab == "competitors":
            item["source_keywords"] = item.get("source_keywords") or ""
        else:
            item["result_script"] = str(output.get("script") or item.get("script") or "")
    else:
        item["error_category"] = _classify_ai_error(str(item.get("error") or ""))
        item["input_payload"] = _json_payload(item.get("input_payload"))
        item["output_payload"] = _json_payload(item.get("output_payload"))
        item["output_summary"] = _output_summary(item["output_payload"])
        for key in ("prompt_version", "system_prompt", "user_prompt", "model", "base_url", "raw_output"):
            item[key] = str(item.get(key) or "")
        if not item.pop("target_has_platform", True):
            item.pop("platform", None)
    return item


def _workbench_page(
    conn: database.sqlite3.Connection, query: str, tab: str,
    keyword: str, status: str, result: str, page: int, page_size: int,
) -> tuple[int, list[dict[str, Any]]]:
    order = "datetime(updated_at) DESC, id DESC" if tab in {"competitors", "leads"} else "datetime(updated_at) DESC, datetime(created_at) DESC"
    if any((keyword, status, result)):
        # Python 的 Unicode 小写、JSON 布尔值和错误分类保持与页面原有规则一致。
        fields = ("id", "nickname", "signature", "job_id", "job_status", "job_output_payload")
        if tab == "competitors":
            fields += ("competitor_status", "competitor_reason", "source_keywords")
        elif tab == "leads":
            fields += ("screening_status", "reason", "script", "comment_samples", "content_samples", "source_account_names")
        else:
            fields = ("id", "target_name", "error", "status", "output_payload")
        filter_json = ", ".join(f"'{field}', {field}" for field in fields)
        conn.create_function(
            "workbench_matches", 1,
            lambda payload: int(_matches_workbench_item(_workbench_item(json.loads(payload), tab), tab, keyword, status, result)),
            deterministic=True,
        )
        where = f"workbench_matches(json_object({filter_json}))"
        # 客户证据聚合先完成再筛选，防止 SQLite 把同一次匹配同时下推到 WHERE/HAVING。
        filter_query = f"{query} LIMIT -1 OFFSET 0" if tab == "leads" else query
        # 筛选只执行一次；计数左联当前页，保证无结果或超出末页时也返回正确总数。
        rows = database.rows_to_dicts(conn.execute(
            f"""
            WITH matched AS MATERIALIZED (SELECT * FROM ({filter_query}) WHERE {where})
            SELECT current_page.*, counts._total
            FROM (SELECT COUNT(*) AS _total FROM matched) counts
            LEFT JOIN (SELECT * FROM matched ORDER BY {order} LIMIT ? OFFSET ?) current_page ON 1
            ORDER BY {order}
            """,
            (page_size, (page - 1) * page_size),
        ).fetchall())
        total = int(rows[0]["_total"])
        for row in rows:
            row.pop("_total")
        return total, [row for row in rows if row["id"] is not None]
    if tab == "competitors":
        count_query = f"SELECT COUNT(*) FROM user_accounts ua WHERE {_WORKBENCH_COMPETITOR_WHERE}"
    elif tab == "leads":
        # 未筛选时直接数客户，避免为总数重复聚合全部来源证据。
        count_query = "SELECT COUNT(*) FROM lead_user_accounts WHERE hidden = 0"
    else:
        statuses = "status = 'failed'" if tab == "failed" else "status IN ('succeeded', 'failed')"
        count_query = f"SELECT COUNT(*) FROM analysis_jobs WHERE {statuses}"
    total = int(conn.execute(count_query).fetchone()[0])
    rows = conn.execute(
        f"SELECT * FROM ({query}) ORDER BY {order} LIMIT ? OFFSET ?",
        (page_size, (page - 1) * page_size),
    ).fetchall()
    return total, database.rows_to_dicts(rows)


def _competitor_analysis_status(item: dict[str, Any], job: dict[str, Any] | None) -> str:
    status = str(job.get("status") or "") if job else ""
    if status == "pending":
        return "排队分析"
    if status == "running":
        return "正在分析"
    if status == "failed":
        return "失败"
    if str(item.get("competitor_status") or "") in ("竞品", "非竞品"):
        return "已分析"
    return "未分析"


def _lead_analysis_status(item: dict[str, Any], job: dict[str, Any] | None) -> str:
    status = str(job.get("status") or "") if job else ""
    if status == "pending":
        return "排队分析"
    if status == "running":
        return "正在分析"
    if status == "failed":
        return "失败"
    if item.get("reason") or item.get("script") or str(item.get("screening_status") or "") in ("目标客户", "非客户"):
        return "已分析"
    return "未分析"


def _competitor_result_label(item: dict[str, Any], output: dict[str, Any]) -> str:
    if "is_competitor" in output:
        return "竞品" if bool(output.get("is_competitor")) else "非竞品"
    status = str(item.get("competitor_status") or "")
    return status if status in ("竞品", "非竞品") else "-"


def _lead_result_label(item: dict[str, Any], output: dict[str, Any]) -> str:
    if "is_customer" in output:
        return "目标客户" if bool(output.get("is_customer")) else "非客户"
    status = str(item.get("screening_status") or "")
    return status if status in ("目标客户", "非客户") else "-"


def _classify_ai_error(error: str) -> str:
    if not error:
        return "未知失败"
    if any(token in error for token in ("API Key", "Base URL", "模型名", "配置")):
        return "配置错误"
    if any(token.lower() in error.lower() for token in ("json", "输出", "parse", "decode")):
        return "JSON解析失败"
    if any(token.lower() in error.lower() for token in ("http", "timed out", "timeout", "urlopen", "network", "connection")):
        return "网络或接口错误"
    if any(token in error for token in ("不存在", "已删除", "not found")):
        return "对象已删除"
    return "其它失败"


def _json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _output_summary(payload: dict[str, Any]) -> str:
    if not payload:
        return ""
    if "is_competitor" in payload:
        label = "竞品" if bool(payload.get("is_competitor")) else "非竞品"
        return f"{label}：{payload.get('reason') or ''}"
    if "is_customer" in payload:
        label = "目标客户" if bool(payload.get("is_customer")) else "非客户"
        intention = str(payload.get("intention") or "")
        return f"{label}{f' / {intention}' if intention else ''}：{payload.get('reason') or ''}"
    return str(payload)[:200]


def get_ai_job(job_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="AI任务不存在")
        return database.row_to_dict(row) or {}


def retry_ai_job(job_id: str, run_now: bool = True) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT id FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="AI任务不存在")
        conn.execute(
            "UPDATE analysis_jobs SET status = 'pending', error = '', updated_at = datetime('now', 'localtime') WHERE id = ?",
            (job_id,),
        )
    return run_ai_job(job_id) if run_now else get_ai_job(job_id)


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
        system_prompt, user_prompt = build_ai_messages(target_type, payload)
        prompt_version = prompt_version_for(target_type)
        if not base_url or not api_key or not model:
            missing_config_error = "请先配置 AI Base URL、API Key 和模型名"
            conn.execute(
                """
                UPDATE analysis_jobs
                SET input_payload = ?, prompt_version = ?, system_prompt = ?, user_prompt = ?,
                    model = ?, base_url = ?, updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (
                    json.dumps(payload, ensure_ascii=False),
                    prompt_version,
                    system_prompt,
                    user_prompt,
                    model,
                    base_url,
                    job_id,
                ),
            )
            _mark_failed(conn, job_id, "请先在设置中配置 OpenAI 兼容 Base URL、API Key 和模型名")
        else:
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'running', error = '', input_payload = ?,
                    prompt_version = ?, system_prompt = ?, user_prompt = ?,
                    model = ?, base_url = ?, raw_output = '',
                    updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (
                    json.dumps(payload, ensure_ascii=False),
                    prompt_version,
                    system_prompt,
                    user_prompt,
                    model,
                    base_url,
                    job_id,
                ),
            )

    if missing_config_error:
        raise HTTPException(status_code=400, detail=missing_config_error)

    output = ""
    try:
        output = call_openai_compatible(base_url, api_key, model, target_type, payload, system_prompt, user_prompt)
        parsed = parse_json_from_text(output)
    except HTTPException:
        raise
    except Exception as exc:
        with database.connect() as conn:
            if output:
                conn.execute(
                    """
                    UPDATE analysis_jobs
                    SET raw_output = ?, updated_at = datetime('now', 'localtime')
                    WHERE id = ?
                    """,
                    (output, job_id),
                )
            _mark_failed(conn, job_id, f"AI分析失败：{exc}")
        raise HTTPException(status_code=502, detail=f"AI分析失败：{exc}") from exc

    with database.connect() as conn:
        apply_ai_result(conn, target_type, target_id, parsed)
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status = 'succeeded', output_payload = ?, raw_output = ?, updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (json.dumps(parsed, ensure_ascii=False), output, job_id),
        )
        job_result = database.row_to_dict(conn.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()) or {}

    auto_delete_result: dict[str, Any] | None = None
    auto_delete_error = ""
    auto_delete_override = int(job_result.get("auto_delete", -1))
    if _should_auto_delete_non_competitor(target_type, parsed, auto_delete_override):
        try:
            auto_delete_result = deletion.delete_overview_account(target_id)
        except Exception as exc:
            auto_delete_error = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
            with database.connect() as conn:
                conn.execute(
                    """
                    UPDATE analysis_jobs
                    SET error = ?, updated_at = datetime('now', 'localtime')
                    WHERE id = ?
                    """,
                    (f"自动删除非竞品失败：{auto_delete_error}", job_id),
                )
    if _should_auto_delete_non_customer(target_type, parsed, auto_delete_override):
        try:
            auto_delete_result = _delete_non_customer_lead_account(target_id)
        except Exception as exc:
            auto_delete_error = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
            with database.connect() as conn:
                conn.execute(
                    """
                    UPDATE analysis_jobs
                    SET error = ?, updated_at = datetime('now', 'localtime')
                    WHERE id = ?
                    """,
                    (f"自动删除非客户失败：{auto_delete_error}", job_id),
                )
    if auto_delete_result:
        job_result["auto_delete_result"] = auto_delete_result
    if auto_delete_error:
        job_result["auto_delete_error"] = auto_delete_error
    return job_result


def _should_auto_delete_non_competitor(target_type: str, result: dict[str, Any], override: int = -1) -> bool:
    if target_type != "competitor":
        return False
    if bool(result.get("is_competitor")):
        return False
    if override >= 0:
        return bool(override)
    with database.connect() as conn:
        return database.get_setting(conn, "auto_delete_non_competitors", "false") == "true"


def _should_auto_delete_non_customer(target_type: str, result: dict[str, Any], override: int = -1) -> bool:
    if target_type != "lead":
        return False
    if bool(result.get("is_customer")):
        return False
    if override >= 0:
        return bool(override)
    with database.connect() as conn:
        return database.get_setting(conn, "auto_delete_non_customers", "false") == "true"


def _delete_non_customer_lead_account(lead_id: int) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT 1 FROM lead_user_accounts WHERE id = ?", (lead_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="线索客户不存在，无法自动删除")
    return deletion.delete_lead_customer(lead_id, source="auto_non_customer_delete")


def build_input_payload(conn, target_type: str, target_id: int) -> dict[str, Any]:
    icp = _load_icp_profile(conn)
    if target_type == "competitor":
        account = conn.execute("SELECT * FROM user_accounts WHERE id = ?", (target_id,)).fetchone()
        if not account:
            raise HTTPException(status_code=404, detail="竞品账号不存在")
        contents = conn.execute(
            """
            SELECT title, description, content_url, source_keyword, like_count, comment_count
            FROM contents
            WHERE author_account_id = ?
            ORDER BY updated_at DESC
            LIMIT 5
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
            SELECT c.body AS comment_body, ct.title, ct.description, ct.content_url,
                   ct.source_keyword, src.source_type,
                   source_ua.nickname AS source_account_nickname,
                   source_ua.signature AS source_account_signature,
                   source_ua.profile_url AS source_account_url,
                   source_ua.competitor_status AS source_account_competitor_status,
                   source_ua.competitor_reason AS source_account_competitor_reason
            FROM lead_sources src
            LEFT JOIN comments c ON c.id = src.comment_id
            LEFT JOIN contents ct ON ct.id = src.content_id
            LEFT JOIN user_accounts source_ua ON source_ua.id = COALESCE(src.source_account_id, ct.author_account_id)
            WHERE src.lead_account_id = ? AND src.active = 1
            ORDER BY src.created_at DESC
            LIMIT 20
            """,
            (target_id,),
        ).fetchall()
        evidence_rows = database.rows_to_dicts(evidence)
        return {
            "icp": icp,
            "lead": database.row_to_dict(lead),
            "evidence": evidence_rows,
            "intent_signals": extract_lead_intent_signals(evidence_rows),
        }
    if target_type == "content":
        content = conn.execute("SELECT * FROM contents WHERE id = ?", (target_id,)).fetchone()
        if not content:
            raise HTTPException(status_code=404, detail="内容不存在")
        return {"icp": icp, "content": database.row_to_dict(content)}
    raise HTTPException(status_code=400, detail="不支持的 AI 分析类型")


def _load_icp_profile(conn) -> dict[str, Any]:
    """Keep old ICP settings explicit about missing optional fields."""
    try:
        raw = json.loads(database.get_setting(conn, "icp_profile", "{}") or "{}")
    except json.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {**DEFAULT_ICP_PROFILE, **raw}


def extract_lead_intent_signals(evidence: list[dict[str, Any]]) -> list[str]:
    signal_patterns = [
        ("询价", r"(多少|多少钱|价格|报价|价钱|钱|贵|便宜)"),
        ("详情", r"(详情|介绍|参数|配置|规格|型号|尺寸|多大|多长|多宽|多高)"),
        ("购买", r"(怎么买|哪里买|购买|下单|订购|卖吗|有卖|联系方式|电话|微信)"),
        ("定制/适配", r"(定制|订制|能不能做|可以做|能做|装一个|改装|适配|配套|方案)"),
        ("供应商/厂家", r"(哪家|哪个公司|厂家|供应商|源头|批发|代理)"),
    ]
    found: list[str] = []
    for row in evidence:
        text = " ".join(
            str(row.get(key) or "")
            for key in ("comment_body", "title", "description")
        )
        for label, pattern in signal_patterns:
            if label not in found and re.search(pattern, text, re.I):
                found.append(label)
    return found


def prompt_version_for(target_type: str) -> str:
    return PROMPT_VERSIONS.get(target_type, f"{target_type}_v1")


def call_openai_compatible(
    base_url: str,
    api_key: str,
    model: str,
    target_type: str,
    payload: dict[str, Any],
    system_prompt: str | None = None,
    user_prompt: str | None = None,
) -> str:
    endpoint = base_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = f"{endpoint}/chat/completions"
    if system_prompt is None or user_prompt is None:
        system_prompt, user_prompt = build_ai_messages(target_type, payload)
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


def build_ai_messages(target_type: str, payload: dict[str, Any]) -> tuple[str, str]:
    system_prompt = (
        "你是一个谨慎的AI拓客分析助手。必须只输出JSON，不要输出解释文字。"
        "判断时结合ICP画像和证据链，不要编造证据。"
    )
    if target_type == "competitor":
        # 竞品判断允许部分内容相关，但不能把关键词命中直接等同于竞品关系。
        user_prompt = (
            "你正在判断一个候选账号是否是我方真正的竞品账号。必须同时参考ICP画像、账号主页简介/签名、昵称、来源关键词和最近采集到的多个视频标题/详情，必要的话可以联网搜索相关账号信息；"
            "如果当前模型或接口不具备联网搜索能力，不得编造外部搜索结果。\n"
            "判断标准要适中：竞品账号应当在主页定位或部分近期视频中提供、销售、展示或获客与ICP高度相近的产品/服务；"
            "不要把关键词命中等同于竞品关系，除非视频简介/主页简介明确说明从事了这个行业；"
            "要在原因中说明相关视频数量、总视频数量、主页简介是否相关，以及和ICP重合/不重合的点。\n"
            "当可用视频少于3条时，只有主页简介明确相关且已有视频也明显相关，才可以判为竞品；证据不足时判为非竞品。\n"
            "输出格式："
            '{"is_competitor": true, "reason": "分析原因，必须说明主页简介、相关视频数量/总视频数量和ICP重合点", '
            '"relevant_content_count": 0, "total_content_count": 0, "profile_relevance": "高/中/低"}。\n'
            f"输入：{json.dumps(payload, ensure_ascii=False)}"
        )
        return system_prompt, user_prompt
    if target_type == "lead":
        user_prompt = (
            "你正在判断“竞品账号或竞品内容评论区”里的评论者是否是我方目标客户。"
            "评论场景本身很重要：如果来源账号已是竞品，或来源视频内容与ICP相关，那么评论者询问价格、报价、多少钱、详情、参数、配置、型号、尺寸、供应商、厂家、联系方式、怎么买、能不能做、能不能装、定制、适配、改装、批发等，通常应判定为目标客户。"
            "不要因为评论没有直接写出ICP关键词、客户主页简介为空、昵称像普通用户、没有公司信息，就把这类询价或详情咨询判为非客户。"
            "只有在评论明显与来源视频/ICP无关、纯玩笑围观、纯技术学习不涉及采购方案、同行广告、招聘、辱骂、抽奖互动时，才判为非客户。"
            "意向分级：高=明确询价/购买/定制/供应商/联系方式/采购配置；中=询问适配、方案可行性、关键参数或技术细节；低=泛泛关注但仍有产品需求线索。"
            "生成的script必须是可直接复制发送给客户的自然私信，不要出现模板占位符。"
            "ICP画像中的company_name/公司名是可选字段：如果非空，script可以自然使用该公司名；如果为空，script不得出现任何公司名、品牌名、公司占位符，也不得出现“我们公司”“我司”“本公司”“公司名”“XX公司”“{company_name}”等表达，且不得编造自己属于哪家公司；这种情况下优先用“这边”这类不暴露公司名的表达。"
            "输出格式："
            '{"is_customer": true, "intention": "低/中/高", "reason": "必须引用具体评论和来源视频依据", '
            '"pain_points": ["..."], "suggested_action": "...", "script": "基于评论内容生成可直接复制的自然私信话术"}。\n'
            f"输入：{json.dumps(payload, ensure_ascii=False)}"
        )
        return system_prompt, user_prompt
    user_prompt = (
        "判断是否为目标客户。"
        "生成的script必须是可直接复制发送给客户的自然私信，不要出现模板占位符。"
        "如果ICP画像中的company_name为空，script不得出现任何公司名、品牌名或公司占位符；如果非空，才可以自然使用该公司名。"
        "输出格式："
        '{"is_customer": true, "intention": "低/中/高", "reason": "...", '
        '"pain_points": ["..."], "suggested_action": "...", "script": "..."}。\n'
        f"输入：{json.dumps(payload, ensure_ascii=False)}"
    )
    return system_prompt, user_prompt


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
        reason = str(result.get("reason", "")).strip()
        conn.execute(
            """
            UPDATE user_accounts
            SET competitor_status = ?, account_role = ?, competitor_reason = ?,
                updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (status, role, reason, target_id),
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
        current = conn.execute(
            "SELECT screening_status, follow_status, manual_follow_status FROM lead_user_accounts WHERE id = ?",
            (lead_id,),
        ).fetchone()
        old_status = str(current["follow_status"]) if current else ""
        new_status = "未私信" if is_customer else "非客户"
        manual_follow_status = int(current["manual_follow_status"] or 0) if current else 0
        if manual_follow_status:
            conn.execute(
                """
                UPDATE lead_user_accounts
                SET intention = ?, reason = ?, pain_points = ?, suggested_action = ?,
                    script = ?, updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (
                    str(result.get("intention", "")),
                    str(result.get("reason", "")),
                    json.dumps(result.get("pain_points", []), ensure_ascii=False),
                    str(result.get("suggested_action", "")),
                    str(result.get("script", "")),
                    lead_id,
                ),
            )
        else:
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
