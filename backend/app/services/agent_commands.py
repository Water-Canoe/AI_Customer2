from __future__ import annotations

import json
import re
import threading
import base64
from typing import Any

from app import database, views
from app.schemas import (
    AgentCommandRequest,
    AgentRunCreate,
    AiBulkDelete,
    AiBatchCreate,
    AiJobCreate,
    BulkActionPreview,
    ClearDataRequest,
    CustomerAutoMessageRequest,
    MessageAutoBatchCreate,
    SettingsUpdate,
    TableUpdate,
    TaskCreate,
    TrafficPlanCreate,
    TrafficSettingsUpdate,
)
from app.services import (
    account_actions,
    agent_service,
    ai_service,
    bulk_actions,
    crawler_adapter,
    deletion,
    diagnostics,
    license_service,
    maintenance,
    message_workbench,
    ops_visibility,
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
    "health": False,
    "license_read": False,
    "license_update": True,
    "license_check": True,
    "traffic_license_read": False,
    "traffic_license_update": True,
    "traffic_license_check": True,
    "settings_read": False,
    "settings_update": True,
    "settings_env_check": False,
    "settings_clear_data": True,
    "task_preview": False,
    "task_detail": False,
    "task_diagnostics": False,
    "task_dedup_summary": False,
    "tasks_list": False,
    "failed_retry": True,
    "task_delete": True,
    "table_list": False,
    "table_update": True,
    "table_delete": True,
    "overview_tree": False,
    "overview_node": False,
    "overview_platform_delete": True,
    "overview_keyword_delete": True,
    "overview_keyword_analyze": True,
    "overview_keyword_find_customers": True,
    "overview_account_delete": True,
    "overview_customer_delete": True,
    "workbench_actions": False,
    "tombstone_summary": False,
    "tombstones_list": False,
    "message_keywords": False,
    "message_customers": False,
    "message_customer_detail": False,
    "message_customer_auto": True,
    "message_batches": False,
    "ai_workbench": False,
    "ai_jobs_list": False,
    "ai_delete_non_competitors": True,
    "ai_delete_non_customers": True,
    "ai_delete_non_targets": True,
    "platform_capabilities": False,
    "agent_runs_list": False,
    "agent_run_detail": False,
    "agent_run_events": False,
    "agent_run_cancel": True,
    "traffic_plans_list": False,
    "traffic_plan_update": True,
    "traffic_plan_delete": True,
    "traffic_plan_archive": True,
    "traffic_plan_restore": True,
    "traffic_run_detail": False,
    "traffic_run_logs": False,
    "traffic_run_archive": True,
    "traffic_run_restore": True,
    "traffic_run_delete": True,
    "traffic_records_clear": True,
    "traffic_settings_read": False,
    "traffic_settings_update": True,
    "traffic_material_image_save": True,
    "traffic_material_image_info": False,
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
    inferred_metric = _infer_metric(payload.command)
    inferred_operation = _infer_system_operation(payload.command, payload.workspace)
    if inferred_operation == "failed_retry":
        action = "system_action"
        normalized["operation"] = inferred_operation
        normalized["title"] = normalized.get("title") or "处理失败待查"
        normalized["summary"] = normalized.get("summary") or "失败待查包含采集失败任务和 AI 分析失败任务；确认后会重试可处理的失败项。"
    elif inferred_operation == "ai_delete_non_targets":
        action = "system_action"
        normalized["operation"] = inferred_operation
        normalized["title"] = normalized.get("title") or "删除非目标账号"
        normalized["summary"] = normalized.get("summary") or "删除已被判定为非竞品或非客户的数据。"
        params = normalized.get("params") if isinstance(normalized.get("params"), dict) else {}
        params["target_types"] = _infer_non_target_types(payload.command)
        normalized["params"] = params
    elif inferred_metric == "failures":
        action = "answer_stats"
    if action not in ALLOWED_ACTIONS:
        action = "system_action" if inferred_operation else _infer_action(payload.command, payload.workspace)
        if inferred_operation:
            normalized["operation"] = normalized.get("operation") or inferred_operation
    if action == "unknown":
        action = "system_action" if inferred_operation else _infer_action(payload.command, payload.workspace)
        if inferred_operation:
            normalized["operation"] = normalized.get("operation") or inferred_operation
    normalized["action"] = action
    normalized["title"] = str(normalized.get("title") or _default_title(action)).strip()
    normalized["summary"] = str(normalized.get("summary") or _default_summary(action)).strip()
    normalized["steps"] = _clean_steps(normalized.get("steps"), action)
    normalized["keywords"] = _clean_keywords(normalized.get("keywords", []))
    if action == "answer_stats":
        metric = str(normalized.get("metric") or inferred_metric).strip()
        if inferred_metric != "overview":
            metric = inferred_metric
        normalized["metric"] = metric if metric in {"overview", "competitors", "target_customers", "unmessaged", "dm_summary", "failures"} else "overview"
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


def _settings_update(params: dict[str, Any]) -> dict[str, Any]:
    return views.update_settings(SettingsUpdate(**params).values)


def _settings_clear_data(params: dict[str, Any]) -> dict[str, Any]:
    payload = ClearDataRequest(**params)
    return maintenance.clear_all_data(payload.confirm)


def _license_update(params: dict[str, Any]) -> dict[str, Any]:
    return license_service.update_license_code(_required_str(params, "license_code"))


def _license_check(params: dict[str, Any]) -> dict[str, Any]:
    value = str(params.get("license_code") or "").strip() or None
    return license_service.check_license(value)


def _traffic_license_update(params: dict[str, Any]) -> dict[str, Any]:
    return license_service.update_license_code_for("traffic", _required_str(params, "license_code"))


def _traffic_license_check(params: dict[str, Any]) -> dict[str, Any]:
    value = str(params.get("license_code") or "").strip() or None
    return license_service.check_license_for("traffic", value)


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


def _task_dedup_summary(params: dict[str, Any]) -> dict[str, Any]:
    return ops_visibility.task_dedup_summary(_required_str(params, "task_id"))


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


def _task_delete(params: dict[str, Any]) -> dict[str, Any]:
    return deletion.delete_task(_required_str(params, "task_id"))


def _table_list(params: dict[str, Any]) -> dict[str, Any]:
    return views.list_library(
        _required_str(params, "library"),
        status=str(params.get("status") or ""),
        keyword=str(params.get("keyword") or ""),
    )


def _table_update(params: dict[str, Any]) -> dict[str, Any]:
    payload = TableUpdate(values=dict(params.get("values") or {}))
    return views.update_library_row(_required_str(params, "library"), _required_int(params, "row_id"), payload.values)


def _table_delete(params: dict[str, Any]) -> dict[str, Any]:
    hard = params.get("hard")
    hard_value = None if hard is None else bool(hard)
    return deletion.delete_library_row(_required_str(params, "library"), _required_int(params, "row_id"), hard_value)


def _overview_keyword_analyze(params: dict[str, Any]) -> dict[str, Any]:
    license_service.ensure_authorized()
    result = account_actions.create_keyword_account_analysis_tasks(
        str(params.get("platform") or "dy"),
        _required_str(params, "keyword"),
    )
    if params.get("run_now", True) and result.get("task_ids"):
        account_ids = [int(item["account_id"]) for item in result.get("accounts", []) if item.get("account_id")]
        _background(account_actions.run_keyword_account_analysis, account_ids, str(result["task_ids"][0]))
    return result


def _overview_keyword_find_customers(params: dict[str, Any]) -> dict[str, Any]:
    license_service.ensure_authorized()
    result = account_actions.create_keyword_find_customer_task(
        str(params.get("platform") or "dy"),
        _required_str(params, "keyword"),
    )
    if params.get("run_now", True) and result.get("task_ids"):
        _background(crawler_adapter.run_tasks_serially, result["task_ids"])
    return result


def _overview_customer_delete(params: dict[str, Any]) -> dict[str, Any]:
    source_account_id = params.get("source_account_id")
    return deletion.delete_lead_customer(
        _required_int(params, "lead_id"),
        source_account_id=int(source_account_id) if source_account_id is not None else None,
        source="agent_system_action",
    )


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


def _ai_delete_non_targets(params: dict[str, Any]) -> dict[str, Any]:
    license_service.ensure_authorized()
    target_types = _normalize_non_target_types(params.get("target_types") or params.get("types"))
    result: dict[str, Any] = {"target_types": target_types}
    with database.connect() as conn:
        if "competitors" in target_types:
            competitor_ids = [
                int(row["id"])
                for row in conn.execute("SELECT id FROM user_accounts WHERE competitor_status = '非竞品'").fetchall()
            ]
        else:
            competitor_ids = []
        if "customers" in target_types:
            customer_ids = [
                int(row["id"])
                for row in conn.execute(
                    """
                    SELECT id
                    FROM lead_user_accounts
                    WHERE hidden = 0
                      AND (screening_status = '非客户' OR follow_status IN ('非客户', '无需跟进'))
                    """
                ).fetchall()
            ]
        else:
            customer_ids = []
    if "competitors" in target_types:
        result["non_competitors"] = ai_service.delete_workbench_non_competitors(competitor_ids)
    if "customers" in target_types:
        result["non_customers"] = ai_service.delete_workbench_non_customers(customer_ids)
    return result


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


def _message_customer_detail(params: dict[str, Any]) -> dict[str, Any]:
    return message_workbench.customer_detail(_required_int(params, "lead_id"))


def _message_customer_auto(params: dict[str, Any]) -> dict[str, Any]:
    import asyncio

    license_service.ensure_authorized()
    payload = CustomerAutoMessageRequest(**params)
    return asyncio.run(
        message_workbench.auto_message_customer(
            _required_int(params, "lead_id"),
            dry_run=payload.dry_run,
            timeout_seconds=payload.timeout_seconds,
        )
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


def _traffic_plan_update(params: dict[str, Any]) -> dict[str, Any]:
    values = dict(params.get("plan") or params.get("values") or {})
    return traffic_workbench.update_plan(_required_str(params, "plan_id"), TrafficPlanCreate(**values))


def _traffic_run_create(params: dict[str, Any]) -> dict[str, Any]:
    license_service.ensure_authorized_for("traffic")
    run = traffic_workbench.create_run(_required_str(params, "plan_id"))
    if params.get("run_now", True):
        _background(traffic_workbench.run_traffic_run, str(run["id"]))
    return run


def _traffic_run_stop(params: dict[str, Any]) -> dict[str, Any]:
    return traffic_workbench.stop_run(_required_str(params, "run_id"))


def _traffic_run_detail(params: dict[str, Any]) -> dict[str, Any]:
    run = traffic_workbench.get_run(_required_str(params, "run_id"))
    if not run:
        raise ValueError("引流批次不存在")
    return run


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


def _traffic_settings_update(params: dict[str, Any]) -> dict[str, Any]:
    return traffic_workbench.update_settings(TrafficSettingsUpdate(**params))


def _traffic_material_image_save(params: dict[str, Any]) -> dict[str, Any]:
    raw = _required_str(params, "content_base64")
    try:
        content = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValueError("content_base64不是有效的Base64内容") from exc
    return traffic_workbench.save_material_image(_required_str(params, "filename"), content)


def _traffic_material_image_info(params: dict[str, Any]) -> dict[str, Any]:
    name = _required_str(params, "name")
    path = traffic_workbench.material_image_path(name)
    return {"name": name, "path": str(path), "preview_url": f"/api/traffic/material-images/{name}"}


def _tombstones_list(params: dict[str, Any]) -> dict[str, Any]:
    return ops_visibility.list_tombstones(
        entity_type=str(params.get("entity_type") or ""),
        platform=str(params.get("platform") or ""),
        source=str(params.get("source") or ""),
        query=str(params.get("query") or ""),
        page=_bounded_int(params.get("page"), 1, 1, 10_000),
        page_size=_bounded_int(params.get("page_size"), 20, 1, 100),
    )


def _failed_overview(limit: int = 10) -> dict[str, Any]:
    with database.connect() as conn:
        failed_tasks = database.rows_to_dicts(
            conn.execute(
                """
                SELECT id, name, mode, platform, error, keywords, creator_id, specified_id, updated_at
                FROM crawl_jobs
                WHERE status = 'failed' AND archived = 0
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        )
        failed_ai_jobs = database.rows_to_dicts(
            conn.execute(
                """
                SELECT id, target_type, target_id, error, updated_at
                FROM analysis_jobs
                WHERE status = 'failed'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        )
        counts = {
            "failed_tasks": _scalar(conn, "SELECT COUNT(*) FROM crawl_jobs WHERE status = 'failed' AND archived = 0"),
            "failed_ai_jobs": _scalar(conn, "SELECT COUNT(*) FROM analysis_jobs WHERE status = 'failed'"),
        }
    counts["failures"] = counts["failed_tasks"] + counts["failed_ai_jobs"]
    return {"counts": counts, "failed_tasks": failed_tasks, "failed_ai_jobs": failed_ai_jobs}


def _task_retry_payload(task_id: str) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM crawl_jobs WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise ValueError(f"任务不存在：{task_id}")
    return {
        "name": str(row["name"] or ""),
        "mode": str(row["mode"] or ""),
        "platform": str(row["platform"] or "dy"),
        "login_type": str(row["login_type"] or "qrcode"),
        "keywords": str(row["keywords"] or ""),
        "specified_id": str(row["specified_id"] or ""),
        "creator_id": str(row["creator_id"] or ""),
        "content_count": int(row["content_count"] or 20),
        "comment_count": int(row["comment_count"] or 20),
        "collect_content": bool(row["collect_content"]),
        "collect_comments": bool(row["collect_comments"]),
        "collect_authors": bool(row["collect_authors"]),
        "collect_sub_comments": bool(row["collect_sub_comments"]),
        "max_concurrency": int(row["max_concurrency"] or 1),
        "tcp_mode": bool(row["tcp_mode"]),
        "headless": bool(row["headless"]),
        "execute_crawler": bool(row["execute_crawler"]),
    }


def _failed_retry(params: dict[str, Any]) -> dict[str, Any]:
    license_service.ensure_authorized()
    limit = _bounded_int(params.get("limit"), 10, 1, 50)
    overview = _failed_overview(limit)
    retried_ai_jobs: list[dict[str, Any]] = []
    created_tasks: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for job in overview["failed_ai_jobs"]:
        try:
            retried_ai_jobs.append(ai_service.retry_ai_job(str(job["id"])))
        except Exception as exc:
            errors.append({"type": "ai_job", "id": str(job["id"]), "error": str(exc)})

    for task in overview["failed_tasks"]:
        try:
            created_tasks.append(_task_create(_task_retry_payload(str(task["id"]))))
        except Exception as exc:
            errors.append({"type": "task", "id": str(task["id"]), "error": str(exc)})

    return {
        "overview": overview,
        "retried_ai_count": len(retried_ai_jobs),
        "created_task_count": len(created_tasks),
        "retried_ai_jobs": retried_ai_jobs,
        "created_tasks": created_tasks,
        "errors": errors,
    }


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
    "health": lambda _params: {"status": "ok"},
    "license_read": lambda _params: license_service.license_overview(),
    "license_update": _license_update,
    "license_check": _license_check,
    "traffic_license_read": lambda _params: license_service.license_overview_for("traffic"),
    "traffic_license_update": _traffic_license_update,
    "traffic_license_check": _traffic_license_check,
    "settings_read": _settings_redacted,
    "settings_update": _settings_update,
    "settings_env_check": lambda _params: views.environment_check(),
    "settings_clear_data": _settings_clear_data,
    "task_preview": _task_preview,
    "task_detail": _task_detail,
    "task_diagnostics": _task_diagnostics,
    "task_dedup_summary": _task_dedup_summary,
    "tasks_list": _tasks_list,
    "failed_retry": _failed_retry,
    "task_delete": _task_delete,
    "table_list": _table_list,
    "table_update": _table_update,
    "table_delete": _table_delete,
    "overview_tree": lambda _params: views.overview_tree(),
    "overview_node": lambda params: views.overview_node(_required_str(params, "node_id")),
    "overview_platform_delete": lambda params: deletion.delete_overview_platform(_required_str(params, "platform")),
    "overview_keyword_delete": lambda params: deletion.delete_overview_keyword(str(params.get("platform") or "dy"), _required_str(params, "keyword")),
    "overview_keyword_analyze": _overview_keyword_analyze,
    "overview_keyword_find_customers": _overview_keyword_find_customers,
    "overview_account_delete": lambda params: deletion.delete_overview_account(_required_int(params, "account_id")),
    "overview_customer_delete": _overview_customer_delete,
    "workbench_actions": lambda _params: views.workbench_actions(),
    "tombstone_summary": lambda _params: ops_visibility.tombstone_summary(),
    "tombstones_list": _tombstones_list,
    "message_keywords": lambda _params: message_workbench.list_keywords(),
    "message_customers": _message_customers,
    "message_customer_detail": _message_customer_detail,
    "message_customer_auto": _message_customer_auto,
    "message_batches": lambda params: message_workbench.list_auto_message_batches(str(params.get("batch_id") or "")),
    "ai_workbench": lambda _params: ai_service.ai_workbench(),
    "ai_jobs_list": lambda _params: ai_service.list_ai_jobs(),
    "ai_delete_non_competitors": lambda params: ai_service.delete_workbench_non_competitors(AiBulkDelete(**params).target_ids),
    "ai_delete_non_customers": lambda params: ai_service.delete_workbench_non_customers(AiBulkDelete(**params).target_ids),
    "ai_delete_non_targets": _ai_delete_non_targets,
    "platform_capabilities": lambda _params: views.platform_capabilities(),
    "agent_runs_list": lambda params: agent_service.list_runs(str(params.get("run_type") or "")),
    "agent_run_detail": lambda params: agent_service.get_run(_required_str(params, "run_id")) or {},
    "agent_run_events": lambda params: agent_service.list_events(_required_str(params, "run_id")),
    "agent_run_cancel": lambda params: agent_service.cancel_run(_required_str(params, "run_id")),
    "traffic_plans_list": lambda params: traffic_workbench.list_plans(bool(params.get("include_archived", False))),
    "traffic_plan_update": _traffic_plan_update,
    "traffic_plan_delete": lambda params: traffic_workbench.delete_plan(_required_str(params, "plan_id")),
    "traffic_plan_archive": lambda params: traffic_workbench.archive_plan(_required_str(params, "plan_id")),
    "traffic_plan_restore": lambda params: traffic_workbench.restore_plan(_required_str(params, "plan_id")),
    "traffic_run_detail": _traffic_run_detail,
    "traffic_run_logs": lambda params: traffic_workbench.list_logs(_required_str(params, "run_id")),
    "traffic_run_archive": lambda params: traffic_workbench.archive_run(_required_str(params, "run_id")),
    "traffic_run_restore": lambda params: traffic_workbench.restore_run(_required_str(params, "run_id")),
    "traffic_run_delete": lambda params: traffic_workbench.delete_run(_required_str(params, "run_id")),
    "traffic_records_clear": lambda _params: traffic_workbench.clear_records(),
    "traffic_settings_read": lambda _params: traffic_workbench.get_settings(),
    "traffic_settings_update": _traffic_settings_update,
    "traffic_material_image_save": _traffic_material_image_save,
    "traffic_material_image_info": _traffic_material_image_info,
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
            "failed_tasks": _scalar(conn, "SELECT COUNT(*) FROM crawl_jobs WHERE status = 'failed' AND archived = 0"),
            "failed_ai_jobs": _scalar(conn, "SELECT COUNT(*) FROM analysis_jobs WHERE status = 'failed'"),
        }
        values["failures"] = values["failed_tasks"] + values["failed_ai_jobs"]
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
        "traffic_material_image_save": "用Base64内容保存引流图片素材",
        "traffic_material_image_info": "查看引流图片素材预览信息",
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
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if operation == "failed_retry":
        return (
            f"已处理失败待查：重试 AI 失败任务 {int(data.get('retried_ai_count') or 0)} 个，"
            f"重新创建采集任务 {int(data.get('created_task_count') or 0)} 个，"
            f"失败 {len(data.get('errors') or [])} 个。"
        )
    if operation == "ai_delete_non_targets":
        competitors = data.get("non_competitors") if isinstance(data.get("non_competitors"), dict) else {}
        customers = data.get("non_customers") if isinstance(data.get("non_customers"), dict) else {}
        return (
            f"已删除非目标数据：非竞品账号 {int(competitors.get('deleted') or 0)} 个，"
            f"非客户 {int(customers.get('deleted') or 0)} 个。"
        )
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
    if _is_non_target_delete_command(text):
        return "system_action"
    if "失败待查" in text or ("失败" in text and ("待查" in text or "重试" in text)):
        return "answer_stats"
    if re.search(r"(多少|几个|检查|查看|统计|列出|查询|数据库)", text):
        return "answer_stats"
    if "引流" in text or workspace == "traffic":
        return "traffic_auto"
    if re.search(r"(竞品|客户|私信|拓客|寻找|找些)", text):
        return "lead_auto"
    return "unknown"


def _infer_system_operation(command: str, workspace: str) -> str:
    text = str(command or "")
    if _is_non_target_delete_command(text):
        return "ai_delete_non_targets"
    if "重试" in text and ("失败待查" in text or "失败" in text):
        return "failed_retry"
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
    if "失败待查" in text or ("失败" in text and "待查" in text):
        return "failures"
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


def _is_non_target_delete_command(text: str) -> bool:
    return bool(re.search(r"(删除|清理|移除|删掉|剔除)", text)) and ("非竞品" in text or "非客户" in text)


def _infer_non_target_types(command: str) -> list[str]:
    text = str(command or "")
    result: list[str] = []
    if "非竞品" in text:
        result.append("competitors")
    if "非客户" in text or "无需跟进" in text:
        result.append("customers")
    return result or ["competitors", "customers"]


def _normalize_non_target_types(values: Any) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[,，\s]+", values)
    if not isinstance(values, list):
        values = ["competitors", "customers"]
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item in {"competitor", "competitors", "non_competitors", "非竞品"} and "competitors" not in result:
            result.append("competitors")
        if item in {"customer", "customers", "non_customers", "非客户"} and "customers" not in result:
            result.append("customers")
    return result or ["competitors", "customers"]


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
    if action == "system_action" and plan.get("operation") == "failed_retry":
        return "失败待查=采集失败任务+AI分析失败任务。确认后我会重试 AI 失败任务，并按原参数重新创建采集失败任务。"
    if action == "system_action" and plan.get("operation") == "ai_delete_non_targets":
        types = set(plan.get("params", {}).get("target_types") or [])
        labels = []
        if "competitors" in types:
            labels.append("非竞品账号")
        if "customers" in types:
            labels.append("非客户")
        return f"确认后会删除所有已判定为{'和'.join(labels) or '非目标'}的数据；未被判定为非目标的数据会跳过。"
    return "确认后执行。"


def _stat_answer(metric: str, values: dict[str, int]) -> str:
    if metric == "failures":
        return (
            f"失败待查表示当前需要排查或重试的失败项：采集任务失败 {values['failed_tasks']} 个，"
            f"AI分析失败 {values['failed_ai_jobs']} 个，合计 {values['failures']} 个。"
        )
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
