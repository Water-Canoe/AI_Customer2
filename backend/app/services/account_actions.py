from __future__ import annotations

import json
from typing import Any

import sqlite3
from fastapi import HTTPException

from app import database
from app.schemas import TaskCreate
from app.services import ai_service, crawler_adapter, deletion
from app.services import tombstones


ACCOUNT_ANALYSIS_CONTENT_COUNT = 5
STALE_AI_JOB_MINUTES = 30
TARGET_FOLLOW_STATUSES = {"未私信", "已私信", "未回复", "已回复", "未成交", "已成交"}
NON_CUSTOMER_FOLLOW_STATUSES = {"非客户", "无需跟进"}


def _account_analysis_content_count() -> int:
    with database.connect() as conn:
        value = database.get_setting(conn, "account_analysis_content_count", str(ACCOUNT_ANALYSIS_CONTENT_COUNT))
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = ACCOUNT_ANALYSIS_CONTENT_COUNT
    # 账号分析只需要少量素材，避免一次补采过多内容拖慢 AI 判断。
    return max(1, min(count, 50))


def create_account_analysis_task(account_id: int) -> dict[str, object]:
    return crawler_adapter.create_profile_enrichment_task(
        account_id,
        mode="account_analysis",
        name_prefix="账号分析",
        content_count=_account_analysis_content_count(),
    )


def _account_profile_identifier(account: dict[str, Any]) -> str:
    platform = str(account.get("platform") or "")
    profile_url = str(account.get("profile_url") or "").strip()
    sec_uid = str(account.get("sec_uid") or "").strip()
    platform_user_id = str(account.get("platform_user_id") or "").strip()
    if profile_url:
        return profile_url
    if platform == "dy" and sec_uid:
        return sec_uid
    return platform_user_id


def _creator_id_items(value: object) -> list[str]:
    # MediaCrawler creator 模式支持逗号分隔多个账号，这里保持同一拆分规则。
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _active_account_analysis_identifiers(conn: sqlite3.Connection, platform: str) -> set[str]:
    active_rows = conn.execute(
        """
        SELECT creator_id
        FROM crawl_jobs
        WHERE mode = 'account_analysis'
          AND status IN ('pending', 'running')
          AND platform = ?
        """,
        (platform,),
    ).fetchall()
    return {
        item.strip()
        for row in active_rows
        for item in str(row["creator_id"] or "").split(",")
        if item.strip()
    }


