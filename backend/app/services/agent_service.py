from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import database
from app.schemas import AgentRunCreate, TaskCreate, TrafficPlanCreate
from app.services import account_actions, ai_service, crawler_adapter, license_service, message_workbench, traffic_workbench


ACTIVE_RUN_STATUSES = {"queued", "running"}
FINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled"}


class AgentCancelled(RuntimeError):
    pass


def create_run(payload: AgentRunCreate) -> dict[str, Any]:
    settings = _settings()
    _validate_ai_config(settings)
    if payload.interval_max_seconds < payload.interval_min_seconds:
        raise ValueError("最大私信间隔不能小于最小私信间隔")
    if payload.run_type not in ("lead_auto", "traffic_auto"):
        raise ValueError("未知的 AI 自动化类型")
    if payload.run_type in ("lead_auto", "traffic_auto") and payload.platform != "dy":
        raise ValueError("AI 自动化首版只支持抖音")

    keywords = _resolve_keywords(payload, settings)
    params = payload.model_dump()
    if payload.run_type == "traffic_auto" and not str(params.get("source_value") or "").strip():
        params["source_value"] = keywords[0] if keywords else ""

    run_id = uuid4().hex
    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_runs(id, run_type, platform, goal, keywords, params)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                payload.run_type,
                payload.platform,
                payload.goal.strip(),
                _json_dumps(keywords),
                _json_dumps(params),
            ),
        )
    _append_event(run_id, "info", "created", "AI 自动化批次已创建", {"keywords": keywords})
    _start_background_run(run_id)
    run = get_run(run_id)
    assert run is not None
    return run


def _start_background_run(run_id: str) -> None:
    # 编排过程会等待采集、AI 分析和私信批次，放到后台线程避免阻塞 HTTP 请求。
    threading.Thread(target=run_agent_run, args=(run_id,), daemon=True).start()


