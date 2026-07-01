from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app import database
from app.services.importer import COMMENT_TABLES, CONTENT_TABLES, CREATOR_TABLES


LIBRARY_LABELS = {
    "contents": "内容库",
    "comments": "评论库",
    "competitor_candidates": "竞品账号候选库",
    "competitors": "竞品账号库",
    "lead_customers": "线索客户库",
    "target_customers": "目标客户库",
}

PLATFORM_LABELS = {"dy": "抖音", "xhs": "小红书", "ks": "快手"}
PROFILE_ENRICHMENT_PLATFORMS = {"dy", "xhs"}
DIAGNOSTIC_FIELDS = {
    "content": [
        ("id", "内容ID"),
        ("author_id", "作者ID"),
        ("nickname", "作者昵称"),
        ("url", "内容链接"),
        ("keyword", "来源关键词"),
        ("signature", "主页简介"),
    ],
    "comment": [
        ("id", "评论ID"),
        ("content_id", "所属内容ID"),
        ("author_id", "评论者ID"),
        ("nickname", "评论者昵称"),
        ("body", "评论内容"),
        ("created", "评论时间"),
    ],
    "creator": [
        ("id", "账号ID"),
        ("nickname", "账号昵称"),
        ("signature", "主页简介"),
        ("fans", "粉丝数"),
    ],
}


def get_settings() -> dict[str, Any]:
    with database.connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
    result = {row["key"]: row["value"] for row in rows}
    for key in (
        "auto_analyze_competitors",
        "auto_delete_non_competitors",
        "auto_analyze_leads",
        "auto_delete_non_customers",
        "headless",
    ):
        result[key] = result.get(key, "false") == "true"
    try:
        result["icp_profile"] = json.loads(result.get("icp_profile", "{}"))
    except json.JSONDecodeError:
        result["icp_profile"] = {}
    for key in ("content_cutoff_days", "comment_cutoff_days", "ai_analysis_concurrency", "unreplied_reminder_days"):
        try:
            result[key] = int(result.get(key, 0) or 0)
        except (TypeError, ValueError):
            result[key] = 0
    try:
        result["douyin_detail_sleep_seconds"] = float(result.get("douyin_detail_sleep_seconds", 2) or 0)
    except (TypeError, ValueError):
        result["douyin_detail_sleep_seconds"] = 2
    return result


def update_settings(values: dict[str, Any]) -> dict[str, Any]:
    with database.connect() as conn:
        for key, value in values.items():
            if key == "icp_profile":
                value = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, bool):
                value = "true" if value else "false"
            database.set_setting(conn, key, value)
    return get_settings()


def environment_check() -> dict[str, Any]:
    settings = get_settings()
    media_path = Path(str(settings.get("media_crawler_path", "")).strip().strip('"').strip("'"))
    raw_db = Path(str(settings.get("media_crawler_db_path", "")).strip().strip('"').strip("'"))
    raw_db_exists = raw_db.exists()
    return {
        "project_db": {"path": str(database.get_db_path()), "ok": database.get_db_path().exists()},
        "media_crawler_path": {"path": str(media_path), "ok": media_path.exists()},
        "media_crawler_db": {"path": str(raw_db), "ok": raw_db_exists},
        "ai_config": {
            "ok": bool(settings.get("ai_base_url") and settings.get("ai_api_key") and settings.get("ai_model")),
            "base_url": settings.get("ai_base_url", ""),
            "model": settings.get("ai_model", ""),
        },
        "project_quality": _project_quality(),
        "platform_diagnostics": _platform_diagnostics(raw_db) if raw_db_exists else [],
        "platform_capabilities": platform_capabilities(),
    }


def platform_capabilities() -> list[dict[str, Any]]:
    """Describe platform field limits before users start a crawl task."""
    return [_platform_capability(platform) for platform in PLATFORM_LABELS]


def _platform_capability(platform: str) -> dict[str, Any]:
    content_mapping = CONTENT_TABLES[platform]
    comment_mapping = COMMENT_TABLES[platform]
    creator_mapping = CREATOR_TABLES.get(platform, {})
    profile_supported = platform in PROFILE_ENRICHMENT_PLATFORMS and bool(creator_mapping)
    return {
        "platform": platform,
        "label": PLATFORM_LABELS[platform],
        "profile_enrichment_supported": profile_supported,
        "warnings": _platform_capability_warnings(platform),
        "fields": {
            "content_signature": _field_capability(
                content_mapping,
                "signature",
                "内容作者主页简介",
                status="partial" if platform == "dy" else "unsupported",
                note="抖音搜索结果有 user_signature 字段，但真实采集时可能为空。"
                if platform == "dy"
                else "该平台内容表没有作者主页简介映射。",
            ),
            "comment_signature": _field_capability(
                comment_mapping,
                "signature",
                "评论者主页简介",
                status="partial" if platform == "dy" else "unsupported",
                note="只有原始评论表 user_signature 非空时才会导入。"
                if platform == "dy"
                else "该平台评论表没有评论者主页简介映射。",
            ),
            "creator_signature": _field_capability(
                creator_mapping,
                "signature",
                "账号主页简介补资料",
                status="supported" if profile_supported else "unsupported",
                note="补资料任务会从 creator 表导入主页简介。"
                if profile_supported
                else "MediaCrawler SQLite 当前没有该平台 creator 主页资料表。",
            ),
            "creator_fans": _field_capability(
                creator_mapping,
                "fans",
                "粉丝数补资料",
                status="supported" if profile_supported else "unsupported",
                note="补资料任务会从 creator 表导入粉丝数。"
                if profile_supported
                else "MediaCrawler SQLite 当前没有该平台 creator 粉丝数字段。",
            ),
        },
        "modes": {mode: _mode_capability(mode, profile_supported) for mode in (
            "competitor_discovery",
            "competitor_crawl",
            "demand_content",
            "own_account",
        )},
    }


