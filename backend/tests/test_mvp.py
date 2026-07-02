from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture(autouse=True)
def allow_license_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import license_service

    # API tests focus on业务逻辑，授权网络调用单独测试，避免真实公网请求影响稳定性。
    monkeypatch.setattr(license_service, "ensure_authorized", lambda: {"authorized": True})


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
    from app import database, views

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

    with database.connect() as conn:
        account_id = conn.execute(
            "SELECT id FROM user_accounts WHERE platform = 'dy' AND platform_user_id = 'creator-1'"
        ).fetchone()["id"]
        imported_lead_id = conn.execute("SELECT id FROM lead_user_accounts LIMIT 1").fetchone()["id"]
        conn.execute(
            """
            UPDATE lead_user_accounts
            SET screening_status = '目标客户', follow_status = '未私信'
            WHERE id = ?
            """,
            (imported_lead_id,),
        )
        # 账号卡片统计应包含 creator/找客户导入的非关键词来源数据。
        extra_content_id = conn.execute(
            """
            INSERT INTO contents(platform, content_id, author_account_id, title, source_keyword)
            VALUES('dy', 'creator-extra-content', ?, '账号采集补充内容', '')
            """,
            (account_id,),
        ).lastrowid
        lead_account_id = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname)
            VALUES('dy', 'lead-2', '补充评论客户')
            """
        ).lastrowid
        lead_id = conn.execute(
            "INSERT INTO lead_user_accounts(account_id) VALUES(?)",
            (lead_account_id,),
        ).lastrowid
        conn.execute(
            """
            UPDATE lead_user_accounts
            SET screening_status = '非客户', follow_status = '非客户'
            WHERE id = ?
            """,
            (lead_id,),
        )
        extra_comment_id = conn.execute(
            """
            INSERT INTO comments(platform, comment_id, content_id, author_account_id, body)
            VALUES('dy', 'extra-comment', ?, ?, '价格多少？')
            """,
            (extra_content_id, lead_account_id),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO lead_sources(lead_account_id, source_account_id, content_id, comment_id, keyword, source_type)
            VALUES(?, NULL, ?, ?, '', 'comment')
            """,
            (lead_id, extra_content_id, extra_comment_id),
        )

    tree = views.overview_tree()
    assert tree[0]["kind"] == "platform"
    assert tree[0]["label"] == "dy"
    account_metrics = tree[0]["children"][0]["children"][0]["metrics"]
    assert account_metrics["content_total_count"] == 12
    assert account_metrics["crawled_content_count"] == 2
    assert account_metrics["comment_count"] == 2
    assert account_metrics["customer_count"] == 2
    assert account_metrics["target_customer_count"] == 1
    assert account_metrics["non_customer_count"] == 1


def test_overview_groups_unlabeled_account_tasks_by_source_mode(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)
    from app import database, views
    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.services.importer import import_for_task

    raw_conn = sqlite3.connect(raw_db)
    try:
        raw_conn.execute("UPDATE douyin_aweme SET source_keyword = ''")
        raw_conn.commit()
    finally:
        raw_conn.close()

    own_task = crawler_adapter.create_task(
        TaskCreate(mode="own_account", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(own_task["id"]))

    with database.connect() as conn:
        competitor_account_id = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status)
            VALUES('dy', 'direct-competitor', '直接账号任务竞品', '竞品')
            """
        ).lastrowid
        conn.execute(
            """
            INSERT INTO crawl_jobs(id, name, mode, platform, login_type, crawler_type, status)
            VALUES('direct-comp-task', '直接竞品账号爬取', 'competitor_crawl', 'dy', 'qrcode', 'creator', 'succeeded')
            """
        )
        conn.execute(
            """
            INSERT INTO contents(platform, content_id, author_account_id, title, source_keyword, task_id)
            VALUES('dy', 'direct-comp-content', ?, '无关键词竞品内容', '', 'direct-comp-task')
            """,
            (competitor_account_id,),
        )

    tree = views.overview_tree()
    groups = {child["metrics"]["source_mode"]: child for child in tree[0]["children"] if child["kind"] == "source_group"}

    assert all(child["kind"] != "keyword" for child in tree[0]["children"])
    assert set(groups) == {"own_account", "competitor_crawl"}
    assert groups["own_account"]["label"] == "自家账号互动"
    assert groups["competitor_crawl"]["label"] == "竞品账号爬取"
    own_account = groups["own_account"]["children"][0]
    assert own_account["metrics"]["account_role"] == "own_account"
    assert own_account["metrics"]["is_own_account"] == 1


def test_own_account_task_requires_own_account_identifier(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app.schemas import TaskCreate
    from app.services import crawler_adapter

    with pytest.raises(ValueError, match="自家账号主页/ID"):
        crawler_adapter.preview_task(TaskCreate(mode="own_account", platform="dy", execute_crawler=False))


def test_comment_cutoff_filters_old_comments(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)
    import time

    now_ts = int(time.time())
    old_ts = now_ts - 20 * 86400
    raw_conn = sqlite3.connect(raw_db)
    try:
        raw_conn.execute("UPDATE douyin_aweme SET create_time = ? WHERE aweme_id = 10001", (now_ts,))
        raw_conn.execute("UPDATE douyin_aweme_comment SET create_time = ?, comment_id = ?", (old_ts, 90001))
        raw_conn.execute(
            """
            INSERT INTO douyin_aweme_comment VALUES (
                'lead-2', 'lead-sec-2', '', 'lead-unique-2', '最近评论客户',
                '', '近期有需求', '', ?, ?, 90002, 10001,
                '最近也想了解价格', ?, '0', '', '4', ''
            )
            """,
            (now_ts * 1000, now_ts * 1000, now_ts * 1000),
        )
        raw_conn.commit()
    finally:
        raw_conn.close()

    from app import database, views
    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.services.importer import import_for_task

    with database.connect() as conn:
        database.set_setting(conn, "comment_cutoff_days", "5")

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
    result = import_for_task(str(task["id"]))

    assert result["comments"] == 1
    assert result["leads"] == 1
    comments = views.list_library("comments")["rows"]
    assert len(comments) == 1
    assert comments[0]["comment_id"] == "90002"
    assert comments[0]["commenter_nickname"] == "最近评论客户"


def test_comment_cutoff_keeps_recent_comments_on_old_content(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)
    import time

    now_ts = int(time.time())
    old_ts = now_ts - 20 * 86400
    raw_conn = sqlite3.connect(raw_db)
    try:
        raw_conn.execute("UPDATE douyin_aweme SET create_time = ? WHERE aweme_id = 10001", (old_ts,))
        raw_conn.execute("UPDATE douyin_aweme_comment SET create_time = ?, comment_id = ?", (old_ts, 90001))
        raw_conn.execute(
            """
            INSERT INTO douyin_aweme_comment VALUES (
                'lead-2', 'lead-sec-2', '', 'lead-unique-2', 'old-video-new-comment-lead',
                '', 'recent inquiry', '', ?, ?, 90002, 10001,
                'what is the current price?', ?, '0', '', '5', ''
            )
            """,
            (now_ts * 1000, now_ts * 1000, now_ts * 1000),
        )
        raw_conn.commit()
    finally:
        raw_conn.close()

    from app import database, views
    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.services.importer import import_for_task

    with database.connect() as conn:
        database.set_setting(conn, "comment_cutoff_days", "5")

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
    result = import_for_task(str(task["id"]))

    assert result["contents"] == 1
    assert result["comments"] == 1
    assert result["leads"] == 1
    comments = views.list_library("comments")["rows"]
    assert len(comments) == 1
    assert comments[0]["comment_id"] == "90002"


def test_content_cutoff_filters_old_contents_and_related_comments(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)
    import time

    now_ts = int(time.time())
    old_ts = now_ts - 20 * 86400
    raw_conn = sqlite3.connect(raw_db)
    try:
        raw_conn.execute("UPDATE douyin_aweme SET create_time = ? WHERE aweme_id = 10001", (old_ts,))
        raw_conn.execute("UPDATE douyin_aweme_comment SET create_time = ? WHERE comment_id = 90001", (now_ts,))
        raw_conn.commit()
    finally:
        raw_conn.close()

    from app import database
    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.services.importer import import_for_task

    with database.connect() as conn:
        database.set_setting(conn, "content_cutoff_days", "5")

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
    result = import_for_task(str(task["id"]))

    assert result["contents"] == 0
    assert result["comments"] == 0
    assert result["leads"] == 0
    with database.connect() as conn:
        content_count = conn.execute("SELECT COUNT(*) AS c FROM contents WHERE task_id = ?", (task["id"],)).fetchone()["c"]
        comment_count = conn.execute("SELECT COUNT(*) AS c FROM comments WHERE task_id = ?", (task["id"],)).fetchone()["c"]
        lead_source_count = conn.execute("SELECT COUNT(*) AS c FROM lead_sources WHERE task_id = ?", (task["id"],)).fetchone()["c"]
    assert content_count == 0
    assert comment_count == 0
    assert lead_source_count == 0


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


def test_search_import_respects_content_count_per_keyword(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)
    raw_conn = sqlite3.connect(raw_db)
    try:
        raw_conn.execute("UPDATE douyin_aweme SET source_keyword = ?, title = ?, desc = ? WHERE aweme_id = 10001", ("EV", "EV content 1", "EV desc 1"))
        for index in range(2, 6):
            raw_conn.execute(
                """
                INSERT INTO douyin_aweme VALUES (
                    ?, ?, '', ?, ?, '', '', '', 1, 1, ?, 'video',
                    ?, ?, 1, '0', '0', '0', '0', ?, '', '', '', '', 'EV'
                )
                """,
                (
                    f"creator-{index}",
                    f"sec-{index}",
                    f"unique-{index}",
                    f"author-{index}",
                    10000 + index,
                    f"EV content {index}",
                    f"EV desc {index}",
                    f"https://douyin.example/video/{10000 + index}",
                ),
            )
        raw_conn.commit()
    finally:
        raw_conn.close()

    from app import database
    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.services.importer import import_for_task

    task = crawler_adapter.create_task(
        TaskCreate(
            mode="competitor_discovery",
            platform="dy",
            keywords="EV",
            content_count=2,
            execute_crawler=False,
        )
    )
    result = import_for_task(str(task["id"]))

    assert result["contents"] == 2
    assert result["competitor_candidates"] == 2
    with database.connect() as conn:
        content_count = conn.execute("SELECT COUNT(*) AS c FROM contents WHERE task_id = ?", (task["id"],)).fetchone()["c"]
    assert content_count == 2


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


def test_task_preview_uses_same_parameter_normalization(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app.main import app
    from app.schemas import TaskCreate
    from app.services import crawler_adapter

    preview = crawler_adapter.preview_task(
        TaskCreate(
            mode="competitor_discovery",
            platform="dy",
            keywords="  AI客服  ",
            creator_id="stale-creator",
            specified_id="stale-content",
            execute_crawler=False,
        )
    )
    assert preview["crawler_type"] == "search"
    assert preview["normalized"]["keywords"] == "AI客服"
    assert preview["normalized"]["creator_id"] == ""
    assert preview["normalized"]["specified_id"] == ""
    assert preview["sanitized"]["creator_id"]["before"] == "stale-creator"
    assert "--keywords AI客服" in preview["command_text"]
    assert "--creator_id" not in preview["command_text"]
    assert "--specified_id" not in preview["command_text"]

    client = TestClient(app)
    response = client.post(
        "/api/tasks/preview",
        json={
            "mode": "competitor_discovery",
            "platform": "dy",
            "keywords": "  ",
        },
    )
    assert response.status_code == 400
    assert "必须填写关键词" in response.json()["detail"]


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


def test_competitor_discovery_keeps_search_authors_as_candidates_before_profile(tmp_path: Path) -> None:
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
    initial_candidates = views.list_library("competitor_candidates")["rows"]
    assert len(initial_candidates) == 1
    assert initial_candidates[0]["nickname"] == "Plain Author"
    assert initial_candidates[0]["signature"] == ""

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
    assert result["accounts"] == 1
    candidates = views.list_library("competitor_candidates")["rows"]
    assert len(candidates) == 1
    assert candidates[0]["nickname"] == "Plain Author"
    assert candidates[0]["signature"] == "EV scooter shop profile"
    content = views.list_library("contents")["rows"][0]
    assert content["task_id"] == discovery["id"]


def test_account_analysis_task_imports_profile_and_recent_contents(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)
    from app import database
    from app import views
    from app.services import account_actions
    from app.services.importer import import_for_task

    sec_uid = "MS4wLjABTestSecUid"
    profile_url = f"https://www.douyin.com/user/{sec_uid}"
    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, sec_uid, nickname, signature, profile_url)
            VALUES('dy', 'creator-1', ?, 'Analysis Test', '', ?)
            """,
            (sec_uid, profile_url),
        )
        account_id = conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'creator-1'").fetchone()["id"]

    task = account_actions.create_account_analysis_task(int(account_id))
    command = str(task["command"])
    assert task["mode"] == "account_analysis"
    assert task["crawler_type"] == "creator"
    assert task["content_count"] == 5
    assert "--type creator" in command
    assert "--crawler_max_notes_count 5" in command

    raw_conn = sqlite3.connect(raw_db)
    try:
        ts = int(task["raw_started_ts_ms"]) + 1000
        raw_conn.execute(
            "UPDATE dy_creator SET user_id = ?, nickname = ?, desc = ?, fans = ?, add_ts = ?, last_modify_ts = ? WHERE user_id = ?",
            (sec_uid, "Analysis Test", "analysis profile bio", "3000", ts, ts, "creator-1"),
        )
        raw_conn.execute(
            "UPDATE douyin_aweme SET sec_uid = ?, add_ts = ?, last_modify_ts = ?, source_keyword = '' WHERE user_id = ?",
            (sec_uid, ts, ts, "creator-1"),
        )
        for index in range(2, 8):
            raw_conn.execute(
                """
                INSERT INTO douyin_aweme VALUES (
                    'creator-1', ?, '', 'unique-1', 'Analysis Test',
                    '', '', '', ?, ?, ?, 'video',
                    ?, ?, 1, '8', '0',
                    '0', '0', ?, '', '', '', '', ''
                )
                """,
                (
                    sec_uid,
                    ts + index,
                    ts + index,
                    20000 + index,
                    f"analysis title {index}",
                    f"analysis desc {index}",
                    f"https://douyin.example/video/{20000 + index}",
                ),
            )
        raw_conn.commit()
    finally:
        raw_conn.close()

    result = import_for_task(str(task["id"]))
    assert result["accounts"] == 1
    assert result["contents"] == 5
    with database.connect() as conn:
        account = conn.execute("SELECT signature, fans FROM user_accounts WHERE id = ?", (account_id,)).fetchone()
        content_count = conn.execute("SELECT COUNT(*) AS c FROM contents WHERE author_account_id = ?", (account_id,)).fetchone()["c"]
        content = conn.execute("SELECT task_id FROM contents WHERE author_account_id = ? LIMIT 1", (account_id,)).fetchone()
        account_matches = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM user_accounts
            WHERE platform = 'dy'
              AND (platform_user_id = ? OR sec_uid = ? OR profile_url = ?)
            """,
            (sec_uid, sec_uid, profile_url),
        ).fetchone()["c"]
    assert account["signature"] == "analysis profile bio"
    assert account["fans"] == 3000
    assert content_count == 5
    assert content["task_id"] == task["id"]
    assert account_matches == 1
    tree = views.overview_tree()
    labels = [child["label"] for platform in tree for child in platform["children"]]
    assert "未标记关键词" not in labels


def test_account_analysis_content_count_uses_setting(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.services import account_actions

    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, profile_url)
            VALUES('dy', 'creator-setting', 'Config Count', 'https://www.douyin.com/user/creator-setting')
            """
        )
        database.set_setting(conn, "account_analysis_content_count", "3")
        account_id = conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'creator-setting'").fetchone()["id"]

    task = account_actions.create_account_analysis_task(int(account_id))

    assert task["content_count"] == 3
    assert "--crawler_max_notes_count 3" in str(task["command"])


