from __future__ import annotations

import json
import re
import threading
from typing import Any

from app import database
from app.schemas import (
    AgentCommandRequest,
    AgentRunCreate,
    AiBatchCreate,
    AiJobCreate,
    BulkActionPreview,
    MessageAutoBatchCreate,
    TaskCreate,
    TrafficPlanCreate,
)
from app.services import (
    account_actions,
    agent_service,
    ai_service,
    bulk_actions,
    crawler_adapter,
    diagnostics,
    license_service,
    message_workbench,
    traffic_workbench,
)


READABLE_TABLES = {
    "crawl_jobs",
    "task_logs",
    "user_accounts",
    "contents",
    "comments",
    "account_sources",
    "lead_user_accounts",
    "lead_sources",
    "lead_status_events",
    "message_batches",
    "message_batch_items",
    "agent_runs",
    "agent_run_events",
    "raw_source_refs",
    "deleted_identities",
    "analysis_jobs",
    "deletion_audit",
    "traffic_plans",
    "traffic_runs",
    "traffic_run_items",
    "traffic_action_logs",
    "traffic_records",
    "traffic_material_texts",
    "traffic_material_images",
    "traffic_dedup_ledger",
}
SYSTEM_ACTIONS = {
    "settings_read": False,
    "task_preview": False,
    "task_detail": False,
    "task_diagnostics": False,
    "tasks_list": False,
    "message_keywords": False,
    "message_customers": False,
    "ai_workbench": False,
    "traffic_environment_check": False,
    "traffic_runs_list": False,
    "traffic_records_list": False,
    "traffic_source_keywords": False,
    "traffic_source_competitor_videos": False,
    "task_create": True,
    "task_cancel": True,
    "task_archive": True,
    "profile_enrichment_batch": True,
    "account_analysis": True,
    "account_find_customers": True,
    "customer_intent_analysis": True,
    "customer_follow_status": True,
    "account_customers_analyze": True,
    "delete_account_non_customers": True,
    "delete_keyword_non_competitors": True,
    "ai_job_create": True,
    "ai_batch_create": True,
    "ai_job_retry": True,
    "bulk_action_preview": False,
    "message_batch_create": True,
    "message_batch_cancel": True,
    "traffic_plan_create": True,
    "traffic_run_create": True,
    "traffic_run_stop": True,
    "traffic_open_login": True,
    "traffic_environment_install": True,
}
MUTATING_ACTIONS = {"lead_auto", "traffic_auto", "system_action"}
QUERY_ACTIONS = {"answer_stats", "query_database"}
ALLOWED_ACTIONS = QUERY_ACTIONS | MUTATING_ACTIONS | {"unknown"}
SAFE_SQL_START = re.compile(r"^(select|with)\b", re.I)
BLOCKED_SQL_WORDS = re.compile(r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum)\b", re.I)
TABLE_REF_PATTERN = re.compile(r"\b(?:from|join)\s+([A-Za-z_][\w]*)", re.I)
TARGET_FOLLOW_STATUSES = ("未私信", "已私信", "未回复", "已回复", "未成交", "已成交")


def preview_command(payload: AgentCommandRequest) -> dict[str, Any]:
    plan = _normalize_plan(payload.plan or _build_plan(payload), payload)
    if plan["action"] == "answer_stats":
        result = _answer_stats(str(plan.get("metric") or "overview"))
        return {
            "executed": True,
            "requires_confirmation": False,
            "can_execute": False,
            "plan": plan,
            "answer": result["answer"],
            "result": result,
        }
    if plan["action"] == "query_database":
        result = _query_database(str(plan.get("sql") or ""))
        return {
            "executed": True,
            "requires_confirmation": False,
            "can_execute": False,
            "plan": plan,
            "answer": result["answer"],
            "result": result,
        }
    if plan["action"] == "system_action" and not _system_action_mutates(plan):
        result = _run_system_action(plan)
        return {
            "executed": True,
            "requires_confirmation": False,
            "can_execute": False,
            "plan": plan,
            "answer": _system_action_done_answer(plan, result),
            "result": result,
        }
    if plan["action"] in MUTATING_ACTIONS:
        return {
            "executed": False,
            "requires_confirmation": True,
            "can_execute": True,
            "plan": plan,
            "answer": _plan_answer(plan),
            "result": {},
        }
    return {
        "executed": False,
        "requires_confirmation": False,
        "can_execute": False,
        "plan": plan,
        "answer": "我还不能可靠理解这条指令。请换一种更明确的说法，例如：检查未私信客户、找竞品、找客户先不私信、私信未私信客户。",
        "result": {},
    }