def list_runs(run_type: str = "") -> list[dict[str, Any]]:
    with database.connect() as conn:
        if run_type:
            rows = conn.execute(
                "SELECT * FROM agent_runs WHERE run_type = ? ORDER BY created_at DESC LIMIT 50",
                (run_type,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT 50").fetchall()
    return [_format_run(row) for row in rows]


def get_run(run_id: str) -> dict[str, Any] | None:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    return _format_run(row) if row else None


def list_events(run_id: str) -> list[dict[str, Any]]:
    with database.connect() as conn:
        exists = conn.execute("SELECT 1 FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if not exists:
            raise ValueError("AI 自动化批次不存在")
        rows = conn.execute(
            "SELECT * FROM agent_run_events WHERE run_id = ? ORDER BY id ASC",
            (run_id,),
        ).fetchall()
    return [_format_event(row) for row in rows]


def cancel_run(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    if not run:
        raise ValueError("AI 自动化批次不存在")

    with database.connect() as conn:
        if run["status"] == "queued":
            conn.execute(
                """
                UPDATE agent_runs
                SET status = 'cancelled', stop_requested = 1, error = '用户取消',
                    finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (run_id,),
            )
        elif run["status"] == "running":
            conn.execute(
                "UPDATE agent_runs SET stop_requested = 1, error = '用户请求取消', updated_at = datetime('now', 'localtime') WHERE id = ?",
                (run_id,),
            )

    for batch_id in run.get("related_message_batch_ids", []):
        try:
            message_workbench.cancel_auto_message_batch(str(batch_id))
        except Exception:
            pass
    if run.get("related_traffic_run_id"):
        try:
            traffic_workbench.stop_run(str(run["related_traffic_run_id"]))
        except Exception:
            pass
    _append_event(run_id, "warn", "cancel", "已请求停止 AI 自动化批次", {})
    updated = get_run(run_id)
    assert updated is not None
    return updated


def run_agent_run(run_id: str) -> None:
    run = get_run(run_id)
    if not run or run["status"] in FINAL_RUN_STATUSES:
        return
    _mark_started(run_id)
    try:
        _raise_if_stopped(run_id)
        if run["run_type"] == "lead_auto":
            result = _run_lead_auto(run_id)
        elif run["run_type"] == "traffic_auto":
            result = _run_traffic_auto(run_id)
        else:
            raise ValueError("未知的 AI 自动化类型")
        _raise_if_stopped(run_id)
        _finish_run(run_id, "succeeded", result, "")
    except AgentCancelled as exc:
        _finish_run(run_id, "cancelled", _current_result(run_id), str(exc) or "用户取消")
    except Exception as exc:
        _append_event(run_id, "error", "failed", "AI 自动化执行失败", {"error": str(exc)})
        _finish_run(run_id, "failed", _current_result(run_id), str(exc))


def _run_lead_auto(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    assert run is not None
    params = run["params"]
    keywords = run["keywords"]
    operation = str(params.get("lead_operation") or "full")
    auto_dm = bool(params.get("auto_dm", True))
    settings = _settings()
    license_service.ensure_authorized()
    if operation != "message":
        _validate_lead_environment(settings)
    _append_event(run_id, "info", "check", "拓客授权、AI 配置和采集环境已通过检查", {})

    summary: dict[str, Any] = {
        "keywords": keywords,
        "competitor_discovery_tasks": [],
        "competitor_analysis_tasks": [],
        "find_customer_tasks": [],
        "lead_ai_results": [],
        "message_batches": [],
        "competitor_candidates": 0,
        "competitor_accounts": 0,
        "customer_candidates": 0,
        "dm_success": 0,
        "dm_failed": 0,
        "dm_skipped": 0,
        "dm_errors": [],
    }

    if operation == "message":
        _run_message_batches(run_id, keywords, params, summary)
        return summary

    for keyword in keywords:
        _raise_if_stopped(run_id)
        discovery_task = crawler_adapter.create_task(
            TaskCreate(
                name=f"AI自动拓客-找竞品-{keyword}",
                mode="competitor_discovery",
                platform="dy",
                keywords=keyword,
                content_count=int(params.get("content_count") or 20),
                comment_count=0,
                collect_comments=False,
                collect_sub_comments=False,
                max_concurrency=1,
                headless=bool(settings.get("headless")),
                execute_crawler=True,
            )
        )
        _link_values(run_id, "related_task_ids", [str(discovery_task["id"])])
        summary["competitor_discovery_tasks"].append(discovery_task)
        _append_event(run_id, "info", "competitor_discovery", f"开始采集竞品关键词：{keyword}", {"task_id": discovery_task["id"]})
        crawler_adapter.run_task(str(discovery_task["id"]))
        _require_task_succeeded(str(discovery_task["id"]), "找竞品")

        analysis = account_actions.create_keyword_account_analysis_tasks("dy", keyword)
        summary["competitor_candidates"] += int(analysis.get("account_count") or 0)
        account_ids = [int(item["account_id"]) for item in analysis.get("accounts", []) if item.get("account_id")]
        task_ids = [str(task_id) for task_id in analysis.get("task_ids", [])]
        _link_values(run_id, "related_task_ids", task_ids)
        summary["competitor_analysis_tasks"].extend(analysis.get("tasks", []))
        if task_ids and account_ids:
            _append_event(run_id, "info", "competitor_screening", f"开始 AI 筛选竞品账号：{keyword}", {"task_ids": task_ids, "account_count": len(account_ids)})
            account_actions.run_keyword_account_analysis(account_ids, task_ids[0])
            _link_values(run_id, "related_ai_job_ids", _ai_job_ids_for_targets("competitor", account_ids))
        else:
            _append_event(run_id, "warn", "competitor_screening", f"没有可筛选的竞品候选：{keyword}", analysis)

        if operation == "competitors":
            _update_result(run_id, summary)
            continue

        customer_task = account_actions.create_keyword_find_customer_task("dy", keyword)
        find_task_ids = [str(task_id) for task_id in customer_task.get("task_ids", [])]
        summary["competitor_accounts"] += int(customer_task.get("account_count") or 0)
        summary["find_customer_tasks"].extend(customer_task.get("tasks", []))
        _link_values(run_id, "related_task_ids", find_task_ids)
        if find_task_ids:
            _append_event(run_id, "info", "customer_discovery", f"开始从竞品评论中找客户：{keyword}", {"task_ids": find_task_ids})
            crawler_adapter.run_tasks_serially(find_task_ids)
            for task_id in find_task_ids:
                _require_task_succeeded(task_id, "找客户")
                ai_result = ai_service.run_auto_lead_analysis_for_task(task_id)
                summary["lead_ai_results"].append(ai_result)
                summary["customer_candidates"] += int(ai_result.get("target_count") or 0)
                _link_values(run_id, "related_ai_job_ids", _lead_ai_job_ids_for_task(task_id))
        else:
            _append_event(run_id, "warn", "customer_discovery", f"没有可执行的找客户任务：{keyword}", customer_task)
        _update_result(run_id, summary)

    if operation == "full" and auto_dm:
        _run_message_batches(run_id, keywords, params, summary)
    elif operation in ("customers", "full"):
        _append_event(run_id, "info", "message", "本次计划不自动私信", {"auto_dm": auto_dm, "operation": operation})
    return summary


def _run_message_batches(run_id: str, keywords: list[str], params: dict[str, Any], summary: dict[str, Any]) -> None:
    remaining = int(params.get("dm_count") or 10)
    interval_min = int(params.get("interval_min_seconds") or 30)
    interval_max = int(params.get("interval_max_seconds") or 60)
    _append_event(run_id, "info", "message", f"准备自动私信，目标 {remaining} 个客户，间隔 {interval_min}-{interval_max} 秒", {})
    for keyword in keywords:
        if remaining <= 0:
            break
        _raise_if_stopped(run_id)
        try:
            batch = message_workbench.create_auto_message_batch("dy", keyword, remaining, interval_min, interval_max, run_now=True)
        except ValueError as exc:
            _append_event(run_id, "warn", "message", f"关键词没有可私信客户：{keyword}", {"reason": str(exc)})
            continue
        batch_id = str(batch["id"])
        _link_values(run_id, "related_message_batch_ids", [batch_id])
        _append_event(run_id, "info", "message", f"已创建自动私信批次：{keyword}", {"batch_id": batch_id})
        batch = _wait_message_batch(run_id, batch_id)
        summary["message_batches"].append(batch)
        summary["dm_success"] += int(batch.get("success_count") or 0)
        summary["dm_failed"] += int(batch.get("failed_count") or 0)
        summary["dm_skipped"] += int(batch.get("skipped_count") or 0)
        if batch.get("error"):
            summary["dm_errors"].append({"batch_id": batch_id, "error": batch.get("error")})
        remaining -= int(batch.get("total_count") or 0)
        _update_result(run_id, summary)
    if not summary["message_batches"]:
        _append_event(run_id, "warn", "message", "没有创建自动私信批次，请检查客户是否已完成 AI 筛选并处于未私信状态", {})


def _run_traffic_auto(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    assert run is not None
    params = run["params"]
    license_service.ensure_authorized_for("traffic")
    env = traffic_workbench.environment_check()
    if not env.get("ok"):
        raise ValueError(str(env.get("summary") or "引流执行环境未通过检查"))
    source_value = str(params.get("source_value") or "").strip()
    if not source_value and run["keywords"]:
        source_value = str(run["keywords"][0])
    if params.get("source_mode") == "search_keyword" and not source_value:
        raise ValueError("自动引流需要关键词或来源值")

    plan = traffic_workbench.create_plan(
        TrafficPlanCreate(
            name=f"AI自动引流-{source_value or run['goal'] or '默认计划'}",
            platform="dy",
            source_mode=params.get("source_mode") or "search_keyword",
            source_value=source_value,
            action_like=bool(params.get("action_like")),
            action_collect=bool(params.get("action_collect")),
            action_follow=bool(params.get("action_follow")),
            action_comment_text=bool(params.get("action_comment_text")),
            action_comment_image=bool(params.get("action_comment_image")),
            round_video_limit=int(params.get("round_video_limit") or 5),
            enabled=True,
        )
    )
    _set_related_single(run_id, "related_traffic_plan_id", str(plan["id"]))
    _append_event(run_id, "info", "traffic_plan", "已创建引流计划", {"plan_id": plan["id"], "source_value": source_value})
    _raise_if_stopped(run_id)

    traffic_run = traffic_workbench.create_run(str(plan["id"]))
    _set_related_single(run_id, "related_traffic_run_id", str(traffic_run["id"]))
    _append_event(run_id, "info", "traffic_run", "已启动引流批次", {"traffic_run_id": traffic_run["id"]})
    traffic_workbench.run_traffic_run(str(traffic_run["id"]))
    detail = traffic_workbench.get_run(str(traffic_run["id"])) or traffic_run
    return {"traffic_plan": plan, "traffic_run": detail}


def _wait_message_batch(run_id: str, batch_id: str) -> dict[str, Any]:
    while True:
        _raise_if_stopped(run_id)
        batch = message_workbench.get_auto_message_batch(batch_id)
        if str(batch.get("status")) not in message_workbench.ACTIVE_BATCH_STATUSES:
            return batch
        time.sleep(1)


def _validate_lead_environment(settings: dict[str, Any]) -> None:
    media_path = Path(str(settings.get("media_crawler_path") or "").strip().strip('"').strip("'"))
    raw_db = Path(str(settings.get("media_crawler_db_path") or "").strip().strip('"').strip("'"))
    if not media_path.exists():
        raise ValueError("MediaCrawler 路径不存在，请先在设置页配置")
    if not raw_db.exists():
        raise ValueError("MediaCrawler SQLite 路径不存在，请先在设置页配置")


def _resolve_keywords(payload: AgentRunCreate, settings: dict[str, Any]) -> list[str]:
    explicit = _clean_keywords(payload.keywords)
    if explicit:
        return explicit
    if payload.run_type == "lead_auto" and payload.lead_operation == "message":
        keywords = _message_keywords()
        if keywords:
            return keywords
        raise ValueError("当前没有可自动私信的未私信客户关键词队列")
    product_keywords = _clean_keywords(settings.get("product_keywords", []))
    if not product_keywords:
        raise ValueError("请先在设置页填写产品关键词，或在执行前手动输入关键词")
    return _select_keywords_with_ai(payload.goal, product_keywords, settings)


def _message_keywords() -> list[str]:
    result: list[str] = []
    for item in message_workbench.list_keywords():
        keyword = str(item.get("keyword") or "").strip()
        if keyword and int(item.get("unmessaged_count") or 0) > 0:
            result.append(keyword)
    return result


def _select_keywords_with_ai(goal: str, product_keywords: list[str], settings: dict[str, Any]) -> list[str]:
    system_prompt = "你是获客系统的关键词规划助手。必须只输出 JSON，不要输出解释。"
    user_prompt = (
        "请根据用户目标和产品关键词，挑选 1 到 5 个最适合抖音搜索的获客关键词。"
        "输出格式必须是 {\"keywords\": [\"关键词1\"]}。"
        f"\n用户目标：{goal or '未填写'}"
        f"\n产品关键词：{json.dumps(product_keywords, ensure_ascii=False)}"
    )
    raw = ai_service.call_openai_compatible(
        str(settings.get("ai_base_url") or ""),
        str(settings.get("ai_api_key") or ""),
        str(settings.get("ai_model") or ""),
        "agent_keywords",
        {},
        system_prompt,
        user_prompt,
    )
    parsed = _extract_json(raw)
    if isinstance(parsed, dict):
        values = parsed.get("keywords", [])
    else:
        values = parsed
    keywords = _clean_keywords(values)
    if not keywords:
        raise ValueError("AI 没有生成可用关键词，请调整目标描述或产品关键词后重试")
    return keywords[:5]


def _extract_json(raw: str) -> Any:
    text = str(raw or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if not match:
            raise ValueError("AI 返回内容不是 JSON")
        return json.loads(match.group(1))


def _clean_keywords(values: Any) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[,，\n]+", values)
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        keyword = str(value or "").strip()
        if keyword and keyword not in result:
            result.append(keyword)
    return result[:10]


def _validate_ai_config(settings: dict[str, Any]) -> None:
    if not (settings.get("ai_base_url") and settings.get("ai_api_key") and settings.get("ai_model")):
        raise ValueError("请先在设置页配置 AI Base URL、API Key 和模型")


def _settings() -> dict[str, Any]:
    with database.connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    result = {row["key"]: row["value"] for row in rows}
    try:
        result["product_keywords"] = json.loads(result.get("product_keywords", "[]"))
    except json.JSONDecodeError:
        result["product_keywords"] = []
    result["headless"] = str(result.get("headless", "false")) == "true"
    return result


def _require_task_succeeded(task_id: str, label: str) -> None:
    task = crawler_adapter.get_task(task_id)
    if task and task.get("status") == "succeeded":
        return
    raise ValueError(f"{label}任务未成功：{(task or {}).get('error') or task_id}")


def _ai_job_ids_for_targets(target_type: str, target_ids: list[int]) -> list[str]:
    if not target_ids:
        return []
    placeholders = ",".join(["?"] * len(target_ids))
    with database.connect() as conn:
        rows = conn.execute(
            f"SELECT id FROM analysis_jobs WHERE target_type = ? AND target_id IN ({placeholders}) ORDER BY created_at DESC",
            (target_type, *target_ids),
        ).fetchall()
    return [str(row["id"]) for row in rows]


def _lead_ai_job_ids_for_task(task_id: str) -> list[str]:
    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT aj.id
            FROM lead_sources ls
            JOIN analysis_jobs aj ON aj.target_type = 'lead' AND aj.target_id = ls.lead_account_id
            WHERE ls.task_id = ?
            ORDER BY aj.created_at DESC
            """,
            (task_id,),
        ).fetchall()
    return [str(row["id"]) for row in rows]


def _append_event(run_id: str, level: str, phase: str, message: str, details: dict[str, Any]) -> None:
    with database.connect() as conn:
        conn.execute(
            "INSERT INTO agent_run_events(run_id, level, phase, message, details) VALUES(?, ?, ?, ?, ?)",
            (run_id, level, phase, message, _json_dumps(details)),
        )


def _mark_started(run_id: str) -> None:
    with database.connect() as conn:
        conn.execute(
            """
            UPDATE agent_runs
            SET status = 'running', started_at = COALESCE(started_at, datetime('now', 'localtime')),
                updated_at = datetime('now', 'localtime')
            WHERE id = ? AND status = 'queued'
            """,
            (run_id,),
        )


def _finish_run(run_id: str, status: str, result: dict[str, Any], error: str) -> None:
    with database.connect() as conn:
        conn.execute(
            """
            UPDATE agent_runs
            SET status = ?, result = ?, error = ?, finished_at = datetime('now', 'localtime'),
                updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (status, _json_dumps(result), error, run_id),
        )
    _append_event(run_id, "info" if status == "succeeded" else "warn", status, f"AI 自动化批次已{_status_label(status)}", {"error": error})


def _status_label(status: str) -> str:
    return {"succeeded": "完成", "failed": "失败", "cancelled": "取消"}.get(status, status)


def _update_result(run_id: str, result: dict[str, Any]) -> None:
    with database.connect() as conn:
        conn.execute(
            "UPDATE agent_runs SET result = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (_json_dumps(result), run_id),
        )


def _current_result(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    return run.get("result", {}) if run else {}


def _link_values(run_id: str, column: str, values: list[str]) -> None:
    values = [str(value) for value in values if str(value)]
    if not values:
        return
    with database.connect() as conn:
        row = conn.execute(f"SELECT {column} FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        current = _json_loads(row[column] if row else "[]", [])
        merged = list(dict.fromkeys([*current, *values]))
        conn.execute(
            f"UPDATE agent_runs SET {column} = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (_json_dumps(merged), run_id),
        )


def _set_related_single(run_id: str, column: str, value: str) -> None:
    with database.connect() as conn:
        conn.execute(
            f"UPDATE agent_runs SET {column} = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (value, run_id),
        )


def _raise_if_stopped(run_id: str) -> None:
    with database.connect() as conn:
        row = conn.execute("SELECT stop_requested FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    if row and int(row["stop_requested"] or 0):
        raise AgentCancelled("用户取消")


def _format_run(row: Any) -> dict[str, Any]:
    run = database.row_to_dict(row) or {}
    for key, fallback in (
        ("keywords", []),
        ("params", {}),
        ("result", {}),
        ("related_task_ids", []),
        ("related_ai_job_ids", []),
        ("related_message_batch_ids", []),
    ):
        run[key] = _json_loads(run.get(key), fallback)
    return run


def _format_event(row: Any) -> dict[str, Any]:
    event = database.row_to_dict(row) or {}
    event["details"] = _json_loads(event.get("details"), {})
    return event


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except json.JSONDecodeError:
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback
