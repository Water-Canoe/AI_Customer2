from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from app import database
from app.services import crawler_adapter


DOUYIN_HOME = "https://www.douyin.com/"


def main(run_id: str) -> int:
    runner = TrafficExecutor(run_id)
    try:
        runner.run()
        return 0
    except Exception as exc:
        runner.fail_run(str(exc))
        return 1


class TrafficExecutor:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.run = self._load_run()
        self.campaign = self._load_campaign(int(self.run["campaign_id"]))
        self.done = 0
        self.failed = 0

    def run(self) -> None:
        self._update_run("running")
        with sync_playwright() as playwright:
            port = self._cdp_port()
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[-1] if context.pages else context.new_page()
            if self.campaign["mode"] == "random":
                self._run_random(page)
            else:
                self._run_targeted(page)
        self._finish_run("succeeded")

    def fail_run(self, message: str) -> None:
        with database.connect() as conn:
            conn.execute(
                """
                UPDATE traffic_runs
                SET status = 'failed', error = ?, counts = ?, finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (message, self._counts_json(), self.run_id),
            )
            self._log(conn, None, "system", "error", message)

    def _run_targeted(self, page: Page) -> None:
        targets = self._claim_targets()
        if not targets:
            raise RuntimeError("没有可执行的待引流视频")
        for target in targets:
            self._execute_target(page, target)

    def _run_random(self, page: Page) -> None:
        page.goto(DOUYIN_HOME, wait_until="domcontentloaded", timeout=45000)
        for _ in range(int(self.run["per_run_limit"])):
            target = self._create_random_target(page)
            self._execute_target(page, target, already_open=True)
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(int(self._random_interval() * 1000))

    def _execute_target(self, page: Page, target: dict[str, Any], already_open: bool = False) -> None:
        target_id = int(target["id"])
        try:
            if not already_open:
                page.goto(str(target["content_url"]), wait_until="domcontentloaded", timeout=45000)
            self._mark_target(target_id, "running")
            self._event(target_id, "open", "succeeded", page.url)
            page.wait_for_timeout(int(self._random_stay() * 1000))
            if self.campaign["action_like"]:
                self._click_required(page, ["button:has-text('点赞')", "[aria-label*='点赞']", "[data-e2e*='like']"], "like", target_id)
            if self.campaign["action_follow"]:
                self._click_required(page, ["button:has-text('关注')", "[aria-label*='关注']", "text=关注"], "follow", target_id)
            if self.campaign["action_comment"]:
                self._comment(page, target)
            self._mark_target(target_id, "succeeded")
            self.done += 1
            self._update_counts()
        except Exception as exc:
            screenshot = self._screenshot(page, target_id)
            self.failed += 1
            self._mark_target(target_id, "failed", str(exc), screenshot)
            self._update_counts()
            raise RuntimeError(f"目标 {target_id} 执行失败：{exc}") from exc

    def _comment(self, page: Page, target: dict[str, Any]) -> None:
        target_id = int(target["id"])
        self._click_required(page, ["button:has-text('评论')", "[aria-label*='评论']", "[data-e2e*='comment']"], "comment_open", target_id)
        text = self._render_comment(target)
        if not text:
            raise RuntimeError("没有可用引流文案")
        box = self._first_visible(page, ["textarea", "[contenteditable='true']", "[placeholder*='评论']", "[class*='comment'] [contenteditable='true']"])
        if box is None:
            raise RuntimeError("找不到评论输入框")
        box.fill(text, timeout=5000)
        page.wait_for_timeout(int(self._random_interval() * 1000))
        self._click_required(page, ["button:has-text('发送')", "text=发送", "[data-e2e*='comment-submit']"], "comment_submit", target_id)
        self._event(target_id, "comment", "succeeded", text)

    def _click_required(self, page: Page, selectors: list[str], action: str, target_id: int) -> None:
        locator = self._first_visible(page, selectors)
        if locator is None:
            raise RuntimeError(f"找不到{action}按钮")
        locator.click(timeout=5000)
        self._event(target_id, action, "succeeded", "")
        page.wait_for_timeout(int(self._random_interval() * 1000))

    def _first_visible(self, page: Page, selectors: list[str]):
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=2500)
                return locator
            except PlaywrightTimeoutError:
                continue
            except Exception:
                continue
        return None

    def _claim_targets(self) -> list[dict[str, Any]]:
        with database.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM traffic_targets
                WHERE campaign_id = ? AND status = 'pending'
                ORDER BY id ASC
                LIMIT ?
                """,
                (int(self.campaign["id"]), int(self.run["per_run_limit"])),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if ids:
                placeholders = ",".join(["?"] * len(ids))
                conn.execute(
                    f"UPDATE traffic_targets SET status = 'running', run_id = ?, updated_at = datetime('now', 'localtime') WHERE id IN ({placeholders})",
                    (self.run_id, *ids),
                )
            return database.rows_to_dicts(rows)

    def _create_random_target(self, page: Page) -> dict[str, Any]:
        url = page.url or DOUYIN_HOME
        title = page.title() or "随机推荐视频"
        key = url if "/video/" in url else f"random:{int(time.time() * 1000)}"
        with database.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO traffic_targets(campaign_id, platform, source_type, target_key, content_url, title, selected_comment, status, run_id)
                VALUES(?, 'dy', 'random_feed', ?, ?, ?, ?, 'running', ?)
                """,
                (int(self.campaign["id"]), key, url, title, "", self.run_id),
            )
            row = conn.execute("SELECT * FROM traffic_targets WHERE id = ?", (int(cur.lastrowid),)).fetchone()
            return database.row_to_dict(row) or {}

    def _mark_target(self, target_id: int, status: str, error: str = "", screenshot: str = "") -> None:
        with database.connect() as conn:
            conn.execute(
                """
                UPDATE traffic_targets
                SET status = ?, error = ?, last_action_at = CASE WHEN ? = 'succeeded' THEN datetime('now', 'localtime') ELSE last_action_at END,
                    updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (status, error, status, target_id),
            )
            if error:
                self._log(conn, target_id, "target", "failed", error, screenshot)

    def _event(self, target_id: int, action: str, status: str, detail: str) -> None:
        with database.connect() as conn:
            self._log(conn, target_id, action, status, detail)

    def _log(self, conn, target_id: int | None, action: str, status: str, detail: str, screenshot: str = "") -> None:
        conn.execute(
            "INSERT INTO traffic_action_events(run_id, target_id, action, status, detail, screenshot_path) VALUES(?, ?, ?, ?, ?, ?)",
            (self.run_id, target_id, action, status, detail, screenshot),
        )

    def _screenshot(self, page: Page, target_id: int) -> str:
        path = database.WORKSPACE_ROOT / "runtime" / "traffic_screenshots" / f"{self.run_id}_{target_id}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(path), full_page=False)
            return str(path)
        except Exception:
            return ""

    def _render_comment(self, target: dict[str, Any]) -> str:
        templates = self.campaign["comment_templates"]
        template = random.choice(templates) if templates else ""
        return (
            str(template)
            .replace("{昵称}", str(target.get("author_name") or ""))
            .replace("{关键词}", str(target.get("keyword") or self.campaign.get("keyword") or ""))
            .replace("{视频标题}", str(target.get("title") or ""))
        )

    def _random_stay(self) -> float:
        return random.uniform(float(self.campaign["stay_seconds_min"]), float(self.campaign["stay_seconds_max"]))

    def _random_interval(self) -> float:
        return random.uniform(float(self.campaign["action_interval_seconds_min"]), float(self.campaign["action_interval_seconds_max"]))

    def _load_run(self) -> dict[str, Any]:
        with database.connect() as conn:
            row = conn.execute("SELECT * FROM traffic_runs WHERE id = ?", (self.run_id,)).fetchone()
            if not row:
                raise RuntimeError("引流批次不存在")
            return database.row_to_dict(row) or {}

    def _load_campaign(self, campaign_id: int) -> dict[str, Any]:
        with database.connect() as conn:
            row = conn.execute("SELECT * FROM traffic_campaigns WHERE id = ?", (campaign_id,)).fetchone()
            if not row:
                raise RuntimeError("引流计划不存在")
            item = database.row_to_dict(row) or {}
            item["action_like"] = bool(item.get("action_like"))
            item["action_follow"] = bool(item.get("action_follow"))
            item["action_comment"] = bool(item.get("action_comment"))
            item["comment_templates"] = _json_list(item.get("comment_templates"))
            return item

    def _update_run(self, status: str) -> None:
        with database.connect() as conn:
            conn.execute("UPDATE traffic_runs SET status = ?, updated_at = datetime('now', 'localtime') WHERE id = ?", (status, self.run_id))

    def _finish_run(self, status: str) -> None:
        with database.connect() as conn:
            conn.execute(
                """
                UPDATE traffic_runs
                SET status = ?, counts = ?, finished_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime')
                WHERE id = ?
                """,
                (status, self._counts_json(), self.run_id),
            )
            self._log(conn, None, "system", "succeeded", f"引流批次完成：成功 {self.done} 条，失败 {self.failed} 条")

    def _update_counts(self) -> None:
        with database.connect() as conn:
            conn.execute("UPDATE traffic_runs SET counts = ?, updated_at = datetime('now', 'localtime') WHERE id = ?", (self._counts_json(), self.run_id))

    def _counts_json(self) -> str:
        return json.dumps({"succeeded": self.done, "failed": self.failed}, ensure_ascii=False)

    def _cdp_port(self) -> int:
        with database.connect() as conn:
            media_path = crawler_adapter.normalize_path(database.get_setting(conn, "media_crawler_path"))
        config = crawler_adapter._read_media_crawler_cdp_config(Path(media_path))
        return int(config.get("debug_port") or 9222)


def _json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: traffic_executor.py RUN_ID")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