def execute_command(payload: AgentCommandRequest) -> dict[str, Any]:
    plan = _normalize_plan(payload.plan or _build_plan(payload), payload)
    if plan["action"] in QUERY_ACTIONS:
        return preview_command(payload.model_copy(update={"plan": plan}))
    if plan["action"] == "lead_auto":
        run = agent_service.create_run(_lead_run_payload(payload.command, plan))
    elif plan["action"] == "traffic_auto":
        run = agent_service.create_run(_traffic_run_payload(payload.command, plan))
    elif plan["action"] == "system_action":
        result = _run_system_action(plan)
        return {
            "executed": True,
            "requires_confirmation": False,
            "can_execute": False,
            "plan": plan,
            "answer": _system_action_done_answer(plan, result),
            "result": result,
        }
    else:
        raise ValueError("这条指令没有可执行动作")
    return {
        "executed": True,
        "requires_confirmation": False,
        "can_execute": False,
        "plan": plan,
        "answer": "已创建AI自动化批次，右侧会持续刷新运行日志和结果。",
        "result": {"run": run},
        "run": run,
    }


def _build_plan(payload: AgentCommandRequest) -> dict[str, Any]:
    settings = agent_service._settings()
    agent_service._validate_ai_config(settings)
    system_prompt = (
        "你是本地AI获客系统的指令规划器，只能输出JSON。"
        "你不能编造数据，不能要求执行任意代码，不能生成写入数据库的SQL。"
        "执行类动作只允许 lead_auto、traffic_auto 或 system_action；查询类动作只允许 answer_stats、query_database 或只读 system_action。"
    )
    user_prompt = (
        "请把用户指令解析成一个JSON计划。\n"
        "可选action：\n"
        "- answer_stats：回答常见统计问题，metric只能是 overview、competitors、target_customers、unmessaged、dm_summary。\n"
        "- query_database：读取数据库信息，sql必须是只读SELECT，并且只能查询给出的安全表。\n"
        "- lead_auto：抖音拓客执行，lead_operation只能是 full、competitors、customers、message；auto_dm表示是否自动私信。\n"
        "- traffic_auto：抖音引流执行。\n"
        "- system_action：调用系统已有能力，operation必须来自系统操作白名单，params只能放该操作需要的参数。\n"
        "- unknown：无法理解时使用。\n"
        "输出字段建议：action、title、summary、metric、sql、keywords、lead_operation、auto_dm、dm_count、"
        "interval_min_seconds、interval_max_seconds、content_count、source_mode、source_value、actions、round_video_limit、operation、params、steps。\n"
        "约束：用户说只找竞品时用 lead_operation=competitors；说找客户但先不私信时用 customers 且 auto_dm=false；"
        "说私信未私信客户时用 message；说找客户并私信时用 full 且 auto_dm=true。\n"
        f"当前页面：{payload.workspace}\n"
        f"系统操作白名单：{json.dumps(_system_action_catalog(), ensure_ascii=False)}\n"
        f"安全表结构：{json.dumps(_schema_summary(), ensure_ascii=False)}\n"
        f"用户指令：{payload.command}"
    )
    raw = ai_service.call_openai_compatible(
        str(settings.get("ai_base_url") or ""),
        str(settings.get("ai_api_key") or ""),
        str(settings.get("ai_model") or ""),
        "agent_command",
        {"command": payload.command, "workspace": payload.workspace},
        system_prompt,
        user_prompt,
    )
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        raise ValueError("AI指令规划结果必须是JSON对象")
    return parsed