def test_account_analysis_import_limits_contents_per_account(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.services.importer import import_for_task

    with database.connect() as conn:
        account_a = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, sec_uid, nickname, profile_url)
            VALUES('dy', 'sec-a', 'sec-a', '账号A', 'https://www.douyin.com/user/sec-a')
            """
        ).lastrowid
        account_b = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, sec_uid, nickname, profile_url)
            VALUES('dy', 'sec-b', 'sec-b', '账号B', 'https://www.douyin.com/user/sec-b')
            """
        ).lastrowid

    task = crawler_adapter.create_task(
        TaskCreate(
            mode="account_analysis",
            platform="dy",
            creator_id="https://www.douyin.com/user/sec-a,https://www.douyin.com/user/sec-b",
            content_count=2,
            execute_crawler=True,
        )
    )
    raw_conn = sqlite3.connect(raw_db)
    try:
        ts = int(task["raw_started_ts_ms"]) + 1000
        for prefix in ("a", "b"):
            for index in range(4):
                raw_conn.execute(
                    """
                    INSERT INTO douyin_aweme VALUES (
                        ?, ?, '', ?, ?, '', '', '', ?, ?, ?, 'video',
                        ?, ?, 1, '8', '0', '0', '0', ?, '', '', '', '', ''
                    )
                    """,
                    (
                        f"sec-{prefix}",
                        f"sec-{prefix}",
                        f"unique-{prefix}",
                        f"账号{prefix.upper()}",
                        ts + index,
                        ts + index,
                        80000 + (100 if prefix == "b" else 0) + index,
                        f"title {prefix}-{index}",
                        f"desc {prefix}-{index}",
                        f"https://douyin.example/video/{prefix}-{index}",
                    ),
                )
        raw_conn.commit()
    finally:
        raw_conn.close()

    result = import_for_task(str(task["id"]))

    assert result["contents"] == 4
    with database.connect() as conn:
        count_a = conn.execute("SELECT COUNT(*) AS c FROM contents WHERE author_account_id = ?", (account_a,)).fetchone()["c"]
        count_b = conn.execute("SELECT COUNT(*) AS c FROM contents WHERE author_account_id = ?", (account_b,)).fetchone()["c"]
    assert count_a == 2
    assert count_b == 2


def test_keyword_account_analysis_creates_unanalysed_tasks_and_skips_running(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.services import account_actions, crawler_adapter

    with database.connect() as conn:
        target_a = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status, profile_url)
            VALUES('dy', 'target-a', '待分析A', '未分析', 'https://www.douyin.com/user/target-a')
            """
        ).lastrowid
        target_b = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status, profile_url)
            VALUES('dy', 'target-b', '待分析B', '未分析', 'https://www.douyin.com/user/target-b')
            """
        ).lastrowid
        analysed = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status, profile_url)
            VALUES('dy', 'done', '已判定', '竞品', 'https://www.douyin.com/user/done')
            """
        ).lastrowid
        running = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status, profile_url)
            VALUES('dy', 'running', '已排队', '未分析', 'https://www.douyin.com/user/running')
            """
        ).lastrowid
        for account_id, content_id in [(target_a, "a"), (target_b, "b"), (analysed, "c"), (running, "d")]:
            conn.execute(
                "INSERT INTO contents(platform, content_id, author_account_id, title, source_keyword) VALUES('dy', ?, ?, '关键词内容', '电车')",
                (content_id, account_id),
            )

    existing = crawler_adapter.create_profile_enrichment_task(
        int(running),
        mode="account_analysis",
        name_prefix="账号分析",
        content_count=5,
    )
    with database.connect() as conn:
        conn.execute("UPDATE crawl_jobs SET status = 'running' WHERE id = ?", (existing["id"],))

    result = account_actions.create_keyword_account_analysis_tasks("dy", "电车")

    assert result["created"] == 1
    assert result["account_count"] == 2
    assert len(result["task_ids"]) == 1
    assert result["tasks"][0]["task_name"] == "账号分析-2个竞品账号"
    with database.connect() as conn:
        rows = conn.execute(
            """
            SELECT name, creator_id, mode, status, command
            FROM crawl_jobs
            WHERE mode = 'account_analysis'
            ORDER BY id
            """
        ).fetchall()
    pending_rows = [row for row in rows if row["status"] == "pending"]
    assert len(pending_rows) == 1
    assert pending_rows[0]["name"] == "账号分析-2个竞品账号"
    assert pending_rows[0]["creator_id"] == "https://www.douyin.com/user/target-b,https://www.douyin.com/user/target-a"
    assert "--creator_id https://www.douyin.com/user/target-b,https://www.douyin.com/user/target-a" in pending_rows[0]["command"]
    assert all(row["mode"] == "account_analysis" for row in rows)


def test_keyword_find_customer_reuses_existing_contents_before_creator(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.services import account_actions, crawler_adapter
    from app.schemas import TaskCreate

    with database.connect() as conn:
        database.set_setting(conn, "default_content_count", "1")
        competitor_a = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status, profile_url)
            VALUES('dy', 'customer-a', '竞品A', '竞品', 'https://www.douyin.com/user/customer-a')
            """
        ).lastrowid
        competitor_b = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status, profile_url)
            VALUES('dy', 'customer-b', '竞品B', '竞品', 'https://www.douyin.com/user/customer-b')
            """
        ).lastrowid
        non_competitor = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status, profile_url)
            VALUES('dy', 'customer-c', '非竞品', '非竞品', 'https://www.douyin.com/user/customer-c')
            """
        ).lastrowid
        for account_id, content_id in [(competitor_a, "fc-a"), (competitor_b, "fc-b"), (non_competitor, "fc-c")]:
            conn.execute(
                "INSERT INTO contents(platform, content_id, author_account_id, title, source_keyword) VALUES('dy', ?, ?, '关键词内容', '电车')",
                (content_id, account_id),
            )

    running = crawler_adapter.create_task(
        TaskCreate(
            mode="competitor_crawl",
            platform="dy",
            creator_id="https://www.douyin.com/user/customer-b",
            execute_crawler=False,
        )
    )
    with database.connect() as conn:
        conn.execute("UPDATE crawl_jobs SET status = 'running' WHERE id = ?", (running["id"],))

    result = account_actions.create_keyword_find_customer_task("dy", "电车")

    assert result["created"] == 1
    assert result["account_count"] == 1
    assert result["reuse_content_count"] == 1
    assert result["creator_account_count"] == 0
    assert result["tasks"][0]["strategy"] == "reuse_existing_contents"
    assert result["skipped"][0]["reason"] == "已有找客户任务正在等待或运行"
    with database.connect() as conn:
        row = conn.execute(
            "SELECT mode, crawler_type, specified_id, creator_id, collect_comments, collect_sub_comments, command FROM crawl_jobs WHERE id = ?",
            (result["task_ids"][0],),
        ).fetchone()
    assert row["mode"] == "competitor_crawl"
    assert row["crawler_type"] == "detail"
    assert row["specified_id"] == "fc-a"
    assert row["creator_id"] == ""
    assert row["collect_comments"] == 1
    assert row["collect_sub_comments"] == 1
    assert "--specified_id fc-a" in row["command"]


def test_keyword_find_customer_supplements_when_existing_contents_are_insufficient(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.services import account_actions

    with database.connect() as conn:
        database.set_setting(conn, "default_content_count", "2")
        competitor = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status, profile_url)
            VALUES('dy', 'customer-a', '竞品A', '竞品', 'https://www.douyin.com/user/customer-a')
            """
        ).lastrowid
        conn.execute(
            "INSERT INTO contents(platform, content_id, author_account_id, title, source_keyword) VALUES('dy', 'fc-a', ?, '关键词内容', '电车')",
            (competitor,),
        )

    result = account_actions.create_keyword_find_customer_task("dy", "电车")

    assert result["created"] == 2
    assert result["reuse_content_count"] == 1
    assert result["creator_account_count"] == 1
    assert [task["strategy"] for task in result["tasks"]] == ["reuse_existing_contents", "supplement_creator_contents"]
    with database.connect() as conn:
        rows = conn.execute(
            "SELECT crawler_type, specified_id, creator_id, content_count FROM crawl_jobs WHERE id IN (?, ?) ORDER BY id",
            tuple(result["task_ids"]),
        ).fetchall()
    assert rows[0]["crawler_type"] == "detail"
    assert rows[0]["specified_id"] == "fc-a"
    assert rows[1]["crawler_type"] == "creator"
    assert rows[1]["creator_id"] == "https://www.douyin.com/user/customer-a"
    assert rows[1]["content_count"] == 1
def test_overview_shows_account_analysis_progress_statuses(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database, views

    with database.connect() as conn:
        queued = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status, profile_url)
            VALUES('dy', 'queued-account', '采集中账号', '未分析', 'https://www.douyin.com/user/queued-account')
            """
        ).lastrowid
        pending_ai = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status, profile_url)
            VALUES('dy', 'pending-ai-account', 'AI排队账号', '未分析', 'https://www.douyin.com/user/pending-ai-account')
            """
        ).lastrowid
        running_ai = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status, profile_url)
            VALUES('dy', 'running-ai-account', 'AI运行账号', '未分析', 'https://www.douyin.com/user/running-ai-account')
            """
        ).lastrowid
        analysed = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status, profile_url)
            VALUES('dy', 'analysed-account', '已判定账号', '竞品', 'https://www.douyin.com/user/analysed-account')
            """
        ).lastrowid
        for account_id, content_id in [
            (queued, "queued-content"),
            (pending_ai, "pending-ai-content"),
            (running_ai, "running-ai-content"),
            (analysed, "analysed-content"),
        ]:
            conn.execute(
                "INSERT INTO contents(platform, content_id, author_account_id, title, source_keyword) VALUES('dy', ?, ?, '关键词内容', '电车')",
                (content_id, account_id),
            )
        conn.execute(
            """
            INSERT INTO crawl_jobs(id, name, mode, platform, login_type, crawler_type, creator_id, status)
            VALUES('queued-task', '账号分析', 'account_analysis', 'dy', 'qrcode', 'creator', ?, 'running')
            """,
            ("https://www.douyin.com/user/queued-account",),
        )
        conn.execute(
            """
            INSERT INTO analysis_jobs(id, target_type, target_id, status)
            VALUES('pending-ai-job', 'competitor', ?, 'pending')
            """,
            (pending_ai,),
        )
        conn.execute(
            """
            INSERT INTO analysis_jobs(id, target_type, target_id, status)
            VALUES('running-ai-job', 'competitor', ?, 'running')
            """,
            (running_ai,),
        )
        conn.execute(
            """
            INSERT INTO analysis_jobs(id, target_type, target_id, status)
            VALUES('analysed-running-job', 'competitor', ?, 'running')
            """,
            (analysed,),
        )

    tree = views.overview_tree()
    account_rows = {
        account["label"]: account["metrics"]
        for keyword in tree[0]["children"]
        for account in keyword["children"]
    }

    assert account_rows["采集中账号"]["competitor_display_status"] == "排队分析"
    assert account_rows["AI排队账号"]["competitor_display_status"] == "排队分析"
    assert account_rows["AI运行账号"]["competitor_display_status"] == "正在分析"
    assert account_rows["已判定账号"]["competitor_display_status"] == "竞品"