def _field_capability(
    mapping: dict[str, str],
    key: str,
    label: str,
    status: str,
    note: str,
) -> dict[str, Any]:
    column = mapping.get(key, "")
    supported = bool(column) and status != "unsupported"
    return {
        "label": label,
        "table": mapping.get("table", ""),
        "column": column,
        "supported": supported,
        "status": status if supported else "unsupported",
        "status_label": _field_status_label(status if supported else "unsupported"),
        "note": note,
    }


def _field_status_label(status: str) -> str:
    return {
        "supported": "可导入",
        "partial": "可能为空",
        "unsupported": "无字段",
    }.get(status, status)


def _platform_capability_warnings(platform: str) -> list[str]:
    if platform == "dy":
        return [
            "关键词搜索常见结果会有作者账号和主页链接，但主页简介 user_signature 可能为空。",
            "缺主页简介时请对账号执行补资料，系统会用 dy_creator.desc 回写并复判竞品关键词。",
        ]
    if platform == "xhs":
        return [
            "内容和评论表没有主页简介映射，主页简介依赖 xhs_creator.desc 补资料。",
            "小红书线索判断应优先看内容、评论和昵称，不要假设评论者简介可用。",
        ]
    return [
        "快手当前没有 creator 主页资料表，主页简介和粉丝数不能通过补资料回填。",
        "快手候选筛选只能依赖昵称、内容和评论证据，不会伪造主页简介。",
    ]


def _mode_capability(mode: str, profile_supported: bool) -> dict[str, Any]:
    base = {
        "mode": mode,
        "label": {
            "competitor_discovery": "竞品账号采集",
            "competitor_crawl": "竞品账号爬取",
            "demand_content": "找需求内容",
            "own_account": "自家账号互动",
        }[mode],
        "crawler_type": "search" if mode in ("competitor_discovery", "demand_content") else "creator/detail",
        "required_input": "关键词" if mode in ("competitor_discovery", "demand_content") else "创作者主页/ID或指定内容ID/链接",
        "comments_default": mode in ("competitor_crawl", "own_account"),
        "expected_outputs": _mode_expected_outputs(mode),
        "warnings": [],
    }
    warnings = list(base["warnings"])
    if mode == "competitor_discovery":
        warnings.append("候选规则只使用作者昵称和已导入主页简介；简介为空时可能漏掉只在简介命中的账号。")
        warnings.append("该平台支持补资料，补齐简介后会自动复判竞品关键词。" if profile_supported else "该平台不支持补资料，后续只能依赖昵称和内容证据判断。")
    elif mode in ("competitor_crawl", "own_account"):
        warnings.append("评论入库取决于评论开关、登录状态和原始评论表是否真的产生新行。")
        warnings.append("线索客户来自评论作者；如果评论表为空，任务成功也不会产生线索。")
    else:
        warnings.append("该模式优先分析内容中的需求、吐槽和讨论，不依赖账号主页简介作为主要证据。")
        if not profile_supported:
            warnings.append("该平台无法补齐作者主页简介，AI判断时应降低对主页资料的依赖。")
    return {**base, "warnings": warnings}


def _mode_expected_outputs(mode: str) -> list[str]:
    if mode == "competitor_discovery":
        return ["内容", "作者账号", "竞品候选"]
    if mode == "competitor_crawl":
        return ["竞品内容", "评论", "线索客户"]
    if mode == "demand_content":
        return ["需求内容", "作者账号", "目标客户候选"]
    return ["自家内容", "评论", "线索客户"]


def _project_quality() -> dict[str, Any]:
    with database.connect() as conn:
        accounts_total = _scalar_count(conn, "SELECT COUNT(*) AS count FROM user_accounts")
        contents_total = _scalar_count(conn, "SELECT COUNT(*) AS count FROM contents")
        comments_total = _scalar_count(conn, "SELECT COUNT(*) AS count FROM comments")
        leads_total = _scalar_count(conn, "SELECT COUNT(*) AS count FROM lead_user_accounts")
        tasks_with_comments = _scalar_count(conn, "SELECT COUNT(*) AS count FROM crawl_jobs WHERE collect_comments = 1")
        active_lead_sources = _scalar_count(conn, "SELECT COUNT(DISTINCT lead_account_id) AS count FROM lead_sources WHERE active = 1")

        sections = [
            {
                "key": "accounts",
                "label": "账号",
                "total": accounts_total,
                "fields": [
                    _project_field(conn, "昵称", accounts_total, "user_accounts", "COALESCE(nickname, '') <> ''"),
                    _project_field(conn, "主页链接", accounts_total, "user_accounts", "COALESCE(profile_url, '') <> ''"),
                    _project_field(conn, "主页简介", accounts_total, "user_accounts", "COALESCE(signature, '') <> ''"),
                    _project_field(conn, "粉丝数", accounts_total, "user_accounts", "fans IS NOT NULL"),
                ],
            },
            {
                "key": "contents",
                "label": "内容",
                "total": contents_total,
                "fields": [
                    _project_field(conn, "标题/文案", contents_total, "contents", "COALESCE(title, '') <> '' OR COALESCE(description, '') <> ''"),
                    _project_field(conn, "内容链接", contents_total, "contents", "COALESCE(content_url, '') <> ''"),
                    _project_field(conn, "来源关键词", contents_total, "contents", "COALESCE(source_keyword, '') <> ''"),
                    _project_field(conn, "作者账号", contents_total, "contents", "author_account_id IS NOT NULL"),
                ],
            },
            {
                "key": "comments",
                "label": "评论",
                "total": comments_total,
                "fields": [
                    _project_field(conn, "评论内容", comments_total, "comments", "COALESCE(body, '') <> ''"),
                    _project_field(conn, "所属内容", comments_total, "comments", "content_id IS NOT NULL"),
                    _project_field(conn, "评论账号", comments_total, "comments", "author_account_id IS NOT NULL"),
                ],
            },
            {
                "key": "leads",
                "label": "线索",
                "total": leads_total,
                "fields": [
                    {"label": "证据来源", "non_empty": active_lead_sources, "total": leads_total},
                    _project_field(conn, "跟进状态", leads_total, "lead_user_accounts", "COALESCE(follow_status, '') <> ''"),
                    _project_field(conn, "AI话术", leads_total, "lead_user_accounts", "COALESCE(script, '') <> ''"),
                ],
            },
        ]

    issues = _project_quality_issues(sections, tasks_with_comments)
    return {
        "summary": {
            "accounts": accounts_total,
            "contents": contents_total,
            "comments": comments_total,
            "leads": leads_total,
            "issues": len(issues),
            "status": "warning" if issues else "ok",
        },
        "sections": sections,
        "issues": issues,
    }