def _build_account_analysis_task(
    platform: str,
    rows: list[sqlite3.Row],
    label: str,
    log_message: str,
    skipped: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    skipped = skipped or []
    with database.connect() as conn:
        login_type = database.get_setting(conn, "login_type", "qrcode")
        headless = database.get_setting(conn, "headless", "false") == "true"
        active_identifiers = _active_account_analysis_identifiers(conn, platform)

    accounts: list[dict[str, Any]] = []
    for row in rows:
        account = database.row_to_dict(row)
        identifier = _account_profile_identifier(account)
        if not identifier:
            skipped.append({"account_id": int(row["id"]), "nickname": row["nickname"], "reason": "账号缺少主页链接或平台ID"})
            continue
        if identifier in active_identifiers:
            skipped.append({"account_id": int(row["id"]), "nickname": row["nickname"], "reason": "已有账号分析任务正在等待或运行"})
            continue
        accounts.append(
            {
                "account_id": int(row["id"]),
                "nickname": row["nickname"],
                "creator_id": identifier,
            }
        )

    if not accounts:
        return {
            "ok": True,
            "platform": platform,
            "keyword": label,
            "created": 0,
            "account_count": 0,
            "task_ids": [],
            "tasks": [],
            "accounts": [],
            "skipped": skipped,
        }

    creator_ids = ",".join(item["creator_id"] for item in accounts)
    task = crawler_adapter.create_task(
        TaskCreate(
            name=f"账号分析-{len(accounts)}个竞品账号",
            mode="account_analysis",
            platform=platform,  # type: ignore[arg-type]
            login_type=login_type if login_type in ("qrcode", "phone", "cookie") else "qrcode",
            creator_id=creator_ids,
            content_count=_account_analysis_content_count(),
            comment_count=0,
            collect_comments=False,
            collect_sub_comments=False,
            max_concurrency=1,
            headless=headless,
            execute_crawler=True,
        )
    )
    with database.connect() as conn:
        crawler_adapter.log_task(conn, str(task["id"]), "info", log_message)

    return {
        "ok": True,
        "platform": platform,
        "keyword": label,
        "created": 1,
        "account_count": len(accounts),
        "task_ids": [task["id"]],
        "tasks": [{"task_id": task["id"], "task_name": task["name"], "account_count": len(accounts)}],
        "accounts": accounts,
        "skipped": skipped,
    }


def create_keyword_account_analysis_tasks(platform: str, keyword: str, limit: int = 100) -> dict[str, Any]:
    keyword_value = keyword or "未标记关键词"
    safe_limit = max(1, min(int(limit), 100))
    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ua.id, ua.nickname, ua.profile_url, ua.platform_user_id, ua.sec_uid
            FROM contents c
            JOIN user_accounts ua ON ua.id = c.author_account_id
            WHERE c.platform = ?
              AND c.source_keyword = ?
              AND ua.platform IN ('dy', 'xhs')
              AND COALESCE(NULLIF(ua.competitor_status, ''), '未分析') = '未分析'
            ORDER BY ua.updated_at DESC, ua.id DESC
            LIMIT ?
            """,
            (platform, keyword_value, safe_limit),
        ).fetchall()
    return _build_account_analysis_task(
        platform,
        rows,
        keyword_value,
        f"关键词一键竞品分析：{platform}/{keyword_value}，账号 {len(rows)} 个",
    )


def create_task_account_analysis_task(source_task_id: str, limit: int = 100) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit), 100))
    with database.connect() as conn:
        task = conn.execute("SELECT * FROM crawl_jobs WHERE id = ?", (source_task_id,)).fetchone()
        if not task:
            raise ValueError("来源任务不存在，无法自动分析竞品账号")
        platform = str(task["platform"])
        rows = conn.execute(
            """
            SELECT DISTINCT ua.id, ua.nickname, ua.profile_url, ua.platform_user_id, ua.sec_uid
            FROM account_sources src
            JOIN user_accounts ua ON ua.id = src.account_id
            WHERE src.task_id = ?
              AND src.active = 1
              AND ua.platform IN ('dy', 'xhs')
              AND COALESCE(NULLIF(ua.competitor_status, ''), '未分析') = '未分析'
            ORDER BY ua.updated_at DESC, ua.id DESC
            LIMIT ?
            """,
            (source_task_id, safe_limit),
        ).fetchall()
    return _build_account_analysis_task(
        platform,
        rows,
        str(task["keywords"] or task["name"] or source_task_id),
        f"自动竞品分析：来源任务 {source_task_id}，账号 {len(rows)} 个",
    )


def _find_customer_defaults() -> dict[str, Any]:
    with database.connect() as conn:
        login_type = database.get_setting(conn, "login_type", "qrcode")
        headless = database.get_setting(conn, "headless", "false") == "true"
        content_count = _safe_int_setting(conn, "default_content_count", 20, 1, 500)
        comment_count = _safe_int_setting(conn, "default_comment_count", 20, 0, 1000)
    return {
        "login_type": login_type if login_type in ("qrcode", "phone", "cookie") else "qrcode",
        "headless": headless,
        "content_count": content_count,
        "comment_count": comment_count,
    }


def _safe_int_setting(conn: sqlite3.Connection, key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(database.get_setting(conn, key, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _active_find_customer_identifiers(conn: sqlite3.Connection, platform: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT creator_id
        FROM crawl_jobs
        WHERE mode = 'competitor_crawl'
          AND status IN ('pending', 'running')
          AND platform = ?
        """,
        (platform,),
    ).fetchall()
    return {
        item.strip()
        for row in rows
        for item in str(row["creator_id"] or "").split(",")
        if item.strip()
    }