def _normalize_plan(plan: dict[str, Any], payload: AgentCommandRequest) -> dict[str, Any]:
    normalized = dict(plan or {})
    action = str(normalized.get("action") or "").strip()
    if action not in ALLOWED_ACTIONS:
        inferred_operation = _infer_system_operation(payload.command, payload.workspace)
        action = "system_action" if inferred_operation else _infer_action(payload.command, payload.workspace)
        if inferred_operation:
            normalized["operation"] = normalized.get("operation") or inferred_operation
    if action == "unknown":
        inferred_operation = _infer_system_operation(payload.command, payload.workspace)
        action = "system_action" if inferred_operation else _infer_action(payload.command, payload.workspace)
        if inferred_operation:
            normalized["operation"] = normalized.get("operation") or inferred_operation
    normalized["action"] = action
    normalized["title"] = str(normalized.get("title") or _default_title(action)).strip()
    normalized["summary"] = str(normalized.get("summary") or _default_summary(action)).strip()
    normalized["steps"] = _clean_steps(normalized.get("steps"), action)
    normalized["keywords"] = _clean_keywords(normalized.get("keywords", []))
    if action == "answer_stats":
        inferred_metric = _infer_metric(payload.command)
        metric = str(normalized.get("metric") or inferred_metric).strip()
        if inferred_metric != "overview":
            metric = inferred_metric
        normalized["metric"] = metric if metric in {"overview", "competitors", "target_customers", "unmessaged", "dm_summary"} else "overview"
    if action == "lead_auto":
        normalized.update(_normalize_lead_plan(normalized, payload.command))
    if action == "traffic_auto":
        normalized.update(_normalize_traffic_plan(normalized))
    if action == "system_action":
        normalized["operation"] = normalized.get("operation") or _infer_system_operation(payload.command, payload.workspace)
        normalized.update(_normalize_system_action(normalized))
    return normalized


def _normalize_lead_plan(plan: dict[str, Any], command: str) -> dict[str, Any]:
    operation = str(plan.get("lead_operation") or "").strip()
    if operation not in {"full", "competitors", "customers", "message"}:
        operation = _infer_lead_operation(command)
    auto_dm = bool(plan.get("auto_dm", operation in {"full", "message"}))
    if re.search(r"(先不|不要|不需要|无需).{0,6}私信", command):
        auto_dm = False
    if operation in {"competitors", "customers"}:
        auto_dm = False
    return {
        "lead_operation": operation,
        "auto_dm": auto_dm,
        "dm_count": _bounded_int(plan.get("dm_count"), 10, 1, 200),
        "interval_min_seconds": _bounded_int(plan.get("interval_min_seconds"), 30, 0, 3600),
        "interval_max_seconds": _bounded_int(plan.get("interval_max_seconds"), 60, 0, 3600),
        "content_count": _bounded_int(plan.get("content_count"), 20, 1, 500),
    }


def _normalize_traffic_plan(plan: dict[str, Any]) -> dict[str, Any]:
    source_mode = str(plan.get("source_mode") or "search_keyword").strip()
    if source_mode not in {"random_feed", "competitor_videos", "collected_keyword", "search_keyword"}:
        source_mode = "search_keyword"
    actions = plan.get("actions")
    if not isinstance(actions, list):
        actions = ["like"]
    return {
        "source_mode": source_mode,
        "source_value": str(plan.get("source_value") or "").strip(),
        "actions": [str(item).strip() for item in actions if str(item).strip()],
        "round_video_limit": _bounded_int(plan.get("round_video_limit"), 5, 1, 200),
    }


def _normalize_system_action(plan: dict[str, Any]) -> dict[str, Any]:
    operation = str(plan.get("operation") or "").strip()
    if operation not in SYSTEM_ACTIONS:
        raise ValueError(f"不支持的系统操作：{operation or '未提供'}")
    params = plan.get("params")
    if not isinstance(params, dict):
        params = {}
    return {"operation": operation, "params": params}


def _lead_run_payload(command: str, plan: dict[str, Any]) -> AgentRunCreate:
    interval_min = int(plan.get("interval_min_seconds") or 30)
    interval_max = max(interval_min, int(plan.get("interval_max_seconds") or 60))
    return AgentRunCreate(
        run_type="lead_auto",
        goal=command,
        platform="dy",
        keywords=_clean_keywords(plan.get("keywords", [])),
        lead_operation=str(plan.get("lead_operation") or "full"),
        auto_dm=bool(plan.get("auto_dm", True)),
        content_count=int(plan.get("content_count") or 20),
        dm_count=int(plan.get("dm_count") or 10),
        interval_min_seconds=interval_min,
        interval_max_seconds=interval_max,
    )


def _traffic_run_payload(command: str, plan: dict[str, Any]) -> AgentRunCreate:
    actions = set(plan.get("actions") or [])
    keywords = _clean_keywords(plan.get("keywords", []))
    return AgentRunCreate(
        run_type="traffic_auto",
        goal=command,
        platform="dy",
        keywords=keywords,
        source_mode=str(plan.get("source_mode") or "search_keyword"),
        source_value=str(plan.get("source_value") or (keywords[0] if keywords else "")),
        action_like="like" in actions or not actions,
        action_collect="collect" in actions,
        action_follow="follow" in actions,
        action_comment_text="comment_text" in actions,
        action_comment_image="comment_image" in actions,
        round_video_limit=int(plan.get("round_video_limit") or 5),
    )


