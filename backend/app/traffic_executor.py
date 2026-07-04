from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urljoin, urlparse

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
except ModuleNotFoundError:
    PlaywrightTimeoutError = TimeoutError

from app import database
from app.services import crawler_adapter, traffic_workbench


DOUYIN_HOME = "https://www.douyin.com/"
DOUYIN_SEARCH = "https://www.douyin.com/search/{keyword}?type=video"


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
        self.run_info = self._load_run()
        self.campaign = self._load_campaign(int(self.run_info["campaign_id"]))
        self.done = 0
        self.failed = 0
        self.skipped = 0

    def run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            raise RuntimeError("引流执行器缺少 Playwright 依赖，请确认 MediaCrawler 虚拟环境已安装 Playwright") from exc
        self._update_run("running")
        with sync_playwright() as playwright:
            port = self._cdp_port()
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[-1] if context.pages else context.new_page()
            if self.campaign["source_type"] == "search_keyword":
                self._run_search_keyword(page)
            elif self.campaign["mode"] == "random":
                self._run_random(page)
            else:
                self._run_targeted(page)
        self._finish_run("succeeded")

    def fail_run(self, message: str) -> None:
        traffic_workbench.release_run_comment_claims(self.run_id)
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
        self._run_runtime_feed(page, DOUYIN_HOME, "random_feed")

    def _run_search_keyword(self, page: Page) -> None:
        keyword = str(self.campaign.get("keyword") or "").strip()
        if not keyword:
            raise RuntimeError("搜索关键词引流必须填写关键词")
        page.goto(DOUYIN_SEARCH.format(keyword=quote(keyword, safe="")), wait_until="domcontentloaded", timeout=45000)
        page = self._open_random_video(page)
        self._run_runtime_feed(page, page.url, "search_keyword", already_open=True)

    def _run_runtime_feed(self, page: Page, start_url: str, source_type: str, already_open: bool = False) -> None:
        if not already_open:
            page.goto(start_url, wait_until="domcontentloaded", timeout=45000)
            page = self._open_random_video(page)
        limit = int(self.run_info["per_run_limit"])
        for index in range(limit):
            target = self._create_runtime_target(page, source_type)
            self._execute_target(page, target, already_open=True)
            # 最后一条执行完停留在当前视频，方便人工核验操作结果。
            if index < limit - 1:
                page.keyboard.press("ArrowDown")
                page.wait_for_timeout(int(self._random_interval() * 1000))

    def _open_random_video(self, page: Page) -> Page:
        if "/video/" in (page.url or "") or self._active_video_id(page):
            return page
        # 先从列表页随机进入一个视频页，再复用单视频动作执行流程。
        selectors = ["a[href*='/video/']", "[class*='waterfall-videoCardContainer']"]
        for selector in selectors:
            try:
                items = page.locator(selector)
                items.first.wait_for(state="visible", timeout=5000)
                indexes = list(range(min(items.count(), 30)))
                random.shuffle(indexes)
            except Exception:
                continue
            for index in indexes:
                try:
                    item = items.nth(index)
                    href = str(item.get_attribute("href", timeout=1000) or "")
                    before = list(page.context.pages)
                    item.wait_for(state="visible", timeout=1000)
                    item.click(timeout=5000)
                    page.wait_for_timeout(2000)
                    for candidate in page.context.pages:
                        if all(candidate is not existing for existing in before):
                            candidate.bring_to_front()
                            page = candidate
                            break
                    if "/video/" in (page.url or "") or self._active_video_id(page):
                        return page
                    if "/video/" in href:
                        page.goto(urljoin(DOUYIN_HOME, href), wait_until="domcontentloaded", timeout=45000)
                        return page
                except Exception:
                    continue
        raise RuntimeError("找不到可进入的抖音视频")

    def _execute_target(self, page: Page, target: dict[str, Any], already_open: bool = False) -> None:
        target_id = int(target["id"])
        try:
            if str(target.get("target_key") or "").startswith("unstable:"):
                self.skipped += 1
                self._mark_target(target_id, "skipped", "无稳定视频标识")
                self._update_counts()
                return
            allowed, reason = traffic_workbench._target_allowed(target, self.campaign.get("rule_config") or {})
            if not allowed:
                self.skipped += 1
                self._mark_target(target_id, "skipped", reason)
                self._update_counts()
                return
            needs_comment_claim = bool(self.campaign["action_comment"] or self.campaign["action_image"])
            if needs_comment_claim and not traffic_workbench.claim_comment_action(self.campaign, target, self.run_id):
                self.skipped += 1
                self._mark_target(target_id, "skipped", "重复视频")
                self._update_counts()
                return
            if not already_open:
                page.goto(str(target["content_url"]), wait_until="domcontentloaded", timeout=45000)
            self._mark_target(target_id, "running")
            self._event(target_id, "open", "succeeded", page.url)
            page.wait_for_timeout(int(self._random_stay() * 1000))
            if self.campaign["action_like"]:
                self._click_required(page, ["[data-e2e='video-player-digg']", "button:has-text('点赞')", "[aria-label*='点赞']", "[data-e2e*='like']"], "like", target_id)
            if self.campaign["action_follow"]:
                self._click_required(page, ["button:has-text('关注')", "[aria-label*='关注']", "text=关注"], "follow", target_id)
            if needs_comment_claim:
                comment_text = self._comment(page, target)
                traffic_workbench.complete_comment_action(target, comment_text)
            self._mark_target(target_id, "succeeded")
            self.done += 1
            self._update_counts()
        except Exception as exc:
            if self.campaign["action_comment"] or self.campaign["action_image"]:
                traffic_workbench.release_comment_action(target)
            screenshot = self._screenshot(page, target_id)
            self.failed += 1
            self._mark_target(target_id, "failed", str(exc), screenshot)
            self._update_counts()
            raise RuntimeError(f"目标 {target_id} 执行失败：{exc}") from exc

    def _comment(self, page: Page, target: dict[str, Any]) -> str:
        target_id = int(target["id"])
        self._click_required(page, ["button:has-text('评论')", "[aria-label*='评论']", "[data-e2e*='comment']"], "comment_open", target_id)
        detail_parts: list[str] = []
        if self.campaign["action_comment"]:
            text = self._render_comment(target)
            if not text:
                raise RuntimeError("没有可用引流文案")
            box = self._first_visible(page, ["textarea", "[contenteditable='true']", "[placeholder*='评论']", "[class*='comment'] [contenteditable='true']"])
            if box is None:
                raise RuntimeError("找不到评论输入框")
            box.fill(text, timeout=5000)
            detail_parts.append(text)
        if self.campaign["action_image"]:
            asset = self._pick_image_asset()
            uploader = self._first_file_input(page)
            if uploader is None:
                raise RuntimeError("找不到图片上传控件")
            uploader.set_input_files(asset["path"])
            detail_parts.append(f"图片：{asset['name']}")
        page.wait_for_timeout(int(self._random_interval() * 1000))
        self._click_required(page, ["button:has-text('发送')", "text=发送", "[data-e2e*='comment-submit']"], "comment_submit", target_id)
        detail = " / ".join(detail_parts)
        self._event(target_id, "comment", "succeeded", detail)
        return detail

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

    def _first_file_input(self, page: Page):
        locator = page.locator("input[type='file']")
        try:
            locator.first.wait_for(state="attached", timeout=2500)
            return locator.first
        except PlaywrightTimeoutError:
            return None

    def _claim_targets(self) -> list[dict[str, Any]]:
        with database.connect() as conn:
            rows = conn.execute(
                """
                SELECT t.*, c.comment_count, c.description
                FROM traffic_targets t
                LEFT JOIN contents c ON c.id = t.content_row_id
                WHERE t.campaign_id = ? AND t.status = 'pending'
                ORDER BY t.id ASC
                LIMIT ?
                """,
                (int(self.campaign["id"]), int(self.run_info["per_run_limit"])),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if ids:
                placeholders = ",".join(["?"] * len(ids))
                conn.execute(
                    f"UPDATE traffic_targets SET status = 'running', run_id = ?, updated_at = datetime('now', 'localtime') WHERE id IN ({placeholders})",
                    (self.run_id, *ids),
                )
            return database.rows_to_dicts(rows)

    def _create_runtime_target(self, page: Page, source_type: str) -> dict[str, Any]:
        url = page.url or DOUYIN_HOME
        video_id = self._active_video_id(page)
        title = page.title() or ("搜索关键词视频" if source_type == "search_keyword" else "随机推荐视频")
        key = traffic_workbench.normalize_target_key("dy", video_id, url)
        status = "running" if key else "skipped"
        error = "" if key else "无稳定视频标识"
        if not key:
            key = f"unstable:{source_type}:{int(time.time() * 1000)}"
        with database.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO traffic_targets(campaign_id, platform, source_type, target_key, content_url, title, keyword, selected_comment, status, error, run_id)
                VALUES(?, 'dy', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (int(self.campaign["id"]), source_type, key, url, title, str(self.campaign.get("keyword") or ""), "", status, error, self.run_id),
            )
            row = conn.execute("SELECT * FROM traffic_targets WHERE id = ?", (int(cur.lastrowid),)).fetchone()
            return database.row_to_dict(row) or {}

    def _active_video_id(self, page: Page) -> str:
        modal_id = parse_qs(urlparse(page.url or "").query).get("modal_id", [""])[0]
        if modal_id:
            return str(modal_id)
        try:
            return str(page.locator("[data-e2e='feed-active-video']").first.get_attribute("data-e2e-vid", timeout=1000) or "")
        except Exception:
            return ""

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
            item["action_image"] = bool(item.get("action_image"))
            item["comment_templates"] = _json_list(item.get("comment_templates"))
            item["image_asset_ids"] = [int(value) for value in _json_list(item.get("image_asset_ids")) if str(value).isdigit()]
            item["rule_config"] = traffic_workbench._json_dict(item.get("rule_config"))
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
        return json.dumps({"succeeded": self.done, "failed": self.failed, "skipped": self.skipped}, ensure_ascii=False)

    def _cdp_port(self) -> int:
        with database.connect() as conn:
            media_path = crawler_adapter.normalize_path(database.get_setting(conn, "media_crawler_path"))
        config = crawler_adapter._read_media_crawler_cdp_config(Path(media_path))
        return int(config.get("debug_port") or 9222)

    def _pick_image_asset(self) -> dict[str, Any]:
        ids = self.campaign.get("image_asset_ids") or []
        if not ids:
            raise RuntimeError("发送图片时没有选择图片素材")
        placeholders = ",".join(["?"] * len(ids))
        with database.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM traffic_assets WHERE enabled = 1 AND id IN ({placeholders})",
                tuple(ids),
            ).fetchall()
        assets = database.rows_to_dicts(rows)
        if not assets:
            raise RuntimeError("图片素材不存在或已停用")
        asset = random.choice(assets)
        if not Path(str(asset["path"])).exists():
            raise RuntimeError("图片素材文件不存在")
        return asset


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