def _build_find_customer_task(
    platform: str,
    rows: list[sqlite3.Row],
    label: str,
    log_message: str,
    skipped: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    skipped = skipped or []
    defaults = _find_customer_defaults()
    with database.connect() as conn:
        active_identifiers = _active_find_customer_identifiers(conn, platform)

    accounts: list[dict[str, Any]] = []
    for row in rows:
        account = database.row_to_dict(row)
        identifier = _account_profile_identifier(account)
        if not identifier:
            skipped.append({"account_id": int(row["id"]), "nickname": row["nickname"], "reason": "账号缺少主页链接或平台ID"})
            continue
        if identifier in active_identifiers:
            skipped.append({"account_id": int(row["id"]), "nickname": row["nickname"], "reason": "已有找客户任务正在等待或运行"})
            continue
        accounts.append(
            {
                "account_id": int(row["id"]),
                "nickname": row["nickname"],
                "creator_id": identifier,
            }
        )

    if not accounts:
        return {
            "ok": True,
            "platform": platform,
            "keyword": label,
            "created": 0,
            "account_count": 0,
            "task_ids": [],
            "tasks": [],
            "accounts": [],
            "skipped": skipped,
        }

    creator_ids = ",".join(item["creator_id"] for item in accounts)
    task = crawler_adapter.create_task(
        TaskCreate(
            name=f"找客户-{len(accounts)}个竞品账号" if len(accounts) > 1 else f"找客户-{accounts[0]['nickname'] or label}",
            mode="competitor_crawl",
            platform=platform,  # type: ignore[arg-type]
            login_type=defaults["login_type"],
            creator_id=creator_ids,
            content_count=defaults["content_count"],
            comment_count=defaults["comment_count"],
            collect_comments=True,
            collect_sub_comments=True,
            max_concurrency=1,
            headless=defaults["headless"],
            execute_crawler=True,
        )
    )
    with database.connect() as conn:
        crawler_adapter.log_task(conn, str(task["id"]), "info", log_message)

    return {
        "ok": True,
        "platform": platform,
        "keyword": label,
        "created": 1,
        "account_count": len(accounts),
        "task_ids": [task["id"]],
        "tasks": [{"task_id": task["id"], "task_name": task["name"], "account_count": len(accounts)}],
        "accounts": accounts,
        "skipped": skipped,
    }


def create_account_find_customer_task(account_id: int) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute(
            """
            SELECT id, platform, platform_user_id, sec_uid, nickname, profile_url, competitor_status
            FROM user_accounts
            WHERE id = ?
            """,
            (account_id,),
        ).fetchone()
        if not row:
            raise ValueError("账号不存在，无法找客户")
        if str(row["competitor_status"] or "") != "竞品":
            raise ValueError("只有已判定为竞品的账号才能找客户")
    return _build_find_customer_task(
        str(row["platform"]),
        [row],
        str(row["nickname"] or account_id),
        f"竞品账号找客户：账号 {row['nickname'] or account_id}（ID={account_id}）",
    )


def create_keyword_find_customer_task(platform: str, keyword: str, limit: int = 100) -> dict[str, Any]:
    keyword_value = keyword or "未标记关键词"
    safe_limit = max(1, min(int(limit), 100))
    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ua.id, ua.platform, ua.platform_user_id, ua.sec_uid,
                   ua.nickname, ua.profile_url, ua.competitor_status
            FROM user_accounts ua
            LEFT JOIN contents c ON c.author_account_id = ua.id
            LEFT JOIN account_sources src ON src.account_id = ua.id AND src.active = 1
            WHERE ua.platform = ?
              AND ua.competitor_status = '竞品'
              AND (
                COALESCE(NULLIF(c.source_keyword, ''), '未标记关键词') = ?
                OR COALESCE(NULLIF(src.keyword, ''), '未标记关键词') = ?
              )
            ORDER BY ua.updated_at DESC, ua.id DESC
            LIMIT ?
            """,
            (platform, keyword_value, keyword_value, safe_limit),
        ).fetchall()
    return _build_find_customer_task(
        platform,
        rows,
        keyword_value,
        f"关键词一键找客户：{platform}/{keyword_value}，竞品账号 {len(rows)} 个",
    )


def run_account_analysis_batch(account_ids: list[int], task_id: str) -> None:
    crawler_adapter.run_task(task_id)
    task = crawler_adapter.get_task(task_id)
    if not task or task.get("status") != "succeeded":
        with database.connect() as conn:
            crawler_adapter.log_task(conn, task_id, "error", "批量账号分析已停止：账号主页和视频采集未成功，未触发 AI 分析")
        return

    succeeded = 0
    auto_deleted = 0
    failed: list[dict[str, str]] = []
    job_ids: list[str] = []
    for account_id in account_ids:
        try:
            job = ai_service.create_ai_job("competitor", int(account_id), run_now=False)
            job_ids.append(str(job["id"]))
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, ensure_ascii=False)
            failed.append({"account_id": str(account_id), "reason": detail})
        except Exception as exc:
            failed.append({"account_id": str(account_id), "reason": str(exc)})
    summary = ai_service.run_ai_jobs_parallel(job_ids) if job_ids else {"succeeded": 0, "failed": 0, "auto_deleted": 0, "errors": []}
    succeeded = int(summary.get("succeeded", 0))
    auto_deleted = int(summary.get("auto_deleted", 0))
    failed.extend(
        {"account_id": str(item.get("job_id", "")), "reason": str(item.get("reason", ""))}
        for item in summary.get("errors", [])
    )

    with database.connect() as conn:
        crawler_adapter.log_task(conn, task_id, "info", f"批量账号 AI 分析完成：成功 {succeeded} 个，失败 {len(failed)} 个，自动删除非竞品 {auto_deleted} 个")
        if failed:
            crawler_adapter.log_task(conn, task_id, "error", f"批量账号 AI 分析失败明细：{json.dumps(failed, ensure_ascii=False)}")


def run_keyword_account_analysis(account_ids: list[int], task_id: str) -> None:
    run_account_analysis_batch(account_ids, task_id)


def resolve_account_analysis_task_account_ids(task: dict[str, object]) -> list[int]:
    # 账号分析必须绑定原账号，采集完成后才能继续触发竞品 AI 判断。
    platform = str(task.get("platform") or "")
    creator_ids = _creator_id_items(task.get("creator_id"))
    if not platform or not creator_ids:
        raise ValueError("账号分析任务缺少平台或主页/ID，无法启动")
    account_ids: list[int] = []
    seen: set[int] = set()
    missing: list[str] = []
    with database.connect() as conn:
        for creator_id in creator_ids:
            row = conn.execute(
                """
                SELECT id
                FROM user_accounts
                WHERE platform = ?
                  AND (
                    profile_url = ?
                    OR platform_user_id = ?
                    OR sec_uid = ?
                  )
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (platform, creator_id, creator_id, creator_id),
            ).fetchone()
            if not row:
                missing.append(creator_id)
                continue
            account_id = int(row["id"])
            if account_id not in seen:
                account_ids.append(account_id)
                seen.add(account_id)
    if missing and len(creator_ids) == 1:
        raise ValueError("账号分析任务找不到原账号，无法启动")
    if missing:
        preview = "、".join(missing[:3])
        suffix = "..." if len(missing) > 3 else ""
        raise ValueError(f"账号分析任务有 {len(missing)} 个主页/ID 找不到原账号，无法启动：{preview}{suffix}")
    return account_ids