def _system_action_mutates(plan: dict[str, Any]) -> bool:
    return bool(SYSTEM_ACTIONS.get(str(plan.get("operation") or "")))


def _run_system_action(plan: dict[str, Any]) -> dict[str, Any]:
    operation = str(plan.get("operation") or "")
    params = plan.get("params") if isinstance(plan.get("params"), dict) else {}
    if operation not in SYSTEM_ACTIONS:
        raise ValueError(f"不支持的系统操作：{operation}")
    result = _SYSTEM_ACTION_HANDLERS[operation](params)
    return {"operation": operation, "mutates": SYSTEM_ACTIONS[operation], "data": result}


def _settings_redacted(_: dict[str, Any]) -> dict[str, Any]:
    with database.connect() as conn:
        rows = conn.execute("SELECT key, value, updated_at FROM settings ORDER BY key").fetchall()
    secrets = re.compile(r"(api_key|license|device_code|token|secret|password)", re.I)
    return {
        row["key"]: {
            "value": "***" if secrets.search(str(row["key"])) else row["value"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    }


def _task_preview(params: dict[str, Any]) -> dict[str, Any]:
    return crawler_adapter.preview_task(TaskCreate(**params))


def _tasks_list(params: dict[str, Any]) -> list[dict[str, Any]]:
    return crawler_adapter.list_tasks(bool(params.get("include_archived", False)))


def _task_detail(params: dict[str, Any]) -> dict[str, Any]:
    task_id = _required_str(params, "task_id")
    task = crawler_adapter.get_task(task_id)
    if not task:
        raise ValueError("任务不存在")
    task["logs"] = crawler_adapter.list_task_logs(task_id)
    return task


def _task_diagnostics(params: dict[str, Any]) -> dict[str, Any]:
    return diagnostics.task_diagnostics(_required_str(params, "task_id"))


def _task_create(params: dict[str, Any]) -> dict[str, Any]:
    license_service.ensure_authorized()
    payload = TaskCreate(**params)
    account_ids = (
        account_actions.resolve_account_analysis_task_account_ids(payload.model_dump())
        if payload.mode == "account_analysis"
        else []
    )
    task = crawler_adapter.create_task(payload)
    if payload.mode == "account_analysis" and account_ids:
        target = account_actions.run_account_analysis if len(account_ids) == 1 else account_actions.run_account_analysis_batch
        args = (account_ids[0], str(task["id"])) if len(account_ids) == 1 else (account_ids, str(task["id"]))
        _background(target, *args)
    else:
        _background(crawler_adapter.run_task, str(task["id"]))
    return task


def _task_cancel(params: dict[str, Any]) -> dict[str, Any]:
    return crawler_adapter.cancel_task(_required_str(params, "task_id"))


def _task_archive(params: dict[str, Any]) -> dict[str, Any]:
    return crawler_adapter.archive_task(_required_str(params, "task_id"))


def _profile_enrichment_batch(params: dict[str, Any]) -> dict[str, Any]:
    license_service.ensure_authorized()
    result = crawler_adapter.create_profile_enrichment_batch(_bounded_int(params.get("limit"), 10, 1, 50))
    if params.get("run_now", True) and result.get("task_ids"):
        _background(crawler_adapter.run_tasks_serially, result["task_ids"])
    return result


def _account_analysis(params: dict[str, Any]) -> dict[str, Any]:
    license_service.ensure_authorized()
    account_id = _required_int(params, "account_id")
    task = account_actions.create_account_analysis_task(account_id)
    if params.get("run_now", True):
        _background(account_actions.run_account_analysis, account_id, str(task["id"]))
    return task


def _account_find_customers(params: dict[str, Any]) -> dict[str, Any]:
    license_service.ensure_authorized()
    result = account_actions.create_account_find_customer_task(_required_int(params, "account_id"))
    if params.get("run_now", True) and result.get("task_ids"):
        _background(crawler_adapter.run_tasks_serially, result["task_ids"])
    return result


def _customer_intent_analysis(params: dict[str, Any]) -> dict[str, Any]:
    license_service.ensure_authorized()
    return account_actions.create_customer_intent_analysis(_required_int(params, "lead_id"), run_now=bool(params.get("run_now", True)))


def _customer_follow_status(params: dict[str, Any]) -> dict[str, Any]:
    return account_actions.update_customer_follow_status(
        _required_int(params, "lead_id"),
        _required_str(params, "follow_status"),
        str(params.get("note") or ""),
    )


def _account_customers_analyze(params: dict[str, Any]) -> dict[str, Any]:
    license_service.ensure_authorized()
    result = account_actions.create_account_customer_intent_jobs(_required_int(params, "account_id"), run_now=False)
    if params.get("run_now", True) and result.get("job_ids"):
        _background(account_actions.run_account_customer_intent_jobs, result["job_ids"])
    return result


def _delete_account_non_customers(params: dict[str, Any]) -> dict[str, Any]:
    return account_actions.delete_account_non_customers(_required_int(params, "account_id"))


def _delete_keyword_non_competitors(params: dict[str, Any]) -> dict[str, Any]:
    return account_actions.delete_keyword_non_competitors(
        str(params.get("platform") or "dy"),
        _required_str(params, "keyword"),
    )


def _ai_job_create(params: dict[str, Any]) -> dict[str, Any]:
    license_service.ensure_authorized()
    payload = AiJobCreate(**params)
    return ai_service.create_ai_job(payload.target_type, payload.target_id, payload.run_now)


def _ai_batch_create(params: dict[str, Any]) -> list[dict[str, Any]]:
    license_service.ensure_authorized()
    payload = AiBatchCreate(**params)
    return ai_service.create_batch_jobs(payload.target_type, payload.target_ids, payload.run_now)


def _ai_job_retry(params: dict[str, Any]) -> dict[str, Any]:
    license_service.ensure_authorized()
    return ai_service.retry_ai_job(_required_str(params, "job_id"))


def _bulk_action_preview(params: dict[str, Any]) -> dict[str, Any]:
    return bulk_actions.preview_bulk_action(BulkActionPreview(**params))


def _message_customers(params: dict[str, Any]) -> dict[str, Any]:
    return message_workbench.list_customers(
        keyword=str(params.get("keyword") or ""),
        platform=str(params.get("platform") or ""),
        status=str(params.get("status") or ""),
        query=str(params.get("query") or ""),
        page=_bounded_int(params.get("page"), 1, 1, 10_000),
        page_size=_bounded_int(params.get("page_size"), 20, 1, 100),
    )


def _message_batch_create(params: dict[str, Any]) -> dict[str, Any]:
    license_service.ensure_authorized()
    payload = MessageAutoBatchCreate(**params)
    return message_workbench.create_auto_message_batch(
        payload.platform,
        payload.keyword,
        payload.count,
        payload.interval_min_seconds,
        payload.interval_max_seconds,
    )


def _message_batch_cancel(params: dict[str, Any]) -> dict[str, Any]:
    license_service.ensure_authorized()
    return message_workbench.cancel_auto_message_batch(_required_str(params, "batch_id"))


def _traffic_plan_create(params: dict[str, Any]) -> dict[str, Any]:
    return traffic_workbench.create_plan(TrafficPlanCreate(**params))


def _traffic_run_create(params: dict[str, Any]) -> dict[str, Any]:
    license_service.ensure_authorized_for("traffic")
    run = traffic_workbench.create_run(_required_str(params, "plan_id"))
    if params.get("run_now", True):
        _background(traffic_workbench.run_traffic_run, str(run["id"]))
    return run


def _traffic_run_stop(params: dict[str, Any]) -> dict[str, Any]:
    return traffic_workbench.stop_run(_required_str(params, "run_id"))


def _traffic_records_list(params: dict[str, Any]) -> dict[str, Any]:
    return traffic_workbench.list_records(
        platform=str(params.get("platform") or ""),
        action_type=str(params.get("action_type") or ""),
        status=str(params.get("status") or ""),
        query=str(params.get("query") or ""),
        page=_bounded_int(params.get("page"), 1, 1, 10_000),
        page_size=_bounded_int(params.get("page_size"), 20, 1, 100),
    )


def _traffic_source_competitor_videos(params: dict[str, Any]) -> list[dict[str, Any]]:
    return traffic_workbench.source_competitor_videos(_bounded_int(params.get("limit"), 100, 1, 500))


def _background(fn: Any, *args: Any) -> None:
    # ponytail: one daemon thread matches existing agent/task execution; add a queue only if concurrency becomes painful.
    threading.Thread(target=fn, args=args, daemon=True).start()


def _required_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key) or "").strip()
    if not value:
        raise ValueError(f"缺少参数：{key}")
    return value