def _project_field(conn: sqlite3.Connection, label: str, total: int, table: str, condition: str) -> dict[str, Any]:
    return {
        "label": label,
        "non_empty": _scalar_count(conn, f"SELECT COUNT(*) AS count FROM {table} WHERE {condition}"),
        "total": total,
    }


def _project_quality_issues(sections: list[dict[str, Any]], tasks_with_comments: int) -> list[dict[str, str]]:
    section_map = {section["key"]: section for section in sections}
    field_map = {
        (section["key"], field["label"]): field
        for section in sections
        for field in section.get("fields", [])
    }
    issues: list[dict[str, str]] = []
    if int(section_map["contents"]["total"]) == 0:
        issues.append({
            "severity": "warning",
            "title": "项目库还没有内容",
            "detail": "采集任务成功不等于业务数据已入库；先检查任务日志和 MediaCrawler 原始表。",
        })
    signature = field_map[("accounts", "主页简介")]
    if int(section_map["accounts"]["total"]) > 0 and int(signature["non_empty"]) < int(signature["total"]):
        issues.append({
            "severity": "warning",
            "title": "账号主页简介缺失",
            "detail": f"{signature['total'] - signature['non_empty']} 个账号缺主页简介，会影响竞品关键词复判和 AI判断。",
        })
    keyword = field_map[("contents", "来源关键词")]
    if int(section_map["contents"]["total"]) > 0 and int(keyword["non_empty"]) == 0:
        issues.append({
            "severity": "danger",
            "title": "内容缺少来源关键词",
            "detail": "关键词任务如果没有 source_keyword，后续总览树和竞品候选来源会不清晰。",
        })
    content_author = field_map[("contents", "作者账号")]
    if int(section_map["contents"]["total"]) > 0 and int(content_author["non_empty"]) < int(content_author["total"]):
        issues.append({
            "severity": "danger",
            "title": "部分内容没有作者账号",
            "detail": "缺少作者账号会导致主页、竞品候选、线索归因无法串起来。",
        })
    if tasks_with_comments > 0 and int(section_map["comments"]["total"]) == 0:
        issues.append({
            "severity": "warning",
            "title": "任务要求采评论但评论库为空",
            "detail": "检查平台登录状态、内容ID/账号主页、评论开关和 MediaCrawler 原始评论表。",
        })
    lead_sources = field_map[("leads", "证据来源")]
    if int(section_map["leads"]["total"]) > 0 and int(lead_sources["non_empty"]) < int(lead_sources["total"]):
        issues.append({
            "severity": "danger",
            "title": "部分线索缺少证据来源",
            "detail": "缺证据的线索不能可靠追溯到内容或评论，删除和复盘都会受影响。",
        })
    return issues


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _platform_diagnostics(raw_db: Path) -> list[dict[str, Any]]:
    raw_conn = sqlite3.connect(raw_db)
    raw_conn.row_factory = sqlite3.Row
    try:
        return [_platform_diagnostic(raw_conn, platform) for platform in PLATFORM_LABELS]
    finally:
        raw_conn.close()


def _platform_diagnostic(raw_conn: sqlite3.Connection, platform: str) -> dict[str, Any]:
    content = _table_diagnostic(raw_conn, CONTENT_TABLES[platform], DIAGNOSTIC_FIELDS["content"])
    comment = _table_diagnostic(raw_conn, COMMENT_TABLES[platform], DIAGNOSTIC_FIELDS["comment"])
    creator_mapping = CREATOR_TABLES.get(platform)
    creator = (
        _table_diagnostic(raw_conn, creator_mapping, DIAGNOSTIC_FIELDS["creator"])
        if creator_mapping
        else {"supported": False, "exists": False, "row_count": 0, "fields": []}
    )
    warnings: list[str] = []
    if not content["exists"]:
        warnings.append(f"{PLATFORM_LABELS[platform]}内容表不存在，无法导入内容")
    if content["exists"] and content["row_count"] == 0:
        warnings.append(f"{PLATFORM_LABELS[platform]}内容表暂无原始行")
    if comment["exists"] and comment["row_count"] == 0:
        warnings.append(f"{PLATFORM_LABELS[platform]}评论表暂无原始行")
    if platform not in PROFILE_ENRICHMENT_PLATFORMS:
        warnings.append("MediaCrawler SQLite 当前不写入该平台 creator 主页资料，补资料不可用")
    elif not creator["exists"]:
        warnings.append(f"{PLATFORM_LABELS[platform]}creator 表不存在，主页简介和粉丝数无法补全")
    return {
        "platform": platform,
        "label": PLATFORM_LABELS[platform],
        "ok": bool(content["exists"]),
        "warnings": warnings,
        "tables": {
            "content": content,
            "comment": comment,
            "creator": creator,
        },
    }


