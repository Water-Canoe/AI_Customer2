from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


def prepare_project(tmp_path: Path) -> tuple[Path, Path]:
    project_db = tmp_path / "ai_customer.sqlite3"
    raw_db = tmp_path / "sqlite_tables.db"
    os.environ["AI_CUSTOMER_DB"] = str(project_db)
    from app import database

    database.init_db()
    create_raw_db(raw_db)
    with database.connect() as conn:
        database.set_setting(conn, "media_crawler_db_path", str(raw_db))
        database.set_setting(conn, "media_crawler_path", str(tmp_path))
    return project_db, raw_db


def create_raw_db(raw_db: Path) -> None:
    conn = sqlite3.connect(raw_db)
    try:
        conn.executescript(
            """
            CREATE TABLE douyin_aweme (
                user_id TEXT, sec_uid TEXT, short_user_id TEXT, user_unique_id TEXT,
                nickname TEXT, avatar TEXT, user_signature TEXT, ip_location TEXT,
                add_ts INTEGER, last_modify_ts INTEGER, aweme_id INTEGER,
                aweme_type TEXT, title TEXT, desc TEXT, create_time INTEGER,
                liked_count TEXT, comment_count TEXT, share_count TEXT,
                collected_count TEXT, aweme_url TEXT, cover_url TEXT,
                video_download_url TEXT, music_download_url TEXT,
                note_download_url TEXT, source_keyword TEXT
            );
            CREATE TABLE douyin_aweme_comment (
                user_id TEXT, sec_uid TEXT, short_user_id TEXT, user_unique_id TEXT,
                nickname TEXT, avatar TEXT, user_signature TEXT, ip_location TEXT,
                add_ts INTEGER, last_modify_ts INTEGER, comment_id INTEGER,
                aweme_id INTEGER, content TEXT, create_time INTEGER,
                sub_comment_count TEXT, parent_comment_id TEXT,
                like_count TEXT, pictures TEXT
            );
            CREATE TABLE dy_creator (
                user_id TEXT, nickname TEXT, avatar TEXT, ip_location TEXT,
                add_ts INTEGER, last_modify_ts INTEGER, desc TEXT, gender TEXT,
                follows TEXT, fans TEXT, interaction TEXT, videos_count TEXT
            );
            INSERT INTO douyin_aweme VALUES (
                'creator-1', 'sec-1', '', 'unique-1', 'AI客服竞品号',
                '', '专注AI客服系统', '', 1, 1, 10001, 'video',
                'AI客服怎么选', '客户在评论区问价格', 1, '18', '2',
                '0', '0', 'https://douyin.example/video/10001', '', '', '', '', 'AI客服'
            );
            INSERT INTO douyin_aweme_comment VALUES (
                'lead-1', 'lead-sec-1', '', 'lead-unique-1', '准备采购的老板',
                '', '正在找替代方案', '', 1, 1, 90001, 10001,
                '多少钱，能不能替代人工客服？', 1, '0', '', '3', ''
            );
            INSERT INTO dy_creator VALUES (
                'creator-1', 'AI客服竞品号', '', '', 1, 1,
                '专注AI客服系统', '', '10', '2000', '88', '12'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_task_command_mapping_and_import(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.services.importer import import_for_task
    from app import views

    task = crawler_adapter.create_task(
        TaskCreate(
            mode="competitor_crawl",
            platform="dy",
            creator_id="creator-1",
            content_count=5,
            comment_count=10,
            execute_crawler=False,
        )
    )

    assert "--platform dy" in str(task["command"])
    assert "--type creator" in str(task["command"])
    result = import_for_task(str(task["id"]))
    assert result["contents"] == 1
    assert result["comments"] == 1
    task_detail = crawler_adapter.get_task(str(task["id"]))
    assert task_detail is not None
    outcome = task_detail["outcome"]
    assert outcome["counts"]["contents"] == 1
    assert outcome["counts"]["comments"] == 1
    assert outcome["counts"]["leads"] == 1
    assert outcome["health"] == "actionable"
    assert "线索客户" in outcome["next_actions"][0]

    leads = views.list_library("lead_customers")["rows"]
    assert len(leads) == 1
    assert leads[0]["nickname"] == "准备采购的老板"
    tree = views.overview_tree()
    assert tree[0]["kind"] == "platform"
    assert tree[0]["label"] == "dy"


def test_competitor_discovery_matches_profile_signature(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)
    raw_conn = sqlite3.connect(raw_db)
    try:
        raw_conn.execute("UPDATE douyin_aweme SET nickname = ?, user_signature = ?, source_keyword = ?", ("普通作者", "专注AI客服系统", "AI客服"))
        raw_conn.execute("UPDATE dy_creator SET nickname = ?, desc = ?", ("普通作者", "专注AI客服系统"))
        raw_conn.commit()
    finally:
        raw_conn.close()

    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.services.importer import import_for_task
    from app import views

    task = crawler_adapter.create_task(
        TaskCreate(
            mode="competitor_discovery",
            platform="dy",
            keywords="AI客服",
            execute_crawler=False,
        )
    )

    result = import_for_task(str(task["id"]))
    assert result["contents"] == 1

    candidates = views.list_library("competitor_candidates")["rows"]
    assert len(candidates) == 1
    assert candidates[0]["nickname"] == "普通作者"
    assert "AI客服" in candidates[0]["signature"]
    task_detail = crawler_adapter.get_task(str(task["id"]))
    assert task_detail is not None
    assert task_detail["outcome"]["counts"]["competitor_candidates"] == 1
    assert task_detail["outcome"]["health"] == "actionable"


def test_search_task_sanitizes_irrelevant_ids_and_requires_keywords(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app.schemas import TaskCreate
    from app.services import crawler_adapter

    task = crawler_adapter.create_task(
        TaskCreate(
            mode="competitor_discovery",
            platform="dy",
            keywords="  AI客服  ",
            creator_id="stale-creator",
            specified_id="stale-content",
            execute_crawler=False,
        )
    )

    assert task["crawler_type"] == "search"
    assert task["keywords"] == "AI客服"
    assert task["creator_id"] == ""
    assert task["specified_id"] == ""
    assert "--keywords AI客服" in str(task["command"])
    assert "--creator_id" not in str(task["command"])
    assert "--specified_id" not in str(task["command"])

    with pytest.raises(ValueError, match="必须填写关键词"):
        crawler_adapter.create_task(
            TaskCreate(mode="competitor_discovery", platform="dy", keywords="  ", execute_crawler=False)
        )


def test_profile_enrichment_task_uses_creator_mode_and_imports_signature(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)

    from app import database
    from app.services import crawler_adapter
    from app.services.importer import import_for_task

    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, sec_uid, nickname, signature, profile_url)
            VALUES('dy', 'creator-1', 'sec-1', 'Profile Test', '', 'https://www.douyin.com/user/sec-1')
            """
        )
        account_id = conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'creator-1'").fetchone()["id"]

    task = crawler_adapter.create_profile_enrichment_task(int(account_id))
    command = str(task["command"])

    assert task["mode"] == "profile_enrichment"
    assert task["crawler_type"] == "creator"
    assert task["creator_id"] == "https://www.douyin.com/user/sec-1"
    assert "--type creator" in command
    assert "--creator_id https://www.douyin.com/user/sec-1" in command
    assert "--get_comment false" in command

    raw_conn = sqlite3.connect(raw_db)
    try:
        raw_conn.execute(
            "UPDATE dy_creator SET nickname = ?, desc = ?, add_ts = ?, last_modify_ts = ? WHERE user_id = ?",
            ("Profile Test", "profile bio from creator mode", int(task["raw_started_ts_ms"]) + 1000, int(task["raw_started_ts_ms"]) + 1000, "creator-1"),
        )
        raw_conn.commit()
    finally:
        raw_conn.close()

    result = import_for_task(str(task["id"]))
    assert result["accounts"] == 1
    with database.connect() as conn:
        row = conn.execute("SELECT signature FROM user_accounts WHERE id = ?", (account_id,)).fetchone()
    assert row["signature"] == "profile bio from creator mode"