def _required_int(params: dict[str, Any], key: str) -> int:
    try:
        return int(params.get(key))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"缺少参数：{key}") from exc


_SYSTEM_ACTION_HANDLERS = {
    "settings_read": _settings_redacted,
    "task_preview": _task_preview,
    "task_detail": _task_detail,
    "task_diagnostics": _task_diagnostics,
    "tasks_list": _tasks_list,
    "message_keywords": lambda _params: message_workbench.list_keywords(),
    "message_customers": _message_customers,
    "ai_workbench": lambda _params: ai_service.ai_workbench(),
    "traffic_environment_check": lambda _params: traffic_workbench.environment_check(),
    "traffic_runs_list": lambda params: traffic_workbench.list_runs(bool(params.get("include_archived", False))),
    "traffic_records_list": _traffic_records_list,
    "traffic_source_keywords": lambda _params: traffic_workbench.source_keywords(),
    "traffic_source_competitor_videos": _traffic_source_competitor_videos,
    "task_create": _task_create,
    "task_cancel": _task_cancel,
    "task_archive": _task_archive,
    "profile_enrichment_batch": _profile_enrichment_batch,
    "account_analysis": _account_analysis,
    "account_find_customers": _account_find_customers,
    "customer_intent_analysis": _customer_intent_analysis,
    "customer_follow_status": _customer_follow_status,
    "account_customers_analyze": _account_customers_analyze,
    "delete_account_non_customers": _delete_account_non_customers,
    "delete_keyword_non_competitors": _delete_keyword_non_competitors,
    "ai_job_create": _ai_job_create,
    "ai_batch_create": _ai_batch_create,
    "ai_job_retry": _ai_job_retry,
    "bulk_action_preview": _bulk_action_preview,
    "message_batch_create": _message_batch_create,
    "message_batch_cancel": _message_batch_cancel,
    "traffic_plan_create": _traffic_plan_create,
    "traffic_run_create": _traffic_run_create,
    "traffic_run_stop": _traffic_run_stop,
    "traffic_open_login": lambda _params: traffic_workbench.open_douyin_login_window(),
    "traffic_environment_install": lambda _params: traffic_workbench.install_environment(),
}