def _table_diagnostic(
    raw_conn: sqlite3.Connection,
    mapping: dict[str, str],
    fields: list[tuple[str, str]],
) -> dict[str, Any]:
    table = mapping["table"]
    table_row = raw_conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if not table_row:
        return {"table": table, "supported": True, "exists": False, "row_count": 0, "fields": []}
    columns = {str(row["name"]) for row in raw_conn.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()}
    row_count = int(raw_conn.execute(f"SELECT COUNT(*) AS c FROM {_quote_identifier(table)}").fetchone()["c"])
    return {
        "table": table,
        "supported": True,
        "exists": True,
        "row_count": row_count,
        "fields": [_field_diagnostic(raw_conn, table, columns, mapping, key, label, row_count) for key, label in fields],
    }


def _field_diagnostic(
    raw_conn: sqlite3.Connection,
    table: str,
    columns: set[str],
    mapping: dict[str, str],
    key: str,
    label: str,
    row_count: int,
) -> dict[str, Any]:
    column = mapping.get(key, "")
    if not column:
        return {"key": key, "label": label, "column": "", "supported": False, "non_empty": 0, "row_count": row_count}
    if column not in columns:
        return {"key": key, "label": label, "column": column, "supported": False, "missing_column": True, "non_empty": 0, "row_count": row_count}
    non_empty = int(
        raw_conn.execute(
            f"SELECT COUNT(*) AS c FROM {_quote_identifier(table)} WHERE COALESCE(CAST({_quote_identifier(column)} AS TEXT), '') <> ''"
        ).fetchone()["c"]
    )
    return {"key": key, "label": label, "column": column, "supported": True, "non_empty": non_empty, "row_count": row_count}


def list_library(library: str, status: str = "", keyword: str = "") -> dict[str, Any]:
    if library == "contents":
        return _library_response(library, _list_contents(keyword))
    if library == "comments":
        return _library_response(library, _list_comments(keyword))
    if library == "competitor_candidates":
        return _library_response(library, _list_competitors(candidate=True, status=status, keyword=keyword))
    if library == "competitors":
        return _library_response(library, _list_competitors(candidate=False, status=status, keyword=keyword))
    if library == "lead_customers":
        return _library_response(library, _list_leads(target=False, status=status, keyword=keyword))
    if library == "target_customers":
        return _library_response(library, _list_leads(target=True, status=status, keyword=keyword))
    raise KeyError(library)


def workbench_actions() -> dict[str, Any]:
    """Return actionable queues that guide the next user step."""
    with database.connect() as conn:
        profile_needed = _scalar_count(
            conn,
            """
            SELECT COUNT(DISTINCT ua.id) AS count
            FROM user_accounts ua
            WHERE ua.platform IN ('dy', 'xhs')
              AND COALESCE(ua.signature, '') = ''
              AND (
                EXISTS (SELECT 1 FROM contents c WHERE c.author_account_id = ua.id)
                OR EXISTS (SELECT 1 FROM comments cm WHERE cm.author_account_id = ua.id)
                OR EXISTS (SELECT 1 FROM account_sources ac WHERE ac.account_id = ua.id AND ac.active = 1)
                OR EXISTS (SELECT 1 FROM lead_user_accounts lua WHERE lua.account_id = ua.id)
              )
            """,
        )
        competitor_pending = _scalar_count(
            conn,
            """
            SELECT COUNT(*) AS count
            FROM user_accounts
            WHERE account_role = 'competitor_candidate' AND competitor_status = '未分析'
            """,
        )
        leads_pending = _scalar_count(
            conn,
            """
            SELECT COUNT(*) AS count
            FROM lead_user_accounts
            WHERE hidden = 0 AND follow_status = '待筛选'
            """,
        )
        targets_pending = _scalar_count(
            conn,
            """
            SELECT COUNT(*) AS count
            FROM lead_user_accounts
            WHERE hidden = 0 AND follow_status IN ('未私信', '已私信', '未回复', '已回复', '未成交')
            """,
        )
        ai_failed = _scalar_count(conn, "SELECT COUNT(*) AS count FROM analysis_jobs WHERE status = 'failed'")

    queues = [
        {
            "key": "profile_enrichment",
            "title": "需补资料账号",
            "count": profile_needed,
            "priority": "warning",
            "view": "overview",
            "library": "",
            "status": "",
            "hint": "补齐主页简介和粉丝数后，竞品关键词会自动复判。",
            "action": "batch_profile_enrichment",
            "limit": min(profile_needed, 10),
            "action_label": "一键补资料" if profile_needed else "查看",
        },
        {
            "key": "competitors_to_analyze",
            "title": "竞品候选待分析",
            "count": competitor_pending,
            "priority": "primary",
            "view": "tables",
            "library": "competitor_candidates",
            "status": "未分析",
            "hint": "先用 AI 判断是否真竞品，再进入竞品库。",
            "action_label": "筛选竞品",
        },
        {
            "key": "leads_to_screen",
            "title": "线索待筛选",
            "count": leads_pending,
            "priority": "primary",
            "view": "tables",
            "library": "lead_customers",
            "status": "待筛选",
            "hint": "判断是否目标客户，并生成后续私信话术。",
            "action_label": "筛选线索",
        },
        {
            "key": "targets_to_follow",
            "title": "目标待跟进",
            "count": targets_pending,
            "priority": "success",
            "view": "tables",
            "library": "target_customers",
            "status": "",
            "hint": "把目标客户按白板状态继续推进。",
            "action_label": "进入跟进",
        },
        {
            "key": "ai_failed",
            "title": "AI失败待重试",
            "count": ai_failed,
            "priority": "danger",
            "view": "ai",
            "library": "",
            "status": "failed",
            "hint": "通常来自模型配置、网络或 JSON 解析失败。",
            "action_label": "查看失败",
        },
    ]
    actionable_total = sum(int(queue["count"]) for queue in queues)
    return {
        "queues": queues,
        "summary": {
            "total": actionable_total,
            "ready": sum(1 for queue in queues if int(queue["count"]) > 0),
        },
    }