def test_profile_enrichment_rechecks_competitor_keyword_match(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)

    from app import views
    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.services.importer import import_for_task

    raw_conn = sqlite3.connect(raw_db)
    try:
        raw_conn.execute(
            "UPDATE douyin_aweme SET nickname = ?, user_signature = ?, source_keyword = ?",
            ("Plain Author", "", "EV"),
        )
        raw_conn.execute("UPDATE dy_creator SET nickname = ?, desc = ?", ("Plain Author", ""))
        raw_conn.commit()
    finally:
        raw_conn.close()

    discovery = crawler_adapter.create_task(
        TaskCreate(
            mode="competitor_discovery",
            platform="dy",
            keywords="EV",
            execute_crawler=False,
        )
    )
    import_for_task(str(discovery["id"]))
    assert views.list_library("competitor_candidates")["rows"] == []

    account = views.list_library("contents")["rows"][0]
    profile_task = crawler_adapter.create_profile_enrichment_task(int(account["author_account_id"]))
    raw_conn = sqlite3.connect(raw_db)
    try:
        profile_ts = int(profile_task["raw_started_ts_ms"]) + 1000
        raw_conn.execute(
            "UPDATE dy_creator SET nickname = ?, desc = ?, add_ts = ?, last_modify_ts = ? WHERE user_id = ?",
            (
                "Plain Author",
                "EV scooter shop profile",
                profile_ts,
                profile_ts,
                "creator-1",
            ),
        )
        raw_conn.execute("UPDATE douyin_aweme SET add_ts = ?, last_modify_ts = ?", (profile_ts, profile_ts))
        raw_conn.commit()
    finally:
        raw_conn.close()

    result = import_for_task(str(profile_task["id"]))
    assert result["competitor_candidates"] == 1
    candidates = views.list_library("competitor_candidates")["rows"]
    assert len(candidates) == 1
    assert candidates[0]["nickname"] == "Plain Author"
    assert candidates[0]["signature"] == "EV scooter shop profile"
    content = views.list_library("contents")["rows"][0]
    assert content["task_id"] == discovery["id"]