def _answer_stats(metric: str) -> dict[str, Any]:
    with database.connect() as conn:
        values = {
            "competitors": _scalar(conn, "SELECT COUNT(*) FROM user_accounts WHERE competitor_status = '竞品'"),
            "target_customers": _scalar(
                conn,
                """
                SELECT COUNT(*) FROM lead_user_accounts
                WHERE hidden = 0
                  AND (screening_status = '目标客户' OR follow_status IN ('未私信', '已私信', '未回复', '已回复', '未成交', '已成交'))
                """,
            ),
            "unmessaged": _scalar(conn, "SELECT COUNT(*) FROM lead_user_accounts WHERE hidden = 0 AND follow_status = '未私信'"),
            "dm_success": _scalar(conn, "SELECT COALESCE(SUM(success_count), 0) FROM message_batches"),
            "dm_failed": _scalar(conn, "SELECT COALESCE(SUM(failed_count), 0) FROM message_batches"),
            "dm_batches": _scalar(conn, "SELECT COUNT(*) FROM message_batches"),
            "agent_message_runs": _scalar(
                conn,
                """
                SELECT COUNT(*) FROM agent_runs
                WHERE run_type = 'lead_auto'
                  AND (params LIKE '%"lead_operation": "message"%' OR params LIKE '%"lead_operation":"message"%')
                """,
            ),
        }
    keyword_stats = message_workbench.list_keywords()
    keyword_unmessaged = int(keyword_stats[0].get("unmessaged_count") or values["unmessaged"]) if keyword_stats else values["unmessaged"]
    values["unmessaged"] = keyword_unmessaged
    answer = _stat_answer(metric, values)
    return {"metric": metric, "values": values, "answer": answer}


def _query_database(sql: str) -> dict[str, Any]:
    safe_sql = _safe_select_sql(sql)
    with database.connect() as conn:
        rows = database.rows_to_dicts(conn.execute(safe_sql).fetchall())
    columns = list(rows[0].keys()) if rows else []
    return {
        "sql": safe_sql,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "answer": f"已按只读查询返回 {len(rows)} 条记录。",
    }