def test_deleting_cancelled_account_analysis_releases_accounts_for_reanalysis(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database, views
    from app.main import app
    from app.schemas import TaskCreate
    from app.services import account_actions, crawler_adapter

    with database.connect() as conn:
        queued = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status, profile_url)
            VALUES('dy', 'queued-again', '重新排队A', '未分析', 'https://www.douyin.com/user/queued-again')
            """
        ).lastrowid
        running = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status, profile_url)
            VALUES('dy', 'running-again', '重新排队B', '未分析', 'https://www.douyin.com/user/running-again')
            """
        ).lastrowid
        for account_id, content_id in [(queued, "queued-again-content"), (running, "running-again-content")]:
            conn.execute(
                "INSERT INTO contents(platform, content_id, author_account_id, title, source_keyword) VALUES('dy', ?, ?, '关键词内容', '电车')",
                (content_id, account_id),
            )
        conn.execute(
            "INSERT INTO analysis_jobs(id, target_type, target_id, status) VALUES('queued-again-job', 'competitor', ?, 'pending')",
            (queued,),
        )
        conn.execute(
            "INSERT INTO analysis_jobs(id, target_type, target_id, status) VALUES('running-again-job', 'competitor', ?, 'running')",
            (running,),
        )

    task = crawler_adapter.create_task(
        TaskCreate(
            mode="account_analysis",
            platform="dy",
            creator_id="https://www.douyin.com/user/queued-again,https://www.douyin.com/user/running-again",
            execute_crawler=False,
        )
    )
    with database.connect() as conn:
        conn.execute("UPDATE crawl_jobs SET status = 'cancelled' WHERE id = ?", (task["id"],))

    before_rows = {
        account["label"]: account["metrics"]["competitor_display_status"]
        for keyword in views.overview_tree()[0]["children"]
        for account in keyword["children"]
    }
    assert before_rows["重新排队A"] == "排队分析"
    assert before_rows["重新排队B"] == "正在分析"

    client = TestClient(app)
    response = client.delete(f"/api/tasks/{task['id']}")

    assert response.status_code == 200
    assert response.json()["analysis_cleanup"]["deleted_jobs"] == 2
    after_rows = {
        account["label"]: account["metrics"]["competitor_display_status"]
        for keyword in views.overview_tree()[0]["children"]
        for account in keyword["children"]
    }
    assert after_rows["重新排队A"] == "未分析"
    assert after_rows["重新排队B"] == "未分析"
    with database.connect() as conn:
        job_count = conn.execute("SELECT COUNT(*) AS c FROM analysis_jobs WHERE id IN ('queued-again-job', 'running-again-job')").fetchone()["c"]
    assert job_count == 0

    result = account_actions.create_keyword_account_analysis_tasks("dy", "电车")

    assert result["created"] == 1
    assert result["account_count"] == 2


def test_auto_analyze_competitors_runs_creator_batch_after_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import account_actions, crawler_adapter

    with database.connect() as conn:
        database.set_setting(conn, "auto_analyze_competitors", "true")

    called: dict[str, object] = {}

    def fake_run_batch(account_ids: list[int], task_id: str) -> None:
        called["account_ids"] = account_ids
        called["task_id"] = task_id

    monkeypatch.setattr(account_actions, "run_account_analysis_batch", fake_run_batch)

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_discovery", platform="dy", keywords="AI客服", execute_crawler=False)
    )
    crawler_adapter.run_task(str(task["id"]))

    assert called["account_ids"]
    with database.connect() as conn:
        rows = conn.execute("SELECT id, mode, creator_id, status FROM crawl_jobs WHERE mode = 'account_analysis'").fetchall()
        ai_jobs = conn.execute("SELECT COUNT(*) AS c FROM analysis_jobs WHERE target_type = 'competitor'").fetchone()["c"]
    assert len(rows) == 1
    assert rows[0]["id"] == called["task_id"]
    assert rows[0]["status"] == "pending"
    assert rows[0]["creator_id"]
    assert ai_jobs == 0


def test_account_analysis_batch_runs_competitor_ai_jobs_parallel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import account_actions, ai_service, crawler_adapter

    task = crawler_adapter.create_task(
        TaskCreate(
            mode="account_analysis",
            platform="dy",
            creator_id="creator-1,creator-2",
            execute_crawler=False,
        )
    )

    def fake_run_task(task_id: str) -> None:
        with database.connect() as conn:
            conn.execute("UPDATE crawl_jobs SET status = 'succeeded' WHERE id = ?", (task_id,))

    created: list[tuple[str, int, bool]] = []
    parallel_called: dict[str, object] = {}

    def fake_create_ai_job(target_type: str, target_id: int, run_now: bool = True) -> dict[str, object]:
        created.append((target_type, target_id, run_now))
        return {"id": f"{target_type}-{target_id}"}

    def fake_run_parallel(job_ids: list[str], max_workers: int | None = None) -> dict[str, object]:
        parallel_called["job_ids"] = job_ids
        parallel_called["max_workers"] = max_workers
        return {"total": len(job_ids), "succeeded": len(job_ids), "failed": 0, "auto_deleted": 0, "errors": []}

    monkeypatch.setattr(crawler_adapter, "run_task", fake_run_task)
    monkeypatch.setattr(ai_service, "create_ai_job", fake_create_ai_job)
    monkeypatch.setattr(ai_service, "run_ai_jobs_parallel", fake_run_parallel)

    account_actions.run_account_analysis_batch([11, 12, 13], str(task["id"]))

    assert created == [("competitor", 11, False), ("competitor", 12, False), ("competitor", 13, False)]
    assert parallel_called["job_ids"] == ["competitor-11", "competitor-12", "competitor-13"]