def _scalar_count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row["count"] or 0) if row else 0


def _library_response(library: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"library": library, "label": LIBRARY_LABELS[library], "rows": rows}


def _keyword_clause(keyword: str, fields: list[str]) -> tuple[str, list[str]]:
    if not keyword:
        return "", []
    clause = " AND (" + " OR ".join([f"{field} LIKE ?" for field in fields]) + ")"
    params = [f"%{keyword}%" for _ in fields]
    return clause, params


def _list_contents(keyword: str) -> list[dict[str, Any]]:
    clause, params = _keyword_clause(keyword, ["c.title", "c.description", "ua.nickname", "c.source_keyword"])
    with database.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT c.*, ua.nickname AS author_nickname, ua.profile_url AS author_url, j.name AS task_name
            FROM contents c
            LEFT JOIN user_accounts ua ON ua.id = c.author_account_id
            LEFT JOIN crawl_jobs j ON j.id = c.task_id
            WHERE 1 = 1 {clause}
            ORDER BY c.updated_at DESC
            LIMIT 500
            """,
            params,
        ).fetchall()
    return database.rows_to_dicts(rows)


def _list_comments(keyword: str) -> list[dict[str, Any]]:
    clause, params = _keyword_clause(keyword, ["cm.body", "ua.nickname", "ct.title"])
    with database.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT cm.*, ua.nickname AS commenter_nickname, ua.profile_url AS commenter_url,
                   ct.title AS content_title, ct.content_url, j.name AS task_name
            FROM comments cm
            LEFT JOIN user_accounts ua ON ua.id = cm.author_account_id
            LEFT JOIN contents ct ON ct.id = cm.content_id
            LEFT JOIN crawl_jobs j ON j.id = cm.task_id
            WHERE 1 = 1 {clause}
            ORDER BY cm.updated_at DESC
            LIMIT 500
            """,
            params,
        ).fetchall()
    return database.rows_to_dicts(rows)


def _list_competitors(candidate: bool, status: str, keyword: str) -> list[dict[str, Any]]:
    base_status = "ua.competitor_status != '竞品'" if candidate else "ua.competitor_status = '竞品'"
    status_clause = " AND ua.competitor_status = ?" if status else ""
    kw_clause, params = _keyword_clause(keyword, ["ua.nickname", "ua.signature", "ua.platform_user_id"])
    all_params: list[Any] = ([status] if status else []) + params
    with database.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT ua.*, COUNT(DISTINCT ac.content_id) AS content_count,
                   MAX(j.name) AS task_name, MAX(j.id) AS task_id
            FROM user_accounts ua
            LEFT JOIN account_sources ac ON ac.account_id = ua.id AND ac.active = 1
            LEFT JOIN crawl_jobs j ON j.id = ac.task_id
            WHERE {base_status} AND (ua.account_role IN ('competitor_candidate', 'competitor') OR ac.id IS NOT NULL)
            {status_clause} {kw_clause}
            GROUP BY ua.id
            ORDER BY ua.updated_at DESC
            LIMIT 500
            """,
            all_params,
        ).fetchall()
    return database.rows_to_dicts(rows)


def _list_leads(target: bool, status: str, keyword: str) -> list[dict[str, Any]]:
    non_target_statuses = "('待筛选', '无需跟进', '非客户')"
    target_clause = f"lua.follow_status NOT IN {non_target_statuses} AND lua.hidden = 0" if target else f"lua.follow_status IN {non_target_statuses} AND lua.hidden = 0"
    status_clause = " AND lua.follow_status = ?" if status else ""
    kw_clause, params = _keyword_clause(keyword, ["ua.nickname", "ua.signature", "lua.reason", "lua.script"])
    all_params: list[Any] = ([status] if status else []) + params
    with database.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT lua.*, ua.platform, ua.nickname, ua.profile_url, ua.platform_user_id,
                   ua.signature, COUNT(DISTINCT ls.id) AS source_count,
                   GROUP_CONCAT(DISTINCT cm.body) AS comment_samples,
                   MAX(j.name) AS task_name, MAX(j.id) AS task_id
            FROM lead_user_accounts lua
            JOIN user_accounts ua ON ua.id = lua.account_id
            LEFT JOIN lead_sources ls ON ls.lead_account_id = lua.id AND ls.active = 1
            LEFT JOIN comments cm ON cm.id = ls.comment_id
            LEFT JOIN crawl_jobs j ON j.id = ls.task_id
            WHERE {target_clause} {status_clause} {kw_clause}
            GROUP BY lua.id
            ORDER BY lua.updated_at DESC
            LIMIT 500
            """,
            all_params,
        ).fetchall()
    return database.rows_to_dicts(rows)