def _safe_select_sql(sql: str) -> str:
    value = str(sql or "").strip().rstrip(";")
    if not SAFE_SQL_START.search(value):
        raise ValueError("Agent数据库查询只允许SELECT只读语句")
    if ";" in value:
        raise ValueError("Agent数据库查询不允许包含多条SQL")
    if BLOCKED_SQL_WORDS.search(value):
        raise ValueError("Agent数据库查询不允许包含写入或管理类SQL关键字")
    referenced = {name.lower() for name in TABLE_REF_PATTERN.findall(value)}
    blocked = sorted(name for name in referenced if name not in READABLE_TABLES)
    if blocked:
        raise ValueError(f"Agent不能读取这些表：{', '.join(blocked)}")
    if not re.search(r"\blimit\b", value, re.I):
        value = f"{value} LIMIT 50"
    return value


def _system_action_catalog() -> dict[str, dict[str, Any]]:
    descriptions = {
        "settings_read": "查看设置，敏感值会打码",
        "task_preview": "预览采集任务参数",
        "task_detail": "查看任务详情和日志",
        "task_diagnostics": "查看任务失败诊断",
        "tasks_list": "查看任务列表",
        "message_keywords": "查看私信关键词队列",
        "message_customers": "查看私信工作台客户",
        "ai_workbench": "查看AI分析工作台",
        "traffic_environment_check": "检查引流执行环境",
        "traffic_runs_list": "查看引流批次列表",
        "traffic_records_list": "查看引流操作记录",
        "traffic_source_keywords": "查看可用于引流的关键词",
        "traffic_source_competitor_videos": "查看可用于引流的竞品视频",
        "task_create": "创建并启动采集任务",
        "task_cancel": "取消采集任务",
        "task_archive": "归档采集任务",
        "profile_enrichment_batch": "批量创建主页资料补全任务",
        "account_analysis": "对单个账号执行竞品分析",
        "account_find_customers": "从单个竞品账号找客户",
        "customer_intent_analysis": "分析单个客户意向",
        "customer_follow_status": "修改客户跟进状态",
        "account_customers_analyze": "分析某竞品账号下全部客户",
        "delete_account_non_customers": "删除某竞品账号下非客户",
        "delete_keyword_non_competitors": "删除某关键词下非竞品",
        "ai_job_create": "创建单个AI分析任务",
        "ai_batch_create": "批量创建AI分析任务",
        "ai_job_retry": "重试AI分析任务",
        "bulk_action_preview": "预览批量操作影响",
        "message_batch_create": "创建AI一键私信批次",
        "message_batch_cancel": "取消AI一键私信批次",
        "traffic_plan_create": "创建引流计划",
        "traffic_run_create": "启动引流计划批次",
        "traffic_run_stop": "停止引流批次",
        "traffic_open_login": "打开抖音登录窗口",
        "traffic_environment_install": "安装引流环境依赖",
    }
    return {
        name: {"mutates": mutates, "description": descriptions.get(name, name)}
        for name, mutates in SYSTEM_ACTIONS.items()
    }


def _schema_summary() -> dict[str, list[str]]:
    summary: dict[str, list[str]] = {}
    with database.connect() as conn:
        for table in sorted(READABLE_TABLES):
            row = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
            if not row:
                continue
            columns = conn.execute(f"PRAGMA table_info({database.quote_identifier(table)})").fetchall()
            summary[table] = [str(column["name"]) for column in columns]
    return summary


def _system_action_done_answer(plan: dict[str, Any], result: dict[str, Any]) -> str:
    operation = str(plan.get("operation") or "")
    mutates = "已执行" if result.get("mutates") else "已查询"
    return f"{mutates}系统操作：{operation}。"