def test_profile_enrichment_rejects_platform_without_sqlite_creator_store(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.services import crawler_adapter

    with database.connect() as conn:
        conn.execute(
            "INSERT INTO user_accounts(platform, platform_user_id, nickname) VALUES('ks', 'ks-user-1', 'KS User')"
        )
        account_id = conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'ks-user-1'").fetchone()["id"]

    with pytest.raises(ValueError, match="MediaCrawler SQLite"):
        crawler_adapter.create_profile_enrichment_task(int(account_id))


def test_batch_profile_enrichment_creates_limited_deduped_tasks(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.main import app
    from app.services import crawler_adapter

    with database.connect() as conn:
        dy_id = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, sec_uid, nickname, signature, profile_url)
            VALUES('dy', 'batch-dy', 'batch-sec', '批量抖音', '', 'https://www.douyin.com/user/batch-sec')
            """
        ).lastrowid
        xhs_id = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, signature, profile_url)
            VALUES('xhs', 'batch-xhs', '批量小红书', '', 'https://www.xiaohongshu.com/user/profile/batch-xhs')
            """
        ).lastrowid
        ks_id = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, signature, profile_url)
            VALUES('ks', 'batch-ks', '批量快手', '', 'https://www.kuaishou.com/profile/batch-ks')
            """
        ).lastrowid
        conn.execute(
            "INSERT INTO contents(platform, content_id, author_account_id, title, source_keyword) VALUES('dy', 'dy-batch-content', ?, '批量内容', '批量')",
            (dy_id,),
        )
        conn.execute(
            "INSERT INTO contents(platform, content_id, author_account_id, title, source_keyword) VALUES('xhs', 'xhs-batch-content', ?, '批量内容', '批量')",
            (xhs_id,),
        )
        conn.execute(
            "INSERT INTO contents(platform, content_id, author_account_id, title, source_keyword) VALUES('ks', 'ks-batch-content', ?, '批量内容', '批量')",
            (ks_id,),
        )

    first = crawler_adapter.create_profile_enrichment_batch(limit=1)
    assert first["created"] == 1
    created_task = crawler_adapter.get_task(first["task_ids"][0])
    assert created_task is not None
    assert created_task["mode"] == "profile_enrichment"
    assert created_task["platform"] in {"dy", "xhs"}

    second = crawler_adapter.create_profile_enrichment_batch(limit=10)
    assert second["created"] == 1
    assert set(first["task_ids"]).isdisjoint(second["task_ids"])

    no_more = crawler_adapter.create_profile_enrichment_batch(limit=10)
    assert no_more["created"] == 0

    client = TestClient(app)
    response = client.post("/api/accounts/profile-enrichment/batch", params={"limit": 5, "run_now": False})
    assert response.status_code == 200
    assert response.json()["created"] == 0


def test_real_crawler_import_ignores_raw_rows_before_task_start(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)
    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.services.importer import import_for_task
    from app import views

    task = crawler_adapter.create_task(
        TaskCreate(
            mode="competitor_discovery",
            platform="dy",
            keywords="新关键词",
            execute_crawler=True,
        )
    )
    started = int(task["raw_started_ts_ms"])
    raw_conn = sqlite3.connect(raw_db)
    try:
        raw_conn.execute("UPDATE douyin_aweme SET add_ts = ?, last_modify_ts = ?", (started - 1000, started - 1000))
        raw_conn.execute("UPDATE dy_creator SET add_ts = ?, last_modify_ts = ?", (started - 1000, started - 1000))
        raw_conn.execute(
            """
            INSERT INTO douyin_aweme VALUES (
                'fresh-creator', 'fresh-sec', '', 'fresh-unique', '新关键词账号',
                '', '新关键词主页简介', '', ?, ?, 10002, 'video',
                '新关键词内容', '只应该导入这一条新内容', 1, '8', '0',
                '0', '0', 'https://douyin.example/video/10002', '', '', '', '', '新关键词'
            )
            """,
            (started + 1000, started + 1000),
        )
        raw_conn.commit()
    finally:
        raw_conn.close()

    result = import_for_task(str(task["id"]))
    assert result["contents"] == 1

    rows = views.list_library("competitor_candidates", keyword="新关键词")["rows"]
    assert len(rows) == 1
    assert rows[0]["nickname"] == "新关键词账号"

    old_rows = views.list_library("competitor_candidates", keyword="AI客服")["rows"]
    assert old_rows == []


def test_ai_missing_key_fails_explicitly(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.services import ai_service

    with database.connect() as conn:
        conn.execute(
            "INSERT INTO user_accounts(platform, platform_user_id, nickname) VALUES('dy', 'u1', '测试客户')"
        )
        account_id = conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'u1'").fetchone()["id"]
        conn.execute("INSERT INTO lead_user_accounts(account_id) VALUES(?)", (account_id,))
        lead_id = conn.execute("SELECT id FROM lead_user_accounts WHERE account_id = ?", (account_id,)).fetchone()["id"]

    with pytest.raises(HTTPException) as exc:
        ai_service.create_ai_job("lead", int(lead_id), run_now=True)
    assert exc.value.status_code == 400


def test_ai_result_updates_whiteboard_status(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.services import ai_service

    with database.connect() as conn:
        conn.execute(
            "INSERT INTO user_accounts(platform, platform_user_id, nickname) VALUES('dy', 'u2', '高意向客户')"
        )
        account_id = conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'u2'").fetchone()["id"]
        conn.execute("INSERT INTO lead_user_accounts(account_id) VALUES(?)", (account_id,))
        lead_id = conn.execute("SELECT id FROM lead_user_accounts WHERE account_id = ?", (account_id,)).fetchone()["id"]
        ai_service.apply_ai_result(
            conn,
            "lead",
            int(lead_id),
            {
                "is_customer": True,
                "intention": "高",
                "reason": "询问价格",
                "pain_points": ["人工客服成本高"],
                "suggested_action": "优先私信",
                "script": "你好，看到你在关注客服替代方案。",
            },
        )
        row = conn.execute("SELECT * FROM lead_user_accounts WHERE id = ?", (lead_id,)).fetchone()
    assert row["follow_status"] == "未私信"
    assert row["screening_status"] == "目标客户"


def test_hard_delete_requires_raw_mapping(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.services import deletion

    with database.connect() as conn:
        conn.execute(
            "INSERT INTO user_accounts(platform, platform_user_id, nickname) VALUES('dy', 'no-raw', '无映射账号')"
        )
        account_id = conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'no-raw'").fetchone()["id"]

    with pytest.raises(HTTPException) as exc:
        deletion.delete_library_row("competitor_candidates", int(account_id), hard=True)
    assert exc.value.status_code == 409


def test_workbench_actions_reports_next_steps(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app.main import app
    from app.schemas import TaskCreate
    from app.services import ai_service, crawler_adapter
    from app.services.importer import import_for_task
    from app import views

    discovery = crawler_adapter.create_task(
        TaskCreate(mode="competitor_discovery", platform="dy", keywords="AI客服", execute_crawler=False)
    )
    import_for_task(str(discovery["id"]))

    crawl = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(crawl["id"]))

    lead_id = views.list_library("lead_customers")["rows"][0]["id"]
    with pytest.raises(HTTPException):
        ai_service.create_ai_job("lead", int(lead_id), run_now=True)

    client = TestClient(app)
    response = client.get("/api/workbench/actions")
    assert response.status_code == 200
    payload = response.json()
    queues = {queue["key"]: queue for queue in payload["queues"]}

    assert queues["competitors_to_analyze"]["count"] >= 1
    assert queues["competitors_to_analyze"]["library"] == "competitor_candidates"
    assert queues["competitors_to_analyze"]["status"] == "未分析"
    assert queues["leads_to_screen"]["count"] >= 1
    assert queues["leads_to_screen"]["library"] == "lead_customers"
    assert queues["ai_failed"]["count"] >= 1
    assert queues["ai_failed"]["view"] == "ai"
    assert queues["profile_enrichment"]["view"] == "overview"
    assert payload["summary"]["ready"] >= 3


def test_api_health_and_settings(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.services.importer import import_for_task
    from app.main import app

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(task["id"]))

    client = TestClient(app)
    assert client.get("/api/health").json()["status"] == "ok"
    task_response = client.get(f"/api/tasks/{task['id']}")
    assert task_response.status_code == 200
    assert task_response.json()["outcome"]["counts"]["leads"] == 1
    list_response = client.get("/api/tasks")
    assert list_response.status_code == 200
    assert list_response.json()[0]["outcome"]["counts"]["comments"] == 1
    response = client.put("/api/settings", json={"values": {"ai_model": "deepseek-chat"}})
    assert response.status_code == 200
    assert response.json()["ai_model"] == "deepseek-chat"


def test_env_check_reports_platform_raw_data_diagnostics(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app.main import app

    client = TestClient(app)
    response = client.get("/api/settings/env-check")
    assert response.status_code == 200
    payload = response.json()
    diagnostics = {item["platform"]: item for item in payload["platform_diagnostics"]}

    dy = diagnostics["dy"]
    assert dy["ok"] is True
    assert dy["tables"]["content"]["table"] == "douyin_aweme"
    assert dy["tables"]["content"]["row_count"] == 1
    dy_content_fields = {field["key"]: field for field in dy["tables"]["content"]["fields"]}
    assert dy_content_fields["author_id"]["non_empty"] == 1
    assert dy_content_fields["signature"]["non_empty"] == 1

    ks = diagnostics["ks"]
    assert ks["tables"]["creator"]["supported"] is False
    assert any("creator" in warning for warning in ks["warnings"])
    assert payload["project_quality"]["summary"]["contents"] == 0
    assert any(issue["title"] == "项目库还没有内容" for issue in payload["project_quality"]["issues"])


def test_platform_capabilities_explain_field_limits(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app.main import app

    client = TestClient(app)
    response = client.get("/api/platform-capabilities")
    assert response.status_code == 200
    capabilities = {item["platform"]: item for item in response.json()}

    dy = capabilities["dy"]
    assert dy["profile_enrichment_supported"] is True
    assert dy["fields"]["content_signature"]["column"] == "user_signature"
    assert dy["fields"]["content_signature"]["status"] == "partial"
    assert dy["fields"]["creator_signature"]["column"] == "desc"
    assert any("补资料" in warning for warning in dy["modes"]["competitor_discovery"]["warnings"])

    xhs = capabilities["xhs"]
    assert xhs["profile_enrichment_supported"] is True
    assert xhs["fields"]["content_signature"]["supported"] is False
    assert xhs["fields"]["creator_signature"]["table"] == "xhs_creator"

    ks = capabilities["ks"]
    assert ks["profile_enrichment_supported"] is False
    assert ks["fields"]["creator_signature"]["supported"] is False
    assert any("不能通过补资料" in warning for warning in ks["warnings"])
    assert ks["modes"]["competitor_crawl"]["comments_default"] is True


def test_env_check_reports_project_quality_after_import(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app.main import app
    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.services.importer import import_for_task

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(task["id"]))

    client = TestClient(app)
    payload = client.get("/api/settings/env-check").json()
    quality = payload["project_quality"]
    sections = {section["key"]: section for section in quality["sections"]}
    account_fields = {field["label"]: field for field in sections["accounts"]["fields"]}
    content_fields = {field["label"]: field for field in sections["contents"]["fields"]}
    comment_fields = {field["label"]: field for field in sections["comments"]["fields"]}

    assert quality["summary"]["contents"] == 1
    assert quality["summary"]["comments"] == 1
    assert quality["summary"]["leads"] == 1
    assert account_fields["昵称"]["non_empty"] >= 1
    assert account_fields["主页简介"]["non_empty"] >= 1
    assert content_fields["作者账号"]["non_empty"] == 1
    assert comment_fields["评论内容"]["non_empty"] == 1


def test_clear_all_data_clears_project_and_media_crawler_databases(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.services.importer import import_for_task
    from app.main import app

    task = crawler_adapter.create_task(
        TaskCreate(
            mode="competitor_crawl",
            platform="dy",
            creator_id="creator-1",
            execute_crawler=False,
        )
    )
    import_for_task(str(task["id"]))

    client = TestClient(app)
    rejected = client.post("/api/settings/clear-data", json={"confirm": "确认"})
    assert rejected.status_code == 400

    response = client.post("/api/settings/clear-data", json={"confirm": "清空所有数据"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["project"]["rows"] > 0
    assert payload["media_crawler"]["rows"] > 0

    with database.connect() as conn:
        for table in ["crawl_jobs", "task_logs", "user_accounts", "contents", "comments", "raw_source_refs"]:
            assert conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"] == 0
        assert database.get_setting(conn, "media_crawler_db_path") == str(raw_db)
        assert database.get_setting(conn, "next_task_number") == "1"

    raw_conn = sqlite3.connect(raw_db)
    try:
        for table in ["douyin_aweme", "douyin_aweme_comment", "dy_creator"]:
            assert raw_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        raw_conn.close()