def update_library_row(library: str, row_id: int, values: dict[str, Any]) -> dict[str, Any]:
    allowed = _allowed_update_fields(library)
    updates = {key: value for key, value in values.items() if key in allowed}
    if not updates:
        return {"ok": True, "updated": 0}
    table = _update_table(library)
    assignments = ", ".join([f"{key} = ?" for key in updates])
    params = list(updates.values()) + [row_id]
    with database.connect() as conn:
        conn.execute(
            f"UPDATE {table} SET {assignments}, updated_at = datetime('now', 'localtime') WHERE id = ?",
            params,
        )
    return {"ok": True, "updated": len(updates)}


def _allowed_update_fields(library: str) -> set[str]:
    if library in ("competitor_candidates", "competitors"):
        return {"nickname", "signature", "profile_url", "fans", "competitor_status", "account_role"}
    if library in ("lead_customers", "target_customers"):
        return {"screening_status", "follow_status", "intention", "reason", "pain_points", "suggested_action", "script", "hidden"}
    if library == "contents":
        return {"title", "description", "content_url", "like_count", "comment_count", "source_keyword"}
    if library == "comments":
        return {"body", "like_count", "parent_comment_id"}
    return set()


def _update_table(library: str) -> str:
    if library in ("competitor_candidates", "competitors"):
        return "user_accounts"
    if library in ("lead_customers", "target_customers"):
        return "lead_user_accounts"
    return library


def overview_tree() -> list[dict[str, Any]]:
    with database.connect() as conn:
        platforms = conn.execute(
            """
            SELECT c.platform AS platform,
                   COUNT(DISTINCT CASE WHEN ua.competitor_status = '竞品' THEN ua.id END) AS competitors,
                   COUNT(DISTINCT lua.id) AS customers,
                   COUNT(DISTINCT c.id) AS contents,
                   COUNT(DISTINCT cm.id) AS comments,
                   MAX(c.updated_at) AS latest
            FROM contents c
            LEFT JOIN user_accounts ua ON ua.id = c.author_account_id
            LEFT JOIN comments cm ON cm.content_id = c.id
            LEFT JOIN lead_sources ls ON ls.content_id = c.id
            LEFT JOIN lead_user_accounts lua ON lua.id = ls.lead_account_id
            GROUP BY c.platform
            """,
        ).fetchall()
        result: list[dict[str, Any]] = []
        for platform in platforms:
            platform_key = platform["platform"]
            keywords = conn.execute(
                """
                SELECT c.source_keyword AS keyword,
                       COUNT(DISTINCT CASE WHEN ua.competitor_status = '竞品' THEN ua.id END) AS competitors,
                       COUNT(DISTINCT c.id) AS contents,
                       COUNT(DISTINCT cm.id) AS comments,
                       COUNT(DISTINCT lua.id) AS customers,
                       MAX(c.updated_at) AS latest
                FROM contents c
                LEFT JOIN user_accounts ua ON ua.id = c.author_account_id
                LEFT JOIN comments cm ON cm.content_id = c.id
                LEFT JOIN lead_sources ls ON ls.content_id = c.id
                LEFT JOIN lead_user_accounts lua ON lua.id = ls.lead_account_id
                WHERE c.platform = ? AND NULLIF(c.source_keyword, '') IS NOT NULL
                GROUP BY c.source_keyword
                """,
                (platform_key,),
            ).fetchall()
            result.append(
                {
                    "id": f"platform:{platform_key}",
                    "label": platform_key,
                    "kind": "platform",
                    "metrics": database.row_to_dict(platform),
                    "children": [_keyword_node(conn, platform_key, row["keyword"], database.row_to_dict(row)) for row in keywords],
                }
            )
    return result


def _keyword_node(conn, platform: str, keyword: str, metrics: dict[str, Any]) -> dict[str, Any]:
    # 返回完整账号列表，由前端逐层分页，避免后端静默截断业务数据。
    accounts = conn.execute(
        """
        SELECT ua.id, ua.platform, ua.platform_user_id, ua.sec_uid,
               ua.nickname, ua.profile_url, ua.fans, ua.signature,
               ua.account_role, ua.competitor_status, ua.competitor_reason,
               ua.content_total_count,
               ua.raw_payload,
               COUNT(DISTINCT c.id) AS content_count,
               COUNT(DISTINCT cm.id) AS comment_count,
               COUNT(DISTINCT lua.id) AS customer_count,
               MAX(c.updated_at) AS latest
        FROM contents c
        JOIN user_accounts ua ON ua.id = c.author_account_id
        LEFT JOIN comments cm ON cm.content_id = c.id
        LEFT JOIN lead_sources ls ON ls.content_id = c.id
        LEFT JOIN lead_user_accounts lua ON lua.id = ls.lead_account_id
        WHERE c.platform = ? AND c.source_keyword = ?
        GROUP BY ua.id
        ORDER BY latest DESC, ua.id DESC
        """,
        (platform, keyword),
    ).fetchall()
    analysis_states = _overview_account_analysis_states(conn, platform, accounts)
    node_metrics = dict(metrics)
    node_metrics["platform"] = platform
    node_metrics["keyword"] = keyword
    return {
        "id": f"keyword:{platform}:{keyword}",
        "label": keyword,
        "kind": "keyword",
        "metrics": node_metrics,
        "children": [_overview_account_node(conn, row, analysis_states) for row in accounts],
    }