def _scalar(conn: Any, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int((row[0] if row else 0) or 0)


def _extract_json(raw: str) -> Any:
    text = str(raw or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if not match:
            raise ValueError("AI指令规划结果不是JSON")
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


def _clean_steps(values: Any, action: str) -> list[str]:
    if isinstance(values, list):
        result = [str(item or "").strip() for item in values if str(item or "").strip()]
        if result:
            return result[:8]
    defaults = {
        "lead_auto": ["解析目标", "生成关键词", "创建拓客批次", "刷新日志和结果"],
        "traffic_auto": ["解析引流目标", "创建引流计划", "启动执行批次", "刷新日志和结果"],
        "answer_stats": ["读取本地数据", "汇总统计结果"],
        "query_database": ["执行只读查询", "返回查询结果"],
    }
    return defaults.get(action, ["等待更明确的指令"])


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _infer_action(command: str, workspace: str) -> str:
    text = str(command or "")
    if re.search(r"(多少|几个|检查|查看|统计|列出|查询|数据库)", text):
        return "answer_stats"
    if "引流" in text or workspace == "traffic":
        return "traffic_auto"
    if re.search(r"(竞品|客户|私信|拓客|寻找|找些)", text):
        return "lead_auto"
    return "unknown"


def _infer_system_operation(command: str, workspace: str) -> str:
    text = str(command or "")
    if re.search(r"(设置|配置)", text):
        return "settings_read"
    if "抖音" in text and "登录" in text:
        return "traffic_open_login"
    if "环境" in text and "安装" in text:
        return "traffic_environment_install"
    if "环境" in text and ("引流" in text or workspace == "traffic"):
        return "traffic_environment_check"
    if "AI分析" in text or "AI 分析" in text:
        return "ai_workbench"
    if "私信" in text and "关键词" in text:
        return "message_keywords"
    if "私信" in text and "客户" in text and "多少" not in text:
        return "message_customers"
    if "引流" in text and ("记录" in text or "操作" in text):
        return "traffic_records_list"
    if "引流" in text and ("批次" in text or "运行" in text):
        return "traffic_runs_list"
    if "任务" in text and ("列表" in text or "最近" in text or "有哪些" in text):
        return "tasks_list"
    return ""


def _infer_metric(command: str) -> str:
    text = str(command or "")
    if "未私信" in text:
        return "unmessaged"
    if "私信" in text:
        return "dm_summary"
    if "竞品" in text and "客户" in text:
        return "overview"
    if "竞品" in text:
        return "competitors"
    if "客户" in text:
        return "target_customers"
    return "overview"


def _infer_lead_operation(command: str) -> str:
    text = str(command or "")
    if "私信" in text and re.search(r"(还未|未|待).{0,4}私信", text):
        return "message"
    if "竞品" in text and "客户" not in text:
        return "competitors"
    if "客户" in text and re.search(r"(先不|不要|不需要|无需).{0,6}私信", text):
        return "customers"
    if "客户" in text and "私信" not in text:
        return "customers"
    return "full"


def _default_title(action: str) -> str:
    return {
        "lead_auto": "AI自动拓客计划",
        "traffic_auto": "AI自动引流计划",
        "answer_stats": "数据统计查询",
        "query_database": "数据库只读查询",
    }.get(action, "未识别指令")


def _default_summary(action: str) -> str:
    return {
        "lead_auto": "将创建抖音拓客自动化批次。",
        "traffic_auto": "将创建抖音引流计划并启动批次。",
        "answer_stats": "将读取本地数据并汇总统计。",
        "query_database": "将执行安全的只读数据库查询。",
    }.get(action, "需要更明确的指令。")


def _plan_answer(plan: dict[str, Any]) -> str:
    action = plan.get("action")
    if action == "lead_auto":
        operation_label = {
            "full": "找竞品、找客户并自动私信",
            "competitors": "只找竞品",
            "customers": "找客户但不私信",
            "message": "私信当前未私信客户",
        }.get(str(plan.get("lead_operation")), "拓客")
        return f"我会执行：{operation_label}。关键词由AI从你的指令和产品关键词中决定，确认后会创建批次。"
    if action == "traffic_auto":
        return "我会创建抖音引流计划并立即启动执行批次。"
    return "确认后执行。"


def _stat_answer(metric: str, values: dict[str, int]) -> str:
    if metric == "competitors":
        return f"当前已有 {values['competitors']} 个竞品账户。"
    if metric == "target_customers":
        return f"当前已有 {values['target_customers']} 个目标客户。"
    if metric == "unmessaged":
        return f"当前还有 {values['unmessaged']} 个目标客户处于未私信状态。"
    if metric == "dm_summary":
        return f"AI私信批次累计成功私信 {values['dm_success']} 个客户，失败 {values['dm_failed']} 个，共 {values['dm_batches']} 个私信批次。"
    return (
        f"当前有 {values['competitors']} 个竞品账户、{values['target_customers']} 个目标客户、"
        f"{values['unmessaged']} 个未私信客户；AI私信累计成功 {values['dm_success']} 个。"
    )
