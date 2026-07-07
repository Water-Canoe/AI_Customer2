from __future__ import annotations

import json
import re
from typing import Any

from app import database
from app.schemas import AgentCommandRequest, AgentRunCreate
from app.services import agent_service, ai_service, message_workbench


READABLE_TABLES = {
    "user_accounts",
    "contents",
    "comments",
    "account_sources",
    "lead_user_accounts",
    "lead_sources",
    "crawl_jobs",
    "ai_jobs",
    "message_batches",
    "message_batch_items",
    "agent_runs",
    "agent_run_events",
    "traffic_plans",
    "traffic_runs",
    "traffic_run_items",
}
MUTATING_ACTIONS = {"lead_auto", "traffic_auto"}
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
        "执行类动作只允许 lead_auto 或 traffic_auto；查询类动作只允许 answer_stats 或 query_database。"
    )
    user_prompt = (
        "请把用户指令解析成一个JSON计划。\n"
        "可选action：\n"
        "- answer_stats：回答常见统计问题，metric只能是 overview、competitors、target_customers、unmessaged、dm_summary。\n"
        "- query_database：读取数据库信息，sql必须是只读SELECT，并且只能查询给出的安全表。\n"
        "- lead_auto：抖音拓客执行，lead_operation只能是 full、competitors、customers、message；auto_dm表示是否自动私信。\n"
        "- traffic_auto：抖音引流执行。\n"
        "- unknown：无法理解时使用。\n"
        "输出字段建议：action、title、summary、metric、sql、keywords、lead_operation、auto_dm、dm_count、"
        "interval_min_seconds、interval_max_seconds、content_count、source_mode、source_value、actions、round_video_limit、steps。\n"
        "约束：用户说只找竞品时用 lead_operation=competitors；说找客户但先不私信时用 customers 且 auto_dm=false；"
        "说私信未私信客户时用 message；说找客户并私信时用 full 且 auto_dm=true。\n"
        f"当前页面：{payload.workspace}\n"
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
        action = _infer_action(payload.command, payload.workspace)
    normalized["action"] = action
    normalized["title"] = str(normalized.get("title") or _default_title(action)).strip()
    normalized["summary"] = str(normalized.get("summary") or _default_summary(action)).strip()
    normalized["steps"] = _clean_steps(normalized.get("steps"), action)
    normalized["keywords"] = _clean_keywords(normalized.get("keywords", []))
    if action == "answer_stats":
        metric = str(normalized.get("metric") or _infer_metric(payload.command)).strip()
        normalized["metric"] = metric if metric in {"overview", "competitors", "target_customers", "unmessaged", "dm_summary"} else "overview"
    if action == "lead_auto":
        normalized.update(_normalize_lead_plan(normalized, payload.command))
    if action == "traffic_auto":
        normalized.update(_normalize_traffic_plan(normalized))
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