def _overview_account_node(conn: sqlite3.Connection, row: sqlite3.Row, analysis_states: dict[int, str]) -> dict[str, Any]:
    metrics = database.row_to_dict(row)
    account_id = int(row["id"])
    children = _overview_customer_nodes_for_account(conn, account_id)
    metrics.update(_overview_account_totals(conn, account_id))
    base_status = str(metrics.get("competitor_status") or "未分析")
    metrics["competitor_display_status"] = analysis_states.get(account_id, base_status) if base_status == "未分析" else base_status
    metrics["content_total_count"] = metrics.get("content_total_count") or _overview_content_total_count(metrics.get("raw_payload"))
    metrics["crawled_content_count"] = int(metrics.get("content_count") or 0)
    return {
        "id": f"account:{account_id}",
        "label": row["nickname"] or f"账号 {account_id}",
        "kind": "account",
        "metrics": metrics,
        "children": children,
    }


def _overview_account_totals(conn: sqlite3.Connection, account_id: int) -> dict[str, Any]:
    # Account cards show account-wide totals, not only the current keyword branch.
    row = conn.execute(
        """
        SELECT
            (
                SELECT COUNT(DISTINCT c.id)
                FROM contents c
                WHERE c.author_account_id = ?
            ) AS content_count,
            (
                SELECT COUNT(DISTINCT cm.id)
                FROM contents c
                JOIN comments cm ON cm.content_id = c.id
                WHERE c.author_account_id = ?
            ) AS comment_count,
            (
                SELECT COUNT(DISTINCT lua.id)
                FROM lead_sources ls
                JOIN lead_user_accounts lua ON lua.id = ls.lead_account_id
                LEFT JOIN contents c ON c.id = ls.content_id
                WHERE ls.active = 1
                  AND lua.hidden = 0
                  AND (ls.source_account_id = ? OR c.author_account_id = ?)
            ) AS customer_count,
            (
                SELECT COUNT(DISTINCT lua.id)
                FROM lead_sources ls
                JOIN lead_user_accounts lua ON lua.id = ls.lead_account_id
                LEFT JOIN contents c ON c.id = ls.content_id
                WHERE ls.active = 1
                  AND lua.hidden = 0
                  AND (ls.source_account_id = ? OR c.author_account_id = ?)
                  AND (
                    lua.screening_status = '目标客户'
                    OR lua.follow_status IN ('未私信', '已私信', '未回复', '已回复', '未成交', '已成交')
                  )
            ) AS target_customer_count,
            (
                SELECT COUNT(DISTINCT lua.id)
                FROM lead_sources ls
                JOIN lead_user_accounts lua ON lua.id = ls.lead_account_id
                LEFT JOIN contents c ON c.id = ls.content_id
                WHERE ls.active = 1
                  AND lua.hidden = 0
                  AND (ls.source_account_id = ? OR c.author_account_id = ?)
                  AND (
                    lua.screening_status = '非客户'
                    OR lua.follow_status IN ('非客户', '无需跟进')
                  )
            ) AS non_customer_count,
            (
                SELECT MAX(ts)
                FROM (
                    SELECT c.updated_at AS ts
                    FROM contents c
                    WHERE c.author_account_id = ?
                    UNION ALL
                    SELECT cm.updated_at AS ts
                    FROM comments cm
                    JOIN contents c ON c.id = cm.content_id
                    WHERE c.author_account_id = ?
                    UNION ALL
                    SELECT ls.created_at AS ts
                    FROM lead_sources ls
                    LEFT JOIN contents c ON c.id = ls.content_id
                    WHERE ls.active = 1
                      AND (ls.source_account_id = ? OR c.author_account_id = ?)
                )
            ) AS latest
        """,
        (
            account_id,
            account_id,
            account_id,
            account_id,
            account_id,
            account_id,
            account_id,
            account_id,
            account_id,
            account_id,
            account_id,
            account_id,
        ),
    ).fetchone()
    return database.row_to_dict(row) if row else {
        "content_count": 0,
        "comment_count": 0,
        "customer_count": 0,
        "target_customer_count": 0,
        "non_customer_count": 0,
        "latest": "",
    }


def _overview_customer_nodes_for_account(conn: sqlite3.Connection, source_account_id: int) -> list[dict[str, Any]]:
    # 客户节点同样返回完整集合，具体展示页码交给总览树前端控制。
    rows = conn.execute(
        """
        SELECT lua.id AS lead_id, lua.screening_status, lua.follow_status,
               lua.intention, lua.reason, lua.suggested_action, lua.script,
               lua.hidden, lua.updated_at,
               ua.id AS account_id, ua.platform, ua.nickname, ua.profile_url,
               ua.signature, COUNT(DISTINCT ls.id) AS source_count,
               GROUP_CONCAT(DISTINCT TRIM(
                   COALESCE(NULLIF(ct.title, ''), '') ||
                   CASE
                       WHEN COALESCE(NULLIF(ct.description, ''), '') <> ''
                            AND COALESCE(NULLIF(ct.description, ''), '') <> COALESCE(NULLIF(ct.title, ''), '')
                       THEN ' ' || ct.description
                       ELSE ''
                   END
               )) AS content_samples,
               GROUP_CONCAT(DISTINCT NULLIF(ct.content_url, '')) AS content_urls,
               GROUP_CONCAT(DISTINCT cm.body) AS comment_samples,
               MAX(ls.created_at) AS latest
        FROM lead_sources ls
        JOIN lead_user_accounts lua ON lua.id = ls.lead_account_id
        JOIN user_accounts ua ON ua.id = lua.account_id
        LEFT JOIN contents ct ON ct.id = ls.content_id
        LEFT JOIN comments cm ON cm.id = ls.comment_id
        WHERE ls.active = 1
          AND lua.hidden = 0
          AND (
            ls.source_account_id = ?
            OR ls.content_id IN (SELECT id FROM contents WHERE author_account_id = ?)
        )
        GROUP BY lua.id
        ORDER BY lua.updated_at DESC, lua.id DESC
        """,
        (source_account_id, source_account_id),
    ).fetchall()
    analysis_states = _overview_customer_analysis_states(conn, [int(row["lead_id"]) for row in rows])
    nodes: list[dict[str, Any]] = []
    for row in rows:
        metrics = database.row_to_dict(row)
        lead_id = int(row["lead_id"])
        metrics["id"] = lead_id
        metrics["source_account_id"] = source_account_id
        metrics["ai_analysis_status"] = analysis_states.get(lead_id) or _overview_customer_analysis_status(metrics)
        nodes.append(
            {
                "id": f"customer:{lead_id}",
                "label": row["nickname"] or f"客户 {lead_id}",
                "kind": "customer",
                "metrics": metrics,
                "children": [],
            }
        )
    return nodes