def test_retry_account_analysis_accepts_multiple_creator_ids(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.main import app

    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status, profile_url)
            VALUES('dy', 'multi-a', '待分析A', '未分析', 'https://www.douyin.com/user/multi-a')
            """
        )
        conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status, profile_url)
            VALUES('dy', 'multi-b', '待分析B', '未分析', 'https://www.douyin.com/user/multi-b')
            """
        )

    client = TestClient(app)
    response = client.post(
        "/api/tasks",
        json={
            "mode": "account_analysis",
            "platform": "dy",
            "creator_id": "https://www.douyin.com/user/multi-a,https://www.douyin.com/user/multi-b",
            "content_count": 2,
            "comment_count": 0,
            "collect_comments": False,
            "collect_sub_comments": False,
            "execute_crawler": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "账号分析-2个竞品账号"
    assert payload["creator_id"] == "https://www.douyin.com/user/multi-a,https://www.douyin.com/user/multi-b"
    assert payload["mode"] == "account_analysis"
    assert payload["crawler_type"] == "creator"


def test_account_analysis_progress_stops_after_target_content_count(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app.schemas import TaskCreate
    from app.services import crawler_adapter

    task = crawler_adapter.create_task(
        TaskCreate(mode="account_analysis", platform="dy", creator_id="creator-1", content_count=2, execute_crawler=False)
    )
    progress = {"creator": False, "contents": 0}
    assert crawler_adapter._should_stop_account_analysis(task, "MediaCrawler INFO [store.douyin.save_creator] creator:{}", progress) is False
    assert crawler_adapter._should_stop_account_analysis(task, "MediaCrawler INFO [store.douyin.update_douyin_aweme] douyin aweme id:1", progress) is False
    assert crawler_adapter._should_stop_account_analysis(task, "MediaCrawler INFO [store.douyin.update_douyin_aweme] douyin aweme id:2", progress) is True

    multi_task = crawler_adapter.create_task(
        TaskCreate(mode="account_analysis", platform="dy", creator_id="creator-1,creator-2", content_count=2, execute_crawler=False)
    )
    multi_progress = {"creator": True, "contents": 99}
    assert crawler_adapter._should_stop_account_analysis(multi_task, "MediaCrawler INFO [store.douyin.update_douyin_aweme] douyin aweme id:99", multi_progress) is False


def test_account_analysis_subprocess_env_limits_douyin_creator_videos(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app.schemas import TaskCreate
    from app.services import crawler_adapter

    task = crawler_adapter.create_task(
        TaskCreate(
            mode="account_analysis",
            platform="dy",
            creator_id="creator-1,creator-2",
            content_count=3,
            execute_crawler=False,
        )
    )
    env = crawler_adapter._media_crawler_subprocess_env({"PYTHONPATH": "existing-path"}, task)

    assert env["AI_CUSTOMER_DY_CREATOR_VIDEO_LIMIT"] == "3"
    assert "mediacrawler_shims" in env["PYTHONPATH"]
    assert env["PYTHONPATH"].endswith("existing-path")

    customer_task = crawler_adapter.create_task(
        TaskCreate(
            mode="competitor_crawl",
            platform="dy",
            creator_id="creator-1",
            content_count=5,
            collect_comments=True,
            execute_crawler=False,
        )
    )
    customer_env = crawler_adapter._media_crawler_subprocess_env({}, customer_task)

    assert customer_env["AI_CUSTOMER_DY_CREATOR_VIDEO_LIMIT"] == "5"
    assert "mediacrawler_shims" in customer_env["PYTHONPATH"]

    xhs_task = crawler_adapter.create_task(
        TaskCreate(
            mode="account_analysis",
            platform="xhs",
            creator_id="https://www.xiaohongshu.com/user/profile/test",
            content_count=3,
            execute_crawler=False,
        )
    )
    xhs_env = crawler_adapter._media_crawler_subprocess_env({}, xhs_task)

    assert "AI_CUSTOMER_DY_CREATOR_VIDEO_LIMIT" not in xhs_env
    assert "PYTHONPATH" not in xhs_env


def test_content_cutoff_subprocess_env_applies_without_comment_collection(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import crawler_adapter

    with database.connect() as conn:
        database.set_setting(conn, "content_cutoff_days", "5")
    task = crawler_adapter.create_task(
        TaskCreate(
            mode="account_analysis",
            platform="dy",
            creator_id="creator-1",
            content_count=3,
            collect_comments=False,
            execute_crawler=False,
        )
    )

    env = crawler_adapter._media_crawler_subprocess_env({}, task)

    assert int(env["AI_CUSTOMER_CONTENT_CUTOFF_TS"]) > 0
    assert "AI_CUSTOMER_COMMENT_CUTOFF_TS" not in env
    assert env["AI_CUSTOMER_DY_CREATOR_VIDEO_LIMIT"] == "3"
    assert "mediacrawler_shims" in env["PYTHONPATH"]


def test_cdp_existing_mode_auto_launches_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import crawler_adapter

    media_dir = tmp_path / "MediaCrawler"
    config_dir = media_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "base_config.py").write_text(
        "\n".join(
            [
                "ENABLE_CDP_MODE = True",
                "CDP_CONNECT_EXISTING = True",
                "CDP_DEBUG_PORT = 9333",
                'CUSTOM_BROWSER_PATH = ""',
            ]
        ),
        encoding="utf-8",
    )

    popen_calls: list[list[str]] = []

    class FakeProcess:
        pid = 12345

    def fake_popen(args: list[str], **_: object) -> FakeProcess:
        popen_calls.append(args)
        return FakeProcess()

    monkeypatch.setattr(crawler_adapter, "_is_tcp_port_open", lambda host, port: False)
    monkeypatch.setattr(crawler_adapter, "_wait_for_tcp_port", lambda host, port, timeout_seconds: True)
    monkeypatch.setattr(crawler_adapter, "_detect_browser_path", lambda custom_browser_path="": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe")
    monkeypatch.setattr(crawler_adapter.platform, "system", lambda: "Windows")
    monkeypatch.setattr(crawler_adapter.subprocess, "Popen", fake_popen)

    message = crawler_adapter._ensure_cdp_browser_for_existing_mode(media_dir, headless=False)

    assert "9333" in str(message)
    assert popen_calls
    assert "--remote-debugging-port=9333" in popen_calls[0]
    assert any(item.startswith("--user-data-dir=") for item in popen_calls[0])


def test_media_crawler_sqlite_schema_auto_initializes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import crawler_adapter

    media_dir = tmp_path / "MediaCrawler"
    db_path = media_dir / "database" / "sqlite_tables.db"
    db_path.parent.mkdir(parents=True)
    db_path.touch()
    run_calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        run_calls.append(command)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE douyin_aweme (id INTEGER PRIMARY KEY)")
            conn.commit()
        finally:
            conn.close()
        return subprocess.CompletedProcess(command, 0, stdout="Database sqlite initialized successfully.")

    monkeypatch.setattr(crawler_adapter.subprocess, "run", fake_run)

    message = crawler_adapter._ensure_media_crawler_sqlite_schema(media_dir, "dy", {"PYTHONIOENCODING": "utf-8"})

    assert run_calls == [["uv", "run", "main.py", "--init_db", "sqlite"]]
    assert "douyin_aweme" in str(message)


def test_recover_interrupted_running_tasks_marks_failed(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import crawler_adapter

    task = crawler_adapter.create_task(
        TaskCreate(mode="account_analysis", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    with database.connect() as conn:
        conn.execute("UPDATE crawl_jobs SET status = 'running', process_id = 999999 WHERE id = ?", (task["id"],))

    recovered = crawler_adapter.recover_interrupted_running_tasks()
    assert recovered == 1
    with database.connect() as conn:
        row = conn.execute("SELECT status, error FROM crawl_jobs WHERE id = ?", (task["id"],)).fetchone()
        log_count = conn.execute("SELECT COUNT(*) AS c FROM task_logs WHERE task_id = ? AND level = 'error'", (task["id"],)).fetchone()["c"]
    assert row["status"] == "failed"
    assert "running" in row["error"]
    assert log_count >= 1


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


def test_delete_keyword_non_competitors_records_tombstones_and_keeps_raw_rows(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import account_actions, crawler_adapter
    from app.services.importer import import_for_task

    discovery = crawler_adapter.create_task(
        TaskCreate(mode="competitor_discovery", platform="dy", keywords="AI客服", execute_crawler=False)
    )
    import_for_task(str(discovery["id"]))
    with database.connect() as conn:
        account_id = conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'creator-1'").fetchone()["id"]
        conn.execute("UPDATE user_accounts SET competitor_status = '非竞品' WHERE id = ?", (account_id,))

    result = account_actions.delete_keyword_non_competitors("dy", "AI客服")
    assert result["deleted"] == 1
    assert result["raw_refs"] >= 1

    with database.connect() as conn:
        account_count = conn.execute("SELECT COUNT(*) AS c FROM user_accounts WHERE id = ?", (account_id,)).fetchone()["c"]
        audit_count = conn.execute("SELECT COUNT(*) AS c FROM deletion_audit WHERE entity_type = 'keyword_non_competitors'").fetchone()["c"]
        tombstone_count = conn.execute("SELECT COUNT(*) AS c FROM deleted_identities WHERE entity_type = 'author_account'").fetchone()["c"]
    assert account_count == 0
    assert audit_count == 1
    assert tombstone_count >= 1

    raw_conn = sqlite3.connect(raw_db)
    try:
        creator_raw_count = raw_conn.execute("SELECT COUNT(*) FROM dy_creator WHERE user_id = 'creator-1'").fetchone()[0]
        content_raw_count = raw_conn.execute("SELECT COUNT(*) FROM douyin_aweme WHERE user_id = 'creator-1'").fetchone()[0]
    finally:
        raw_conn.close()
    assert creator_raw_count == 1
    assert content_raw_count == 1

    retry = crawler_adapter.create_task(
        TaskCreate(mode="competitor_discovery", platform="dy", keywords="AI客服", execute_crawler=False)
    )
    import_for_task(str(retry["id"]))
    with database.connect() as conn:
        recreated = conn.execute("SELECT COUNT(*) AS c FROM user_accounts WHERE platform_user_id = 'creator-1'").fetchone()["c"]
    assert recreated == 0


def test_delete_overview_keyword_records_tombstones_and_keeps_raw_rows(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import crawler_adapter, deletion
    from app.services.importer import import_for_task

    raw_conn = sqlite3.connect(raw_db)
    try:
        raw_conn.execute(
            """
            INSERT INTO douyin_aweme VALUES (
                'creator-2', 'sec-2', '', 'unique-2', 'CRM竞品号',
                '', '专注CRM系统', '', 1, 1, 10002, 'video',
                'CRM怎么选', '另一条关键词内容', 1, '8', '0',
                '0', '0', 'https://douyin.example/video/10002', '', '', '', '', 'CRM'
            )
            """
        )
        raw_conn.execute(
            """
            INSERT INTO dy_creator VALUES (
                'creator-2', 'CRM竞品号', '', '', 1, 1,
                '专注CRM系统', '', '3', '300', '11', '4'
            )
            """
        )
        raw_conn.commit()
    finally:
        raw_conn.close()

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_discovery", platform="dy", keywords="AI客服", execute_crawler=False)
    )
    import_for_task(str(task["id"]))

    result = deletion.delete_overview_keyword("dy", "AI客服")
    assert result["counts"]["contents"] == 1
    assert result["counts"]["accounts"] == 1
    assert result["raw_refs"] >= 2

    with database.connect() as conn:
        ai_content_count = conn.execute(
            "SELECT COUNT(*) AS c FROM contents WHERE source_keyword = 'AI客服'"
        ).fetchone()["c"]
        crm_content_count = conn.execute(
            "SELECT COUNT(*) AS c FROM contents WHERE source_keyword = 'CRM'"
        ).fetchone()["c"]
        deleted_account = conn.execute(
            "SELECT COUNT(*) AS c FROM user_accounts WHERE platform_user_id = 'creator-1'"
        ).fetchone()["c"]
        kept_account = conn.execute(
            "SELECT COUNT(*) AS c FROM user_accounts WHERE platform_user_id = 'creator-2'"
        ).fetchone()["c"]
        audit_count = conn.execute(
            "SELECT COUNT(*) AS c FROM deletion_audit WHERE entity_type = 'overview_keyword'"
        ).fetchone()["c"]
        tombstone_count = conn.execute("SELECT COUNT(*) AS c FROM deleted_identities").fetchone()["c"]
    assert ai_content_count == 0
    assert crm_content_count == 1
    assert deleted_account == 0
    assert kept_account == 1
    assert audit_count == 1
    assert tombstone_count >= 2

    raw_conn = sqlite3.connect(raw_db)
    try:
        deleted_raw = raw_conn.execute("SELECT COUNT(*) FROM douyin_aweme WHERE aweme_id = 10001").fetchone()[0]
        kept_raw = raw_conn.execute("SELECT COUNT(*) FROM douyin_aweme WHERE aweme_id = 10002").fetchone()[0]
    finally:
        raw_conn.close()
    assert deleted_raw == 1
    assert kept_raw == 1

    retry = crawler_adapter.create_task(
        TaskCreate(mode="competitor_discovery", platform="dy", keywords="AI客服", execute_crawler=False)
    )
    import_for_task(str(retry["id"]))
    with database.connect() as conn:
        recreated_content = conn.execute("SELECT COUNT(*) AS c FROM contents WHERE content_id = '10001'").fetchone()["c"]
        recreated_account = conn.execute("SELECT COUNT(*) AS c FROM user_accounts WHERE platform_user_id = 'creator-1'").fetchone()["c"]
    assert recreated_content == 0
    assert recreated_account == 0


def test_delete_overview_platform_records_tombstones_and_keeps_raw_rows(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import crawler_adapter, deletion
    from app.services.importer import import_for_task

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(task["id"]))

    result = deletion.delete_overview_platform("dy")
    assert result["counts"]["contents"] == 1
    assert result["counts"]["comments"] == 1
    assert result["counts"]["accounts"] == 2
    assert result["counts"]["leads"] == 1

    with database.connect() as conn:
        for table in ["contents", "comments", "user_accounts", "lead_user_accounts", "lead_sources", "raw_source_refs"]:
            assert conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"] == 0
        audit_count = conn.execute(
            "SELECT COUNT(*) AS c FROM deletion_audit WHERE entity_type = 'overview_platform'"
        ).fetchone()["c"]
        tombstone_count = conn.execute("SELECT COUNT(*) AS c FROM deleted_identities").fetchone()["c"]
    assert audit_count == 1
    assert tombstone_count >= 3

    raw_conn = sqlite3.connect(raw_db)
    try:
        content_raw_count = raw_conn.execute("SELECT COUNT(*) FROM douyin_aweme").fetchone()[0]
        comment_raw_count = raw_conn.execute("SELECT COUNT(*) FROM douyin_aweme_comment").fetchone()[0]
        creator_raw_count = raw_conn.execute("SELECT COUNT(*) FROM dy_creator").fetchone()[0]
    finally:
        raw_conn.close()
    assert content_raw_count == 1
    assert comment_raw_count == 1
    assert creator_raw_count == 1

    retry = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    result = import_for_task(str(retry["id"]))
    assert result["contents"] == 0
    assert result["comments"] == 0


def test_delete_overview_account_allows_content_derived_account_without_creator_ref(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import crawler_adapter, deletion
    from app.services.importer import import_for_task

    discovery = crawler_adapter.create_task(
        TaskCreate(mode="competitor_discovery", platform="dy", keywords="AI客服", execute_crawler=False)
    )
    import_for_task(str(discovery["id"]))

    with database.connect() as conn:
        account_id = int(conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'creator-1'").fetchone()["id"])
        conn.execute("DELETE FROM raw_source_refs WHERE entity_type = 'account' AND entity_id = ?", (account_id,))

    result = deletion.delete_overview_account(account_id)
    assert result["counts"]["accounts"] == 1
    assert result["counts"]["contents"] == 1
    assert result["raw_refs"] == 1

    with database.connect() as conn:
        account_count = conn.execute("SELECT COUNT(*) AS c FROM user_accounts WHERE id = ?", (account_id,)).fetchone()["c"]
        content_count = conn.execute("SELECT COUNT(*) AS c FROM contents").fetchone()["c"]
        audit_count = conn.execute("SELECT COUNT(*) AS c FROM deletion_audit WHERE entity_type = 'overview_account'").fetchone()["c"]
        tombstone_count = conn.execute("SELECT COUNT(*) AS c FROM deleted_identities").fetchone()["c"]
    assert account_count == 0
    assert content_count == 0
    assert audit_count == 1
    assert tombstone_count >= 2

    raw_conn = sqlite3.connect(raw_db)
    try:
        content_raw_count = raw_conn.execute("SELECT COUNT(*) FROM douyin_aweme WHERE aweme_id = 10001").fetchone()[0]
        creator_raw_count = raw_conn.execute("SELECT COUNT(*) FROM dy_creator WHERE user_id = 'creator-1'").fetchone()[0]
    finally:
        raw_conn.close()
    assert content_raw_count == 1
    assert creator_raw_count == 1

    retry = crawler_adapter.create_task(
        TaskCreate(mode="competitor_discovery", platform="dy", keywords="AI客服", execute_crawler=False)
    )
    import_for_task(str(retry["id"]))
    with database.connect() as conn:
        recreated_content = conn.execute("SELECT COUNT(*) AS c FROM contents WHERE content_id = '10001'").fetchone()["c"]
    assert recreated_content == 0


def test_deleted_content_and_comment_tombstones_skip_future_import(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import crawler_adapter, deletion
    from app.services.importer import import_for_task

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(task["id"]))

    with database.connect() as conn:
        content_id = int(conn.execute("SELECT id FROM contents WHERE content_id = '10001'").fetchone()["id"])
    result = deletion.delete_library_row("contents", content_id, hard=True)
    assert result["tombstones"]["contents"] == 1
    assert result["tombstones"]["comments"] == 1

    raw_conn = sqlite3.connect(raw_db)
    try:
        raw_content = raw_conn.execute("SELECT COUNT(*) FROM douyin_aweme WHERE aweme_id = 10001").fetchone()[0]
        raw_comment = raw_conn.execute("SELECT COUNT(*) FROM douyin_aweme_comment WHERE comment_id = 90001").fetchone()[0]
    finally:
        raw_conn.close()
    assert raw_content == 1
    assert raw_comment == 1

    retry = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(retry["id"]))
    with database.connect() as conn:
        content_count = conn.execute("SELECT COUNT(*) AS c FROM contents WHERE content_id = '10001'").fetchone()["c"]
        comment_count = conn.execute("SELECT COUNT(*) AS c FROM comments WHERE comment_id = '90001'").fetchone()["c"]
    assert content_count == 0
    assert comment_count == 0


def test_deleted_non_competitor_author_skips_future_authored_content(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import account_actions, crawler_adapter
    from app.services.importer import import_for_task

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_discovery", platform="dy", keywords="AI客服", execute_crawler=False)
    )
    import_for_task(str(task["id"]))
    with database.connect() as conn:
        account_id = conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'creator-1'").fetchone()["id"]
        conn.execute("UPDATE user_accounts SET competitor_status = '非竞品' WHERE id = ?", (account_id,))

    deleted = account_actions.delete_keyword_non_competitors("dy", "AI客服")
    assert deleted["deleted"] == 1

    raw_conn = sqlite3.connect(raw_db)
    try:
        raw_conn.execute(
            """
            INSERT INTO douyin_aweme VALUES (
                'creator-1', 'sec-1', '', 'unique-1', 'AI客服竞品号',
                '', '专注AI客服系统', '', 2, 2, 10002, 'video',
                'AI客服新增内容', '删除后又被关键词搜索到', 2, '18', '2',
                '0', '0', 'https://douyin.example/video/10002', '', '', '', '', 'AI客服'
            )
            """
        )
        raw_conn.commit()
    finally:
        raw_conn.close()

    retry = crawler_adapter.create_task(
        TaskCreate(mode="competitor_discovery", platform="dy", keywords="AI客服", execute_crawler=False)
    )
    result = import_for_task(str(retry["id"]))

    assert result["contents"] == 0
    with database.connect() as conn:
        new_content = conn.execute("SELECT 1 FROM contents WHERE content_id = '10002'").fetchone()
        tombstone = conn.execute(
            """
            SELECT 1
            FROM deleted_identities
            WHERE entity_type = 'author_account'
              AND identifier_type = 'platform_user_id'
              AND identifier_value = 'creator-1'
            """
        ).fetchone()
    assert new_content is None
    assert tombstone is not None


def test_deleted_customer_allows_future_new_comment(tmp_path: Path) -> None:
    _, raw_db = prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import crawler_adapter, deletion
    from app.services.importer import import_for_task

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(task["id"]))
    with database.connect() as conn:
        lead_id = int(conn.execute("SELECT id FROM lead_user_accounts LIMIT 1").fetchone()["id"])

    deleted = deletion.delete_lead_customer(lead_id)
    assert deleted["counts"]["accounts"] == 1
    assert deleted["tombstones"]["accounts"] == 0
    assert deleted["tombstones"]["comments"] == 1

    raw_conn = sqlite3.connect(raw_db)
    try:
        raw_conn.execute(
            """
            INSERT INTO douyin_aweme_comment VALUES (
                'lead-1', 'lead-sec-1', '', 'lead-unique-1', '准备采购的老板',
                '', '正在找替代方案', '', 2, 2, 90002, 10001,
                '现在有预算，报价多少？', 2, '0', '', '5', ''
            )
            """
        )
        raw_conn.commit()
    finally:
        raw_conn.close()

    retry = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    result = import_for_task(str(retry["id"]))

    assert result["comments"] == 1
    assert result["leads"] == 1
    with database.connect() as conn:
        old_comment = conn.execute("SELECT 1 FROM comments WHERE comment_id = '90001'").fetchone()
        new_comment = conn.execute("SELECT 1 FROM comments WHERE comment_id = '90002'").fetchone()
        lead = conn.execute(
            """
            SELECT lua.id
            FROM lead_user_accounts lua
            JOIN user_accounts ua ON ua.id = lua.account_id
            WHERE ua.platform_user_id = 'lead-1'
            """
        ).fetchone()
        author_tombstone = conn.execute(
            """
            SELECT 1
            FROM deleted_identities
            WHERE entity_type = 'author_account'
              AND identifier_type = 'platform_user_id'
              AND identifier_value = 'lead-1'
            """
        ).fetchone()
    assert old_comment is None
    assert new_comment is not None
    assert lead is not None
    assert author_tombstone is None


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


def test_update_customer_follow_status_records_manual_event(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.main import app

    with database.connect() as conn:
        account_id = conn.execute(
            "INSERT INTO user_accounts(platform, platform_user_id, nickname) VALUES('dy', 'manual-lead', '人工跟进客户')"
        ).lastrowid
        lead_id = conn.execute("INSERT INTO lead_user_accounts(account_id) VALUES(?)", (account_id,)).lastrowid

    client = TestClient(app)
    response = client.patch(
        f"/api/overview/customers/{lead_id}/follow-status",
        json={"follow_status": "未私信", "note": "人工确认是目标客户"},
    )

    assert response.status_code == 200
    assert response.json()["follow_status"] == "未私信"
    assert response.json()["screening_status"] == "目标客户"
    assert response.json()["manual_follow_status"] == 1
    with database.connect() as conn:
        row = conn.execute(
            "SELECT screening_status, follow_status, manual_follow_status FROM lead_user_accounts WHERE id = ?",
            (lead_id,),
        ).fetchone()
        event = conn.execute(
            "SELECT from_status, to_status, note FROM lead_status_events WHERE lead_account_id = ?",
            (lead_id,),
        ).fetchone()
    assert row["screening_status"] == "目标客户"
    assert row["follow_status"] == "未私信"
    assert row["manual_follow_status"] == 1
    assert event["from_status"] == "待筛选"
    assert event["to_status"] == "未私信"
    assert event["note"] == "人工确认是目标客户"

    private_message_response = client.patch(
        f"/api/overview/customers/{lead_id}/follow-status",
        json={"follow_status": "已私信", "note": "已经复制话术并打开主页"},
    )
    assert private_message_response.status_code == 200
    assert private_message_response.json()["follow_status"] == "已私信"

    back_to_unmessaged_after_message_response = client.patch(
        f"/api/overview/customers/{lead_id}/follow-status",
        json={"follow_status": "未私信", "note": "误操作，改回未私信"},
    )
    assert back_to_unmessaged_after_message_response.status_code == 200
    assert back_to_unmessaged_after_message_response.json()["follow_status"] == "未私信"

    waiting_response = client.patch(
        f"/api/overview/customers/{lead_id}/follow-status",
        json={"follow_status": "未回复", "note": "已经私信，等待回复"},
    )
    assert waiting_response.status_code == 200
    assert waiting_response.json()["follow_status"] == "未回复"

    back_to_unmessaged_response = client.patch(
        f"/api/overview/customers/{lead_id}/follow-status",
        json={"follow_status": "未私信", "note": "误操作，改回未私信"},
    )
    assert back_to_unmessaged_response.status_code == 200
    assert back_to_unmessaged_response.json()["follow_status"] == "未私信"

    reset_response = client.patch(
        f"/api/overview/customers/{lead_id}/follow-status",
        json={"follow_status": "待筛选", "note": "重新交给AI判断"},
    )
    assert reset_response.status_code == 200
    assert reset_response.json()["manual_follow_status"] == 0
    with database.connect() as conn:
        reset_row = conn.execute(
            "SELECT screening_status, follow_status, manual_follow_status FROM lead_user_accounts WHERE id = ?",
            (lead_id,),
        ).fetchone()
    assert reset_row["screening_status"] == "待筛选"
    assert reset_row["follow_status"] == "待筛选"
    assert reset_row["manual_follow_status"] == 0


def test_message_workbench_keyword_queue_and_global_follow_status(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.main import app

    with database.connect() as conn:
        database.set_setting(conn, "unreplied_reminder_days", "3")
        source_account_id = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, profile_url)
            VALUES('dy', 'competitor-a', '竞品账号A', 'https://example.com/source')
            """
        ).lastrowid
        content_id = conn.execute(
            """
            INSERT INTO contents(platform, content_id, author_account_id, title, description, content_url, source_keyword)
            VALUES('dy', 'content-a', ?, '电动车定制案例', '评论区询价很多', 'https://example.com/video', '电动车')
            """,
            (source_account_id,),
        ).lastrowid
        lead_account_id = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, profile_url, signature)
            VALUES('dy', 'lead-global', '多关键词客户', 'https://example.com/lead', '准备采购')
            """
        ).lastrowid
        lead_id = conn.execute(
            """
            INSERT INTO lead_user_accounts(
                account_id, screening_status, follow_status, intention, reason, script, updated_at
            )
            VALUES(?, '目标客户', '未回复', '高', '询问价格', '你好，可以发你方案。', '2026-06-30 10:00:00')
            """,
            (lead_account_id,),
        ).lastrowid
        comment_id = conn.execute(
            """
            INSERT INTO comments(platform, comment_id, content_id, author_account_id, body, raw_payload, created_at)
            VALUES('dy', 'comment-a', ?, ?, '多少钱？', ?, '2026-06-29 10:00:00')
            """,
            (content_id, lead_account_id, json.dumps({"create_time": 1710000000})),
        ).lastrowid
        for keyword in ("电动车", "巡游花车"):
            conn.execute(
                """
                INSERT INTO lead_sources(
                    lead_account_id, source_account_id, content_id, comment_id, keyword, source_type, created_at
                )
                VALUES(?, ?, ?, ?, ?, 'competitor_crawl', '2026-06-30 10:00:00')
                """,
                (lead_id, source_account_id, content_id, comment_id, keyword),
            )
        conn.execute(
            """
            INSERT INTO lead_status_events(lead_account_id, from_status, to_status, note, created_at)
            VALUES(?, '未私信', '已私信', '已发私信', '2020-01-01 00:00:00')
            """,
            (lead_id,),
        )

        unmessaged_account_id = conn.execute(
            "INSERT INTO user_accounts(platform, platform_user_id, nickname) VALUES('dy', 'lead-unmessaged', '待私信客户')"
        ).lastrowid
        unmessaged_lead_id = conn.execute(
            """
            INSERT INTO lead_user_accounts(account_id, screening_status, follow_status, script)
            VALUES(?, '目标客户', '未私信', '可以沟通需求。')
            """,
            (unmessaged_account_id,),
        ).lastrowid
        unmessaged_comment_id = conn.execute(
            """
            INSERT INTO comments(platform, comment_id, content_id, author_account_id, body)
            VALUES('dy', 'comment-b', ?, ?, '能定制吗？')
            """,
            (content_id, unmessaged_account_id),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO lead_sources(lead_account_id, source_account_id, content_id, comment_id, keyword, source_type)
            VALUES(?, ?, ?, ?, '', 'competitor_crawl')
            """,
            (unmessaged_lead_id, source_account_id, content_id, unmessaged_comment_id),
        )

        hidden_account_id = conn.execute(
            "INSERT INTO user_accounts(platform, platform_user_id, nickname) VALUES('dy', 'lead-hidden', '隐藏客户')"
        ).lastrowid
        hidden_lead_id = conn.execute(
            """
            INSERT INTO lead_user_accounts(account_id, screening_status, follow_status, hidden)
            VALUES(?, '目标客户', '未私信', 1)
            """,
            (hidden_account_id,),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO lead_sources(lead_account_id, source_account_id, content_id, keyword, source_type)
            VALUES(?, ?, ?, '电动车', 'competitor_crawl')
            """,
            (hidden_lead_id, source_account_id, content_id),
        )

        non_customer_account_id = conn.execute(
            "INSERT INTO user_accounts(platform, platform_user_id, nickname) VALUES('dy', 'lead-non', '非客户')"
        ).lastrowid
        non_customer_lead_id = conn.execute(
            """
            INSERT INTO lead_user_accounts(account_id, screening_status, follow_status)
            VALUES(?, '非客户', '非客户')
            """,
            (non_customer_account_id,),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO lead_sources(lead_account_id, source_account_id, content_id, keyword, source_type)
            VALUES(?, ?, ?, '电动车', 'competitor_crawl')
            """,
            (non_customer_lead_id, source_account_id, content_id),
        )

    client = TestClient(app)
    keywords = client.get("/api/message-workbench/keywords").json()
    all_keyword = keywords[0]
    electric = next(item for item in keywords if item["keyword"] == "电动车")
    parade = next(item for item in keywords if item["keyword"] == "巡游花车")
    assert all(item["keyword"] != "未标记关键词" for item in keywords[1:])
    assert all_keyword["customer_count"] == 2
    assert electric["customer_count"] == 2
    assert electric["unmessaged_count"] == 1
    assert electric["overdue_count"] == 1
    assert parade["customer_count"] == 1

    waiting_list = client.get(
        "/api/message-workbench/customers",
        params={"keyword": "电动车", "status": "待私信", "page": 1, "page_size": 10},
    ).json()
    assert waiting_list["total"] == 1
    assert waiting_list["rows"][0]["lead_id"] == unmessaged_lead_id

    overdue_list = client.get(
        "/api/message-workbench/customers",
        params={"keyword": "巡游花车", "status": "未回复", "page": 1, "page_size": 10},
    ).json()
    assert overdue_list["total"] == 1
    assert overdue_list["rows"][0]["lead_id"] == lead_id
    assert overdue_list["rows"][0]["overdue"] is True
    assert overdue_list["rows"][0]["private_message_at"] == "2020-01-01 00:00:00"

    detail = client.get(f"/api/message-workbench/customers/{lead_id}").json()
    assert len(detail["sources"]) == 2
    assert detail["events"][0]["to_status"] == "已私信"

    response = client.patch(
        f"/api/overview/customers/{lead_id}/follow-status",
        json={"follow_status": "已回复", "note": "客户回复了"},
    )
    assert response.status_code == 200
    for keyword in ("电动车", "巡游花车"):
        replied = client.get(
            "/api/message-workbench/customers",
            params={"keyword": keyword, "status": "已回复", "page": 1, "page_size": 10},
        ).json()
        assert replied["total"] == 1
        assert replied["rows"][0]["lead_id"] == lead_id
        assert replied["rows"][0]["reply_at"]


def test_ai_result_does_not_override_manual_follow_status(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.services import account_actions, ai_service

    with database.connect() as conn:
        account_id = conn.execute(
            "INSERT INTO user_accounts(platform, platform_user_id, nickname) VALUES('dy', 'manual-ai-lead', '人工保护客户')"
        ).lastrowid
        lead_id = conn.execute("INSERT INTO lead_user_accounts(account_id) VALUES(?)", (account_id,)).lastrowid

    account_actions.update_customer_follow_status(int(lead_id), "未私信", "人工确认")
    account_actions.update_customer_follow_status(int(lead_id), "未回复", "已经私信")
    with database.connect() as conn:
        ai_service.apply_ai_result(
            conn,
            "lead",
            int(lead_id),
            {
                "is_customer": False,
                "intention": "低",
                "reason": "AI认为不是客户",
                "pain_points": [],
                "suggested_action": "不跟进",
                "script": "",
            },
        )
        row = conn.execute(
            "SELECT screening_status, follow_status, reason, manual_follow_status FROM lead_user_accounts WHERE id = ?",
            (lead_id,),
        ).fetchone()
    assert row["screening_status"] == "目标客户"
    assert row["follow_status"] == "未回复"
    assert row["reason"] == "AI认为不是客户"
    assert row["manual_follow_status"] == 1


def test_customer_intent_analysis_marks_non_customer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_project(tmp_path)
    from app import database, views
    from app.schemas import TaskCreate
    from app.services import account_actions, ai_service, crawler_adapter
    from app.services.importer import import_for_task

    with database.connect() as conn:
        database.set_setting(conn, "ai_base_url", "https://example.test")
        database.set_setting(conn, "ai_api_key", "test-key")
        database.set_setting(conn, "ai_model", "test-model")

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(task["id"]))
    lead_id = views.list_library("lead_customers")["rows"][0]["id"]

    monkeypatch.setattr(
        ai_service,
        "call_openai_compatible",
        lambda *_args, **_kwargs: '{"is_customer": false, "intention": "低", "reason": "只是询问，没有采购信号", "pain_points": [], "suggested_action": "不跟进", "script": ""}',
    )

    result = account_actions.create_customer_intent_analysis(int(lead_id), run_now=True)

    assert result["status"] == "succeeded"
    with database.connect() as conn:
        row = conn.execute("SELECT screening_status, follow_status FROM lead_user_accounts WHERE id = ?", (lead_id,)).fetchone()
    assert row["screening_status"] == "非客户"
    assert row["follow_status"] == "非客户"
    lead_rows = views.list_library("lead_customers", status="非客户")["rows"]
    assert len(lead_rows) == 1


def test_lead_ai_prompt_treats_competitor_comment_price_question_as_intent(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import ai_service, crawler_adapter
    from app.services.importer import import_for_task

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(task["id"]))

    with database.connect() as conn:
        lead_id = int(conn.execute("SELECT id FROM lead_user_accounts LIMIT 1").fetchone()["id"])
        payload = ai_service.build_input_payload(conn, "lead", lead_id)

    assert payload["icp"]["company_name"] == ""
    assert "询价" in payload["intent_signals"]
    assert payload["evidence"][0]["source_account_nickname"] == "AI客服竞品号"
    _, user_prompt = ai_service.build_ai_messages("lead", payload)
    assert "评论者询问价格" in user_prompt
    assert "不要因为评论没有直接写出ICP关键词" in user_prompt
    assert "通常应判定为目标客户" in user_prompt
    assert "company_name/公司名是可选字段" in user_prompt
    assert "script不得出现任何公司名" in user_prompt
    assert "我们公司" in user_prompt
    assert ai_service.prompt_version_for("lead") == "lead_v2"


def test_lead_ai_prompt_uses_optional_company_name_when_provided(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import ai_service, crawler_adapter
    from app.services.importer import import_for_task

    with database.connect() as conn:
        database.set_setting(
            conn,
            "icp_profile",
            json.dumps(
                {"product": "AI客服", "company_name": "水舟科技", "value_proposition": "降低客服人力成本"},
                ensure_ascii=False,
            ),
        )

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(task["id"]))

    with database.connect() as conn:
        lead_id = int(conn.execute("SELECT id FROM lead_user_accounts LIMIT 1").fetchone()["id"])
        payload = ai_service.build_input_payload(conn, "lead", lead_id)

    assert payload["icp"]["company_name"] == "水舟科技"
    _, user_prompt = ai_service.build_ai_messages("lead", payload)
    assert "如果非空，script可以自然使用该公司名" in user_prompt
    assert "水舟科技" in user_prompt


def test_competitor_ai_prompt_requires_profile_and_majority_content_relevance(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.services import ai_service

    with database.connect() as conn:
        database.set_setting(
            conn,
            "icp_profile",
            json.dumps({"产品": "花车定制", "客户": "景区、文旅公司"}, ensure_ascii=False),
        )
        conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, signature)
            VALUES('dy', 'loose-competitor-1', '生活记录号', '日常生活、旅行、美食分享')
            """
        )
        account_id = int(conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'loose-competitor-1'").fetchone()["id"])
        rows = [
            ("loose-content-1", "周末花车巡游真热闹", "逛街随拍，没有业务介绍"),
            ("loose-content-2", "家庭晚餐记录", "做饭和生活分享"),
            ("loose-content-3", "城市散步", "旅游日常"),
            ("loose-content-4", "猫咖体验", "休闲娱乐"),
            ("loose-content-5", "美食合集", "餐厅探店"),
        ]
        for content_id, title, description in rows:
            conn.execute(
                """
                INSERT INTO contents(platform, content_id, author_account_id, title, description, source_keyword)
                VALUES('dy', ?, ?, ?, ?, '花车巡游')
                """,
                (content_id, account_id, title, description),
            )
        payload = ai_service.build_input_payload(conn, "competitor", account_id)

    _, user_prompt = ai_service.build_ai_messages("competitor", payload)
    assert payload["account"]["signature"] == "日常生活、旅行、美食分享"
    assert payload["contents"][0]["source_keyword"] == "花车巡游"
    assert "判断标准要适中" in user_prompt
    assert "部分近期视频" in user_prompt
    assert "不得编造外部搜索结果" in user_prompt
    assert "不要把关键词命中等同于竞品关系" in user_prompt
    assert "除非视频简介/主页简介明确说明从事了这个行业" in user_prompt
    assert "相关视频数量/总视频数量" in user_prompt


def test_account_customer_intent_batch_and_delete_non_customers(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import account_actions, ai_service, crawler_adapter
    from app.services.importer import import_for_task

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(task["id"]))

    with database.connect() as conn:
        account_id = int(conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'creator-1'").fetchone()["id"])
        conn.execute("UPDATE user_accounts SET competitor_status = '竞品' WHERE id = ?", (account_id,))
        lead_id = int(conn.execute("SELECT id FROM lead_user_accounts LIMIT 1").fetchone()["id"])

    batch = account_actions.create_account_customer_intent_jobs(account_id, run_now=False)

    assert batch["created"] == 1
    assert batch["lead_count"] == 1
    with database.connect() as conn:
        job_count = conn.execute("SELECT COUNT(*) AS c FROM analysis_jobs WHERE target_type = 'lead' AND target_id = ?", (lead_id,)).fetchone()["c"]
        ai_service.apply_ai_result(
            conn,
            "lead",
            lead_id,
            {
                "is_customer": False,
                "intention": "低",
                "reason": "没有明确需求",
                "pain_points": [],
                "suggested_action": "不跟进",
                "script": "",
            },
        )

    assert job_count == 1
    deleted = account_actions.delete_account_non_customers(account_id)

    assert deleted["deleted"] == 1
    with database.connect() as conn:
        lead = conn.execute("SELECT 1 FROM lead_user_accounts WHERE id = ?", (lead_id,)).fetchone()
        competitor = conn.execute("SELECT competitor_status FROM user_accounts WHERE id = ?", (account_id,)).fetchone()
        source_contents = conn.execute("SELECT COUNT(*) AS c FROM contents WHERE author_account_id = ?", (account_id,)).fetchone()["c"]
        author_tombstones = conn.execute("SELECT COUNT(*) AS c FROM deleted_identities WHERE entity_type = 'author_account'").fetchone()["c"]
        comment_tombstones = conn.execute("SELECT COUNT(*) AS c FROM deleted_identities WHERE entity_type = 'comment'").fetchone()["c"]
    assert lead is None
    assert competitor["competitor_status"] == "竞品"
    assert source_contents >= 1
    assert author_tombstones == 0
    assert comment_tombstones >= 1


def test_delete_non_customer_keeps_other_lead_sources(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import account_actions, ai_service, crawler_adapter
    from app.services.importer import import_for_task

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(task["id"]))

    with database.connect() as conn:
        source_account_id = int(conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'creator-1'").fetchone()["id"])
        conn.execute("UPDATE user_accounts SET competitor_status = '竞品' WHERE id = ?", (source_account_id,))
        lead = conn.execute("SELECT id, account_id FROM lead_user_accounts LIMIT 1").fetchone()
        lead_id = int(lead["id"])
        lead_account_id = int(lead["account_id"])
        other_source_id = conn.execute(
            "INSERT INTO user_accounts(platform, platform_user_id, nickname, competitor_status) VALUES('dy', 'other-source', '其它竞品', '竞品')"
        ).lastrowid
        other_content_id = conn.execute(
            "INSERT INTO contents(platform, content_id, author_account_id, title) VALUES('dy', 'other-content', ?, '其它竞品视频')",
            (other_source_id,),
        ).lastrowid
        other_comment_id = conn.execute(
            "INSERT INTO comments(platform, comment_id, content_id, author_account_id, body) VALUES('dy', 'other-comment', ?, ?, '同一个客户在另一个竞品下评论')",
            (other_content_id, lead_account_id),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO lead_sources(lead_account_id, source_account_id, content_id, comment_id, keyword, source_type)
            VALUES(?, ?, ?, ?, '另一个关键词', 'comment')
            """,
            (lead_id, other_source_id, other_content_id, other_comment_id),
        )
        ai_service.apply_ai_result(
            conn,
            "lead",
            lead_id,
            {"is_customer": False, "intention": "低", "reason": "不匹配", "script": ""},
        )

    deleted = account_actions.delete_account_non_customers(source_account_id)

    assert deleted["deleted"] == 1
    with database.connect() as conn:
        lead_after = conn.execute("SELECT 1 FROM lead_user_accounts WHERE id = ?", (lead_id,)).fetchone()
        lead_account_after = conn.execute("SELECT 1 FROM user_accounts WHERE id = ?", (lead_account_id,)).fetchone()
        source_after = conn.execute("SELECT 1 FROM user_accounts WHERE id = ?", (source_account_id,)).fetchone()
        other_source_after = conn.execute("SELECT 1 FROM user_accounts WHERE id = ?", (other_source_id,)).fetchone()
        source_active = conn.execute(
            "SELECT COUNT(*) AS c FROM lead_sources WHERE lead_account_id = ? AND source_account_id = ? AND active = 1",
            (lead_id, source_account_id),
        ).fetchone()["c"]
        other_active = conn.execute(
            "SELECT COUNT(*) AS c FROM lead_sources WHERE lead_account_id = ? AND source_account_id = ? AND active = 1",
            (lead_id, other_source_id),
        ).fetchone()["c"]
    assert lead_after is not None
    assert lead_account_after is not None
    assert source_after is not None
    assert other_source_after is not None
    assert source_active == 0
    assert other_active == 1


def test_account_customer_intent_batch_resumes_pending_and_stale_running_jobs(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import account_actions, crawler_adapter
    from app.services.importer import import_for_task

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(task["id"]))

    with database.connect() as conn:
        account_id = int(conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'creator-1'").fetchone()["id"])
        conn.execute("UPDATE user_accounts SET competitor_status = '竞品' WHERE id = ?", (account_id,))
        lead_id = int(conn.execute("SELECT id FROM lead_user_accounts LIMIT 1").fetchone()["id"])
        conn.execute(
            "INSERT INTO analysis_jobs(id, target_type, target_id, status) VALUES('pending-lead-job', 'lead', ?, 'pending')",
            (lead_id,),
        )

    pending_result = account_actions.create_account_customer_intent_jobs(account_id, run_now=False)

    assert pending_result["created"] == 0
    assert pending_result["resumed"] == 1
    assert pending_result["job_ids"] == ["pending-lead-job"]

    with database.connect() as conn:
        conn.execute("DELETE FROM analysis_jobs WHERE id = 'pending-lead-job'")
        conn.execute(
            """
            INSERT INTO analysis_jobs(id, target_type, target_id, status, updated_at)
            VALUES('stale-running-lead-job', 'lead', ?, 'running', datetime('now', 'localtime', '-2 hours'))
            """,
            (lead_id,),
        )

    stale_result = account_actions.create_account_customer_intent_jobs(account_id, run_now=False)

    assert stale_result["created"] == 0
    assert stale_result["resumed"] == 1
    assert stale_result["job_ids"] == ["stale-running-lead-job"]
    with database.connect() as conn:
        row = conn.execute("SELECT status, error FROM analysis_jobs WHERE id = 'stale-running-lead-job'").fetchone()
    assert row["status"] == "pending"
    assert "已恢复" in row["error"]


def test_auto_lead_analysis_deletes_non_customer_when_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import ai_service, crawler_adapter
    from app.services.importer import import_for_task

    with database.connect() as conn:
        database.set_setting(conn, "ai_base_url", "https://example.test")
        database.set_setting(conn, "ai_api_key", "test-key")
        database.set_setting(conn, "ai_model", "test-model")
        database.set_setting(conn, "auto_delete_non_customers", "true")

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(task["id"]))

    lead_id = None
    account_id = None
    with database.connect() as conn:
        row = conn.execute(
            """
            SELECT lua.id, lua.account_id
            FROM lead_user_accounts lua
            JOIN lead_sources ls ON ls.lead_account_id = lua.id
            WHERE ls.task_id = ?
            """,
            (task["id"],),
        ).fetchone()
        lead_id = int(row["id"])
        account_id = int(row["account_id"])

    monkeypatch.setattr(
        ai_service,
        "call_openai_compatible",
        lambda *_args, **_kwargs: '{"is_customer": false, "intention": "低", "reason": "只是围观，没有采购意图", "pain_points": [], "suggested_action": "不跟进", "script": ""}',
    )

    result = ai_service.run_auto_lead_analysis_for_task(str(task["id"]))

    assert result["succeeded"] == 1
    assert result["auto_deleted"] == 1
    with database.connect() as conn:
        lead = conn.execute("SELECT 1 FROM lead_user_accounts WHERE id = ?", (lead_id,)).fetchone()
        account = conn.execute("SELECT 1 FROM user_accounts WHERE id = ?", (account_id,)).fetchone()
        author_tombstones = conn.execute("SELECT COUNT(*) AS c FROM deleted_identities WHERE entity_type = 'author_account'").fetchone()["c"]
        comment_tombstones = conn.execute("SELECT COUNT(*) AS c FROM deleted_identities WHERE entity_type = 'comment'").fetchone()["c"]
    assert lead is None
    assert account is None
    assert author_tombstones == 0
    assert comment_tombstones >= 1


def test_auto_lead_analysis_runs_jobs_parallel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import ai_service, crawler_adapter
    from app.services.importer import import_for_task

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(task["id"]))

    with database.connect() as conn:
        account_id = conn.execute(
            "INSERT INTO user_accounts(platform, platform_user_id, nickname) VALUES('dy', 'parallel-lead', 'parallel lead')"
        ).lastrowid
        lead_id = conn.execute("INSERT INTO lead_user_accounts(account_id) VALUES(?)", (account_id,)).lastrowid
        conn.execute(
            """
            INSERT INTO lead_sources(lead_account_id, source_type, task_id)
            VALUES(?, 'competitor_comment', ?)
            """,
            (lead_id, task["id"]),
        )

    created: list[tuple[str, int, bool]] = []
    parallel_called: dict[str, object] = {}

    def fake_create_ai_job(target_type: str, target_id: int, run_now: bool = True) -> dict[str, object]:
        created.append((target_type, target_id, run_now))
        return {"id": f"{target_type}-{target_id}"}

    def fake_run_parallel(job_ids: list[str], max_workers: int | None = None) -> dict[str, object]:
        parallel_called["job_ids"] = job_ids
        parallel_called["max_workers"] = max_workers
        return {"total": len(job_ids), "succeeded": len(job_ids), "failed": 0, "auto_deleted": 0, "errors": []}

    monkeypatch.setattr(ai_service, "create_ai_job", fake_create_ai_job)
    monkeypatch.setattr(ai_service, "run_ai_jobs_parallel", fake_run_parallel)

    result = ai_service.run_auto_lead_analysis_for_task(str(task["id"]))

    assert result["succeeded"] == 2
    assert len(created) == 2
    assert all(item[0] == "lead" and item[2] is False for item in created)
    assert len(parallel_called["job_ids"]) == 2


def test_competitor_ai_reason_is_saved_and_returned_in_overview(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app import views
    from app.services import ai_service

    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, signature)
            VALUES('dy', 'competitor-reason-1', '竞品理由账号', '卖同类AI客服')
            """
        )
        account_id = int(conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'competitor-reason-1'").fetchone()["id"])
        conn.execute(
            """
            INSERT INTO contents(platform, content_id, author_account_id, title, source_keyword)
            VALUES('dy', 'reason-content-1', ?, '同类AI客服介绍', 'AI客服')
            """,
            (account_id,),
        )
        ai_service.apply_ai_result(
            conn,
            "competitor",
            account_id,
            {"is_competitor": True, "reason": "账号主页和视频都在销售同类AI客服产品"},
        )

    tree = views.overview_tree()
    account = tree[0]["children"][0]["children"][0]
    assert account["metrics"]["competitor_status"] == "竞品"
    assert account["metrics"]["competitor_reason"] == "账号主页和视频都在销售同类AI客服产品"


def test_overview_customer_rows_include_reason_and_script(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database, views
    from app.schemas import TaskCreate
    from app.services import ai_service, crawler_adapter
    from app.services.importer import import_for_task

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(task["id"]))
    lead_id = views.list_library("lead_customers")["rows"][0]["id"]
    with database.connect() as conn:
        ai_service.apply_ai_result(
            conn,
            "lead",
            int(lead_id),
            {
                "is_customer": True,
                "intention": "高",
                "reason": "在评论区询问价格和替代人工客服",
                "pain_points": ["人工客服成本高"],
                "suggested_action": "优先私信",
                "script": "你好，看到你在关注客服替代方案。",
            },
        )

    tree = views.overview_tree()
    account = tree[0]["children"][0]["children"][0]
    customer = account["children"][0]

    assert customer["kind"] == "customer"
    assert customer["label"] == "准备采购的老板"
    assert customer["metrics"]["reason"] == "在评论区询问价格和替代人工客服"
    assert customer["metrics"]["script"] == "你好，看到你在关注客服替代方案。"


def test_overview_customer_rows_show_ai_analysis_status(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database, views
    from app.schemas import TaskCreate
    from app.services import ai_service, crawler_adapter
    from app.services.importer import import_for_task

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(task["id"]))
    lead_id = int(views.list_library("lead_customers")["rows"][0]["id"])

    def customer_metrics() -> dict:
        tree = views.overview_tree()
        return tree[0]["children"][0]["children"][0]["children"][0]["metrics"]

    assert customer_metrics()["ai_analysis_status"] == "未分析"

    with database.connect() as conn:
        conn.execute(
            "INSERT INTO analysis_jobs(id, target_type, target_id, status) VALUES('pending-lead-overview', 'lead', ?, 'pending')",
            (lead_id,),
        )
    assert customer_metrics()["ai_analysis_status"] == "排队分析"

    with database.connect() as conn:
        conn.execute("UPDATE analysis_jobs SET status = 'running' WHERE id = 'pending-lead-overview'")
    assert customer_metrics()["ai_analysis_status"] == "正在分析"

    with database.connect() as conn:
        conn.execute("DELETE FROM analysis_jobs WHERE id = 'pending-lead-overview'")
        ai_service.apply_ai_result(
            conn,
            "lead",
            lead_id,
            {"is_customer": True, "intention": "中", "reason": "客户在评论区询价", "script": "你好，看到你在了解价格。"},
        )
    assert customer_metrics()["ai_analysis_status"] == "已分析"


def test_ai_workbench_enriches_targets_and_failure_categories(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.main import app
    from app.schemas import TaskCreate
    from app.services import ai_service, crawler_adapter
    from app.services.importer import import_for_task

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(task["id"]))

    with database.connect() as conn:
        account_id = int(conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'creator-1'").fetchone()["id"])
        lead_id = int(conn.execute("SELECT id FROM lead_user_accounts LIMIT 1").fetchone()["id"])
        ai_service.apply_ai_result(
            conn,
            "competitor",
            account_id,
            {"is_competitor": True, "reason": "主页和视频都与AI客服竞品相关"},
        )
        conn.execute(
            """
            INSERT INTO analysis_jobs(id, target_type, target_id, status, output_payload)
            VALUES('workbench-competitor-ok', 'competitor', ?, 'succeeded', ?)
            """,
            (account_id, '{"is_competitor": true, "reason": "主页和视频都与AI客服竞品相关"}'),
        )
        conn.execute(
            """
            INSERT INTO analysis_jobs(id, target_type, target_id, status, error)
            VALUES('workbench-lead-failed', 'lead', ?, 'failed', 'AI输出中没有找到JSON对象')
            """,
            (lead_id,),
        )

    client = TestClient(app)
    response = client.get("/api/ai/workbench")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["failed"] == 1
    competitor = next(item for item in payload["competitors"] if item["id"] == account_id)
    assert competitor["nickname"] == "AI客服竞品号"
    assert competitor["analysis_status"] == "已分析"
    assert competitor["result_label"] == "竞品"
    lead = next(item for item in payload["leads"] if item["id"] == lead_id)
    assert lead["nickname"] == "准备采购的老板"
    assert "多少钱" in lead["comment_samples"]
    failed = payload["failed_jobs"][0]
    assert failed["id"] == "workbench-lead-failed"
    assert failed["target_name"] == "准备采购的老板"
    assert failed["error_category"] == "JSON解析失败"
    assert any(item["id"] == "workbench-competitor-ok" and "竞品" in item["output_summary"] for item in payload["history"])


def test_ai_workbench_bulk_delete_only_confirmed_negative_results(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.main import app
    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.services.importer import import_for_task

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(task["id"]))

    with database.connect() as conn:
        lead_id = int(conn.execute("SELECT id FROM lead_user_accounts LIMIT 1").fetchone()["id"])
        conn.execute(
            "UPDATE lead_user_accounts SET screening_status = '非客户', follow_status = '非客户' WHERE id = ?",
            (lead_id,),
        )
        conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, account_role, competitor_status)
            VALUES('dy', 'confirmed-not-competitor', '确认非竞品', 'competitor_candidate', '非竞品')
            """
        )
        non_competitor_id = int(conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'confirmed-not-competitor'").fetchone()["id"])
        conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, account_role, competitor_status)
            VALUES('dy', 'confirmed-competitor', '确认竞品', 'competitor', '竞品')
            """
        )
        competitor_id = int(conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'confirmed-competitor'").fetchone()["id"])

    client = TestClient(app)
    competitor_response = client.post(
        "/api/ai/workbench/non-competitors/delete",
        json={"target_ids": [non_competitor_id, competitor_id]},
    )
    customer_response = client.post(
        "/api/ai/workbench/non-customers/delete",
        json={"target_ids": [lead_id]},
    )

    assert competitor_response.status_code == 200
    assert competitor_response.json()["deleted"] == 1
    assert len(competitor_response.json()["skipped"]) == 1
    assert customer_response.status_code == 200
    assert customer_response.json()["deleted"] == 1
    with database.connect() as conn:
        assert conn.execute("SELECT 1 FROM user_accounts WHERE id = ?", (non_competitor_id,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM user_accounts WHERE id = ?", (competitor_id,)).fetchone() is not None
        assert conn.execute("SELECT 1 FROM lead_user_accounts WHERE id = ?", (lead_id,)).fetchone() is None


def test_auto_delete_non_competitor_after_ai_analysis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.services import ai_service

    with database.connect() as conn:
        database.set_setting(conn, "ai_base_url", "https://example.test")
        database.set_setting(conn, "ai_api_key", "test-key")
        database.set_setting(conn, "ai_model", "test-model")
        database.set_setting(conn, "auto_delete_non_competitors", "true")
        conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, nickname, account_role, competitor_status)
            VALUES('dy', 'not-competitor-1', '非竞品账号', 'competitor_candidate', '未分析')
            """
        )
        account_id = int(conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'not-competitor-1'").fetchone()["id"])

    monkeypatch.setattr(
        ai_service,
        "call_openai_compatible",
        lambda *_args, **_kwargs: '{"is_competitor": false, "reason": "主页和内容不卖同类产品"}',
    )

    result = ai_service.create_ai_job("competitor", account_id, run_now=True)

    assert result["status"] == "succeeded"
    assert result["auto_delete_result"]["mode"] == "overview_account_delete"
    with database.connect() as conn:
        account = conn.execute("SELECT 1 FROM user_accounts WHERE id = ?", (account_id,)).fetchone()
        job_count = conn.execute("SELECT COUNT(*) AS c FROM analysis_jobs WHERE target_type = 'competitor' AND target_id = ?", (account_id,)).fetchone()["c"]
    assert account is None
    assert job_count == 0


def test_hard_delete_without_raw_mapping_records_tombstone(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.services import deletion

    with database.connect() as conn:
        conn.execute(
            "INSERT INTO user_accounts(platform, platform_user_id, nickname) VALUES('dy', 'no-raw', '无映射账号')"
        )
        account_id = conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'no-raw'").fetchone()["id"]

    result = deletion.delete_library_row("competitor_candidates", int(account_id), hard=True)
    assert result["raw_refs"] == 0
    assert result["tombstones"]["accounts"] >= 1
    with database.connect() as conn:
        account_count = conn.execute("SELECT COUNT(*) AS c FROM user_accounts WHERE id = ?", (account_id,)).fetchone()["c"]
        tombstone_count = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM deleted_identities
            WHERE entity_type = 'author_account'
              AND identifier_value = 'no-raw'
            """
        ).fetchone()["c"]
    assert account_count == 0
    assert tombstone_count == 1


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
    assert response.json()["own_accounts"] == {"dy": [], "xhs": [], "ks": []}
    own_accounts = {
        "dy": ["https://www.douyin.com/user/a", "https://www.douyin.com/user/b"],
        "xhs": ["xhs-account"],
        "ks": [],
    }
    response = client.put("/api/settings", json={"values": {"own_accounts": own_accounts}})
    assert response.status_code == 200
    assert response.json()["own_accounts"] == own_accounts
    preview = client.post(
        "/api/tasks/preview",
        json={"mode": "own_account", "platform": "dy", "execute_crawler": False},
    )
    assert preview.status_code == 200
    assert preview.json()["normalized"]["creator_id"] == ",".join(own_accounts["dy"])
    created = client.post(
        "/api/tasks",
        json={"mode": "own_account", "platform": "xhs", "execute_crawler": False},
    )
    assert created.status_code == 200
    assert created.json()["creator_id"] == "xhs-account"


def test_license_api_generates_readonly_device_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_project(tmp_path)
    from app.main import app
    from app.services import license_service

    client = TestClient(app)
    first = client.get("/api/license").json()
    assert first["device_code"].startswith("AI-CUS-")

    saved = client.put("/api/license", json={"license_code": "LIC-TEST"}).json()
    assert saved["license_code"] == "LIC-TEST"
    assert saved["device_code"] == first["device_code"]

    response = client.put("/api/settings", json={"values": {"device_code": "changed-by-user"}})
    assert response.status_code == 200
    assert client.get("/api/license").json()["device_code"] == first["device_code"]

    def fake_remote(server_url: str, license_code: str, device_code: str) -> dict[str, object]:
        assert server_url.endswith("/ai-customer")
        assert license_code == "LIC-TEST"
        assert device_code == first["device_code"]
        return {
            "code": 200,
            "message": "授权通过",
            "data": {
                "permission": True,
                "reason": "DEVICE_ALREADY_BOUND",
                "maxDevices": 3,
                "activeDeviceCount": 1,
                "boundNewDevice": False,
            },
        }

    monkeypatch.setattr(license_service, "_request_license_check", fake_remote)
    checked = client.post("/api/license/check", json={"license_code": "LIC-TEST"}).json()
    assert checked["authorized"] is True
    assert checked["reason"] == "DEVICE_ALREADY_BOUND"
    assert checked["max_devices"] == 3


def test_failed_task_without_imported_data_can_be_deleted(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.main import app

    task = crawler_adapter.create_task(
        TaskCreate(mode="account_analysis", platform="dy", creator_id="https://www.douyin.com/user/MS4wLjABFailed", execute_crawler=False)
    )
    with database.connect() as conn:
        conn.execute("UPDATE crawl_jobs SET status = 'failed', error = 'startup failed' WHERE id = ?", (task["id"],))
        crawler_adapter.log_task(conn, str(task["id"]), "error", "startup failed")

    client = TestClient(app)
    response = client.delete(f"/api/tasks/{task['id']}")

    assert response.status_code == 200
    assert response.json()["mode"] == "task_record_delete"
    with database.connect() as conn:
        task_row = conn.execute("SELECT 1 FROM crawl_jobs WHERE id = ?", (task["id"],)).fetchone()
        log_count = conn.execute("SELECT COUNT(*) AS c FROM task_logs WHERE task_id = ?", (task["id"],)).fetchone()["c"]
        audit = conn.execute("SELECT detail FROM deletion_audit WHERE entity_type = 'task' ORDER BY id DESC LIMIT 1").fetchone()
    assert task_row is None
    assert log_count == 0
    assert str(task["id"]) in audit["detail"]


def test_succeeded_task_without_remaining_evidence_can_be_deleted(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.main import app

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_discovery", platform="dy", keywords="无效证据", execute_crawler=False)
    )
    with database.connect() as conn:
        conn.execute("UPDATE crawl_jobs SET status = 'succeeded' WHERE id = ?", (task["id"],))
        crawler_adapter.log_task(conn, str(task["id"]), "info", "任务数据已被范围删除")

    client = TestClient(app)
    response = client.delete(f"/api/tasks/{task['id']}")

    assert response.status_code == 200
    assert response.json()["mode"] == "task_record_delete"
    with database.connect() as conn:
        task_row = conn.execute("SELECT 1 FROM crawl_jobs WHERE id = ?", (task["id"],)).fetchone()
        log_count = conn.execute("SELECT COUNT(*) AS c FROM task_logs WHERE task_id = ?", (task["id"],)).fetchone()["c"]
        audit = conn.execute("SELECT detail FROM deletion_audit WHERE entity_type = 'task' ORDER BY id DESC LIMIT 1").fetchone()
    assert task_row is None
    assert log_count == 0
    assert '"status": "succeeded"' in audit["detail"]


def test_running_task_without_evidence_must_be_cancelled_before_record_delete(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.main import app

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_discovery", platform="dy", keywords="运行中", execute_crawler=False)
    )
    with database.connect() as conn:
        conn.execute("UPDATE crawl_jobs SET status = 'running' WHERE id = ?", (task["id"],))

    client = TestClient(app)
    response = client.delete(f"/api/tasks/{task['id']}")

    assert response.status_code == 409
    assert "先取消" in response.json()["detail"]


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
    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO deleted_identities(entity_type, platform, identifier_type, identifier_value, source)
            VALUES('content', 'dy', 'native_id', '10001', 'test')
            """
        )

    client = TestClient(app)
    rejected = client.post("/api/settings/clear-data", json={"confirm": "确认"})
    assert rejected.status_code == 400

    response = client.post("/api/settings/clear-data", json={"confirm": "清空所有数据"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["project"]["rows"] > 0
    assert payload["media_crawler"]["rows"] > 0

    with database.connect() as conn:
        for table in ["crawl_jobs", "task_logs", "user_accounts", "contents", "comments", "raw_source_refs", "deleted_identities"]:
            assert conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"] == 0
        assert database.get_setting(conn, "media_crawler_db_path") == str(raw_db)
        assert database.get_setting(conn, "next_task_number") == "1"

    raw_conn = sqlite3.connect(raw_db)
    try:
        for table in ["douyin_aweme", "douyin_aweme_comment", "dy_creator"]:
            assert raw_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    finally:
        raw_conn.close()


def test_task_diagnostics_classifies_missing_raw_table_and_empty_success(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.main import app

    failed = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    empty = crawler_adapter.create_task(
        TaskCreate(mode="competitor_discovery", platform="dy", keywords="无结果", execute_crawler=False)
    )
    with database.connect() as conn:
        conn.execute("UPDATE crawl_jobs SET status = 'failed', error = 'sqlite3.OperationalError: no such table: douyin_aweme' WHERE id = ?", (failed["id"],))
        crawler_adapter.log_task(conn, str(failed["id"]), "error", "sqlalchemy.exc.OperationalError: no such table: douyin_aweme")
        conn.execute("UPDATE crawl_jobs SET status = 'succeeded' WHERE id = ?", (empty["id"],))

    client = TestClient(app)
    failed_response = client.get(f"/api/tasks/{failed['id']}/diagnostics")
    empty_response = client.get(f"/api/tasks/{empty['id']}/diagnostics")

    assert failed_response.status_code == 200
    assert failed_response.json()["category"] == "数据库缺表"
    assert failed_response.json()["retryable"] is True
    assert empty_response.status_code == 200
    assert empty_response.json()["category"] == "无有效数据"
    assert empty_response.json()["status"] == "warning"


def test_tombstone_summary_list_and_task_dedup_summary(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import crawler_adapter
    from app.main import app

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_discovery", platform="dy", keywords="AI客服", execute_crawler=False)
    )
    with database.connect() as conn:
        conn.execute(
            """
            INSERT INTO deleted_identities(entity_type, platform, identifier_type, identifier_value, source, snapshot)
            VALUES('content', 'dy', 'native_id', 'content-1', 'unit-test', ?)
            """,
            (json.dumps({"title": "已删除内容", "task_id": task["id"]}, ensure_ascii=False),),
        )
        conn.execute(
            "INSERT INTO deletion_audit(entity_type, entity_id, hard_delete, detail) VALUES('task', 0, 1, ?)",
            (json.dumps({"task_id": task["id"], "counts": {"contents": 1}}, ensure_ascii=False),),
        )

    client = TestClient(app)
    summary = client.get("/api/tombstones/summary").json()
    listing = client.get("/api/tombstones", params={"entity_type": "content", "query": "content-1"}).json()
    dedup = client.get(f"/api/tasks/{task['id']}/dedup-summary").json()

    assert summary["contents"] == 1
    assert listing["total"] == 1
    assert listing["items"][0]["snapshot_summary"] == "已删除内容"
    assert dedup["task_id"] == task["id"]
    assert dedup["audit"][0]["detail"]["task_id"] == task["id"]


def test_bulk_action_preview_counts_ai_and_deletes(tmp_path: Path) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.main import app

    with database.connect() as conn:
        account_id = conn.execute(
            """
            INSERT INTO user_accounts(platform, platform_user_id, sec_uid, nickname, competitor_status, profile_url)
            VALUES('dy', 'non-comp-1', 'sec-non-comp-1', '非竞品账号', '非竞品', 'https://example.com/non-comp')
            """
        ).lastrowid
        lead_account_id = conn.execute(
            "INSERT INTO user_accounts(platform, platform_user_id, nickname) VALUES('dy', 'lead-non-customer', '非客户')"
        ).lastrowid
        lead_id = conn.execute(
            """
            INSERT INTO lead_user_accounts(account_id, screening_status, follow_status)
            VALUES(?, '非客户', '非客户')
            """,
            (lead_account_id,),
        ).lastrowid
        conn.execute(
            "INSERT INTO comments(platform, comment_id, author_account_id, body) VALUES('dy', 'comment-non-customer', ?, '无关评论')",
            (lead_account_id,),
        )

    client = TestClient(app)
    competitor_preview = client.post(
        "/api/bulk-actions/preview",
        json={"action": "delete_non_competitors", "target_type": "competitor", "target_ids": [account_id]},
    ).json()
    lead_preview = client.post(
        "/api/bulk-actions/preview",
        json={"action": "delete_non_customers", "target_type": "lead", "target_ids": [lead_id]},
    ).json()
    ai_preview = client.post(
        "/api/bulk-actions/preview",
        json={"action": "ai_analyze", "target_type": "competitor", "target_ids": [account_id]},
    ).json()

    assert competitor_preview["eligible_count"] == 1
    assert competitor_preview["tombstone_counts"]["author_account"] >= 1
    assert lead_preview["eligible_count"] == 1
    assert ai_preview["affected_counts"]["analysis_jobs"] == 1


def test_ai_job_records_prompt_version_and_raw_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prepare_project(tmp_path)
    from app import database
    from app.schemas import TaskCreate
    from app.services import ai_service, crawler_adapter
    from app.services.importer import import_for_task

    task = crawler_adapter.create_task(
        TaskCreate(mode="competitor_crawl", platform="dy", creator_id="creator-1", execute_crawler=False)
    )
    import_for_task(str(task["id"]))
    with database.connect() as conn:
        database.set_setting(conn, "ai_base_url", "https://ai.example/v1")
        database.set_setting(conn, "ai_api_key", "test-key")
        database.set_setting(conn, "ai_model", "test-model")
        account_id = conn.execute("SELECT id FROM user_accounts WHERE platform_user_id = 'creator-1'").fetchone()["id"]

    monkeypatch.setattr(
        ai_service,
        "call_openai_compatible",
        lambda *_args, **_kwargs: '{"is_competitor": true, "reason": "主页和视频均相关", "relevant_content_count": 1, "total_content_count": 1, "profile_relevance": "高"}',
    )

    job = ai_service.create_ai_job("competitor", int(account_id), run_now=True)

    with database.connect() as conn:
        row = conn.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job["id"],)).fetchone()
    assert row["prompt_version"] == "competitor_v1"
    assert row["model"] == "test-model"
    assert row["base_url"] == "https://ai.example/v1"
    assert "你正在判断一个候选账号" in row["user_prompt"]
    assert '"is_competitor": true' in row["raw_output"]