def resolve_account_analysis_task_account_id(task: dict[str, object]) -> int:
    return resolve_account_analysis_task_account_ids(task)[0]


def run_account_analysis(account_id: int, task_id: str) -> None:
    crawler_adapter.run_task(task_id)
    task = crawler_adapter.get_task(task_id)
    if not task or task.get("status") != "succeeded":
        with database.connect() as conn:
            crawler_adapter.log_task(conn, task_id, "error", "账号分析已停止：主页和视频采集未成功，未触发 AI 分析")
        return

    try:
        job = ai_service.create_ai_job("competitor", account_id, run_now=True)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, ensure_ascii=False)
        with database.connect() as conn:
            crawler_adapter.log_task(conn, task_id, "error", f"账号资料已导入，但 AI 分析失败：{detail}")
        return
    except Exception as exc:
        with database.connect() as conn:
            crawler_adapter.log_task(conn, task_id, "error", f"账号资料已导入，但 AI 分析异常：{exc}")
        return

    with database.connect() as conn:
        crawler_adapter.log_task(conn, task_id, "info", f"账号 AI 分析完成：{job['id']}")


def create_customer_intent_analysis(lead_id: int, run_now: bool = True) -> dict[str, Any]:
    with database.connect() as conn:
        row = conn.execute(
            """
            SELECT lua.id
            FROM lead_user_accounts lua
            WHERE lua.id = ? AND lua.hidden = 0
            """,
            (lead_id,),
        ).fetchone()
        if not row:
            raise ValueError("客户账号不存在或已隐藏，无法意向分析")
    return ai_service.create_ai_job("lead", lead_id, run_now=run_now)