def _overview_customer_analysis_status(metrics: dict[str, Any]) -> str:
    if metrics.get("reason") or metrics.get("script"):
        return "已分析"
    if str(metrics.get("screening_status") or "") in ("目标客户", "非客户"):
        return "已分析"
    if str(metrics.get("follow_status") or "") in ("未私信", "已私信", "未回复", "已回复", "未成交", "已成交", "非客户", "无需跟进"):
        return "已分析"
    return "未分析"


def _overview_customer_analysis_states(conn: sqlite3.Connection, lead_ids: list[int]) -> dict[int, str]:
    if not lead_ids:
        return {}
    states: dict[int, str] = {}
    for index in range(0, len(lead_ids), 800):
        chunk = lead_ids[index:index + 800]
        placeholders = ",".join(["?"] * len(chunk))
        rows = conn.execute(
            f"""
            SELECT target_id, status
            FROM analysis_jobs
            WHERE target_type = 'lead'
              AND status IN ('pending', 'running')
              AND target_id IN ({placeholders})
            ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, updated_at DESC
            """,
            chunk,
        ).fetchall()
        for job in rows:
            lead_id = int(job["target_id"])
            status = "正在分析" if job["status"] == "running" else "排队分析"
            if states.get(lead_id) != "正在分析":
                states[lead_id] = status
    return states


def _overview_content_total_count(raw_payload: Any) -> int | None:
    if isinstance(raw_payload, str):
        try:
            raw_payload = json.loads(raw_payload or "{}")
        except json.JSONDecodeError:
            raw_payload = {}
    if not isinstance(raw_payload, dict):
        return None
    for key in ("videos_count", "notes_count", "content_count", "contents_count", "aweme_count"):
        value = raw_payload.get(key)
        if value in ("", None):
            continue
        try:
            return int(float(str(value).replace(",", "")))
        except ValueError:
            continue
    return None


def _overview_account_analysis_states(conn: sqlite3.Connection, platform: str, rows: list[sqlite3.Row]) -> dict[int, str]:
    # 总览树只展示派生进度，不改写账号真实竞品结论。
    account_ids = [int(row["id"]) for row in rows]
    if not account_ids:
        return {}

    states: dict[int, str] = {}
    placeholders = ",".join(["?"] * len(account_ids))
    job_rows = conn.execute(
        f"""
        SELECT target_id, status
        FROM analysis_jobs
        WHERE target_type = 'competitor'
          AND status IN ('pending', 'running')
          AND target_id IN ({placeholders})
        ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, updated_at DESC
        """,
        account_ids,
    ).fetchall()
    for job in job_rows:
        account_id = int(job["target_id"])
        status = "正在分析" if job["status"] == "running" else "排队分析"
        if states.get(account_id) != "正在分析":
            states[account_id] = status

    identifiers: dict[str, set[int]] = {}
    for row in rows:
        account_id = int(row["id"])
        if str(row["competitor_status"] or "未分析") != "未分析":
            continue
        for value in (row["profile_url"], row["platform_user_id"], row["sec_uid"]):
            key = str(value or "").strip()
            if key:
                identifiers.setdefault(key, set()).add(account_id)
    if not identifiers:
        return states

    task_rows = conn.execute(
        """
        SELECT creator_id
        FROM crawl_jobs
        WHERE mode = 'account_analysis'
          AND status IN ('pending', 'running')
          AND platform = ?
        """,
        (platform,),
    ).fetchall()
    for task in task_rows:
        for creator_id in str(task["creator_id"] or "").split(","):
            for account_id in identifiers.get(creator_id.strip(), set()):
                states.setdefault(account_id, "排队分析")
    return states


def overview_node(node_id: str) -> dict[str, Any]:
    kind, _, raw_id = node_id.partition(":")
    with database.connect() as conn:
        if kind == "account":
            row = conn.execute("SELECT * FROM user_accounts WHERE id = ?", (raw_id,)).fetchone()
            contents = conn.execute("SELECT * FROM contents WHERE author_account_id = ? LIMIT 20", (raw_id,)).fetchall()
            return {"node": database.row_to_dict(row), "contents": database.rows_to_dicts(contents)}
        if kind == "platform":
            return {"node": {"platform": raw_id}, "tables": list_library("contents", keyword="")}
    return {"node": {"id": node_id}}
