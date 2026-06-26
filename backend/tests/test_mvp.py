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
    raw_conn = sqlite3.connect(raw_db)
    try:
        raw_conn.execute("UPDATE dy_creator SET nickname = ?, desc = ? WHERE user_id = ?", ("Profile Test", "profile bio from creator mode", "creator-1"))
        raw_conn.commit()
    finally:
        raw_conn.close()

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

    result = import_for_task(str(task["id"]))
    assert result["accounts"] == 1
    with database.connect() as conn:
        row = conn.execute("SELECT signature FROM user_accounts WHERE id = ?", (account_id,)).fetchone()
    assert row["signature"] == "profile bio from creator mode"


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


def test_api_health_and_settings(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app.main import app

    client = TestClient(app)
    assert client.get("/api/health").json()["status"] == "ok"
    response = client.put("/api/settings", json={"values": {"ai_model": "deepseek-chat"}})
    assert response.status_code == 200
    assert response.json()["ai_model"] == "deepseek-chat"


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