def update_customer_follow_status(lead_id: int, follow_status: str, note: str = "") -> dict[str, Any]:
    follow_status = str(follow_status or "").strip()
    with database.connect() as conn:
        row = conn.execute(
            """
            SELECT id, screening_status, follow_status, hidden
            FROM lead_user_accounts
            WHERE id = ?
            """,
            (lead_id,),
        ).fetchone()
        if not row:
            raise ValueError("客户账号不存在，无法修改跟进状态")

        current_status = str(row["follow_status"] or "待筛选")
        allowed_targets = _allowed_customer_follow_targets(current_status)
        if follow_status not in allowed_targets:
            allowed_text = " / ".join(allowed_targets)
            raise ValueError(f"当前状态“{current_status}”不能直接改为“{follow_status}”，可选：{allowed_text}")

        screening_status = _screening_status_for_follow_status(follow_status)
        manual_follow_status = 0 if follow_status == "待筛选" else 1
        conn.execute(
            """
            UPDATE lead_user_accounts
            SET screening_status = ?, follow_status = ?, manual_follow_status = ?,
                hidden = 0, updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (screening_status, follow_status, manual_follow_status, lead_id),
        )
        if current_status != follow_status:
            conn.execute(
                "INSERT INTO lead_status_events(lead_account_id, from_status, to_status, note) VALUES(?, ?, ?, ?)",
                (lead_id, current_status, follow_status, note.strip() or "人工修改跟进状态"),
            )
    return {
        "ok": True,
        "lead_id": lead_id,
        "screening_status": screening_status,
        "follow_status": follow_status,
        "manual_follow_status": manual_follow_status,
    }


def _screening_status_for_follow_status(follow_status: str) -> str:
    if follow_status == "待筛选":
        return "待筛选"
    if follow_status in NON_CUSTOMER_FOLLOW_STATUSES:
        return "非客户"
    return "目标客户"


def _allowed_customer_follow_targets(current_status: str) -> list[str]:
    current_status = current_status or "待筛选"
    if current_status in ("待筛选", "未分析", "目标客户"):
        return ["未私信", "已私信", "非客户"]
    if current_status == "未私信":
        return ["已私信", "未回复", "非客户", "待筛选"]
    if current_status in ("未回复", "已私信"):
        return ["未私信", "已私信", "已回复", "未成交", "非客户", "待筛选"]
    if current_status == "已回复":
        return ["已成交", "未成交", "未回复", "未私信", "非客户", "待筛选"]
    if current_status == "未成交":
        return ["已成交", "已回复", "未回复", "未私信", "非客户", "待筛选"]
    if current_status == "已成交":
        return ["已回复", "未成交", "未回复", "未私信", "非客户", "待筛选"]
    if current_status in ("非客户", "无需跟进", "已移出", "隐藏"):
        return ["待筛选", "未私信"]
    return ["待筛选", "未私信", "非客户"]


def _customer_lead_rows_for_account(conn: sqlite3.Connection, account_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT DISTINCT lua.id AS lead_id, lua.account_id, ua.nickname,
               lua.screening_status, lua.follow_status, lua.updated_at
        FROM lead_sources ls
        JOIN lead_user_accounts lua ON lua.id = ls.lead_account_id
        JOIN user_accounts ua ON ua.id = lua.account_id
        LEFT JOIN contents c ON c.id = ls.content_id
        WHERE ls.active = 1
          AND lua.hidden = 0
          AND (ls.source_account_id = ? OR c.author_account_id = ?)
        ORDER BY lua.updated_at DESC, lua.id DESC
        """,
        (account_id, account_id),
    ).fetchall()


def create_account_customer_intent_jobs(account_id: int, run_now: bool = False) -> dict[str, Any]:
    with database.connect() as conn:
        account = conn.execute(
            "SELECT id, nickname, competitor_status FROM user_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if not account:
            raise ValueError("竞品账号不存在，无法一键意向分析")
        if str(account["competitor_status"] or "") != "竞品":
            raise ValueError("只有已判定为竞品的账号才能一键意向分析")
        rows = _customer_lead_rows_for_account(conn, account_id)
        lead_ids = [int(row["lead_id"]) for row in rows]
        existing_jobs = _existing_customer_intent_jobs(conn, lead_ids)

    jobs: list[dict[str, Any]] = []
    resumed_job_ids: list[str] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        lead_id = int(row["lead_id"])
        existing_job = existing_jobs.get(lead_id)
        if existing_job:
            if existing_job["status"] == "running":
                skipped.append({"lead_id": lead_id, "nickname": row["nickname"], "reason": "已有意向分析任务正在运行"})
            else:
                resumed_job_ids.append(str(existing_job["id"]))
            continue
        jobs.append(ai_service.create_ai_job("lead", lead_id, run_now=False))

    job_ids = [str(job["id"]) for job in jobs] + resumed_job_ids
    summary: dict[str, Any] | None = None
    if run_now and job_ids:
        summary = ai_service.run_ai_jobs_parallel(job_ids)

    return {
        "ok": True,
        "account_id": account_id,
        "account_name": account["nickname"],
        "lead_count": len(rows),
        "created": len(jobs),
        "resumed": len(resumed_job_ids),
        "job_ids": job_ids,
        "jobs": jobs,
        "skipped": skipped,
        "summary": summary,
        "concurrency": ai_service.ai_analysis_concurrency(),
    }


def _existing_customer_intent_jobs(conn: sqlite3.Connection, lead_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not lead_ids:
        return {}
    placeholders = ",".join(["?"] * len(lead_ids))
    rows = conn.execute(
        f"""
        SELECT id, target_id, status, updated_at
        FROM analysis_jobs
        WHERE target_type = 'lead'
          AND target_id IN ({placeholders})
          AND status IN ('pending', 'running')
        ORDER BY target_id, created_at DESC
        """,
        lead_ids,
    ).fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        lead_id = int(row["target_id"])
        if lead_id in result:
            continue
        job = database.row_to_dict(row)
        if job["status"] == "running" and _is_stale_ai_job(conn, str(job["updated_at"])):
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'pending',
                    error = '检测到旧的运行中任务，已恢复到待执行队列',
                    updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (job["id"],),
            )
            job["status"] = "pending"
        result[lead_id] = job
    return result


def _is_stale_ai_job(conn: sqlite3.Connection, updated_at: str) -> bool:
    row = conn.execute(
        "SELECT ? < datetime('now', 'localtime', ?) AS stale",
        (updated_at, f"-{STALE_AI_JOB_MINUTES} minutes"),
    ).fetchone()
    return bool(row["stale"])


def run_account_customer_intent_jobs(job_ids: list[str]) -> dict[str, Any]:
    return ai_service.run_ai_jobs_parallel(job_ids)


def delete_account_non_customers(account_id: int) -> dict[str, Any]:
    with database.connect() as conn:
        account = conn.execute(
            "SELECT id, nickname, competitor_status FROM user_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if not account:
            raise ValueError("竞品账号不存在，无法删除非客户")
        if str(account["competitor_status"] or "") != "竞品":
            raise ValueError("只有已判定为竞品的账号才能删除非客户")
        rows = [
            row for row in _customer_lead_rows_for_account(conn, account_id)
            if str(row["screening_status"] or "") == "非客户" or str(row["follow_status"] or "") in ("非客户", "无需跟进")
        ]

    deleted = 0
    errors: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    for row in rows:
        try:
            result = deletion.delete_lead_customer(
                int(row["lead_id"]),
                source_account_id=account_id,
                source="account_non_customer_delete",
            )
            deleted += 1
            details.append({"lead_id": int(row["lead_id"]), "account_id": int(row["account_id"]), "result": result})
        except Exception as exc:
            reason = exc.detail if isinstance(exc, HTTPException) else str(exc)
            errors.append({"lead_id": int(row["lead_id"]), "account_id": int(row["account_id"]), "reason": reason})

    return {
        "ok": True,
        "account_id": account_id,
        "candidate_count": len(rows),
        "deleted": deleted,
        "failed": len(errors),
        "errors": errors,
        "details": details,
    }


def delete_keyword_non_competitors(platform: str, keyword: str) -> dict[str, Any]:
    keyword_value = keyword or "未标记关键词"
    with database.connect() as conn:
        account_rows = conn.execute(
            """
            SELECT DISTINCT ua.*
            FROM contents c
            JOIN user_accounts ua ON ua.id = c.author_account_id
            WHERE c.platform = ?
              AND COALESCE(NULLIF(c.source_keyword, ''), '未标记关键词') = ?
              AND ua.competitor_status = '非竞品'
            ORDER BY ua.id
            """,
            (platform, keyword_value),
        ).fetchall()
        account_ids = [int(row["id"]) for row in account_rows]
        if not account_ids:
            return {"ok": True, "deleted": 0, "keyword": keyword_value, "account_ids": []}

        placeholders = ",".join(["?"] * len(account_ids))
        refs = conn.execute(
            f"""
            SELECT *
            FROM raw_source_refs
            WHERE entity_type = 'account' AND entity_id IN ({placeholders})
            ORDER BY entity_id
            """,
            account_ids,
        ).fetchall()
        refs_by_account: dict[int, list[dict[str, Any]]] = {account_id: [] for account_id in account_ids}
        for ref in refs:
            refs_by_account[int(ref["entity_id"])].append(database.row_to_dict(ref))
        flat_refs = [ref for items in refs_by_account.values() for ref in items]
        tombstone_count = 0
        for row in account_rows:
            tombstone_count += tombstones.record_account_row(conn, row, "keyword_non_competitor_delete")
        for account_id in account_ids:
            conn.execute("DELETE FROM user_accounts WHERE id = ?", (account_id,))
        conn.execute(
            f"DELETE FROM raw_source_refs WHERE entity_type = 'account' AND entity_id IN ({placeholders})",
            account_ids,
        )
        conn.execute(
            "INSERT INTO deletion_audit(entity_type, entity_id, hard_delete, detail) VALUES('keyword_non_competitors', 0, 1, ?)",
            (
                json.dumps(
                    {
                        "platform": platform,
                        "keyword": keyword_value,
                        "account_ids": account_ids,
                        "raw_refs": len(flat_refs),
                        "tombstones": {"accounts": tombstone_count, "contents": 0, "comments": 0},
                    },
                    ensure_ascii=False,
                ),
            ),
        )
    return {
        "ok": True,
        "deleted": len(account_ids),
        "keyword": keyword_value,
        "account_ids": account_ids,
        "raw_refs": len(flat_refs),
        "tombstones": {"accounts": tombstone_count, "contents": 0, "comments": 0},
    }
