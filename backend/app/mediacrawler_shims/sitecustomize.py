from __future__ import annotations

import os
import sys
from typing import Any


def _creator_video_limit() -> int:
    try:
        value = int(os.getenv("AI_CUSTOMER_DY_CREATOR_VIDEO_LIMIT", "0") or "0")
    except ValueError:
        return 0
    return max(0, value)


def _comment_cutoff_ts() -> int:
    try:
        value = int(os.getenv("AI_CUSTOMER_COMMENT_CUTOFF_TS", "0") or "0")
    except ValueError:
        return 0
    return max(0, value)


def _content_cutoff_ts() -> int:
    try:
        value = int(os.getenv("AI_CUSTOMER_CONTENT_CUTOFF_TS", "0") or "0")
    except ValueError:
        return 0
    return max(0, value)


def _douyin_detail_sleep_seconds() -> float | None:
    raw = os.getenv("AI_CUSTOMER_DY_DETAIL_SLEEP_SEC", "")
    if raw == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return min(max(value, 0.0), 10.0)


def _dy_resilient_http_enabled() -> bool:
    return os.getenv("AI_CUSTOMER_DY_RESILIENT_HTTP", "").lower() in {"1", "true", "yes"}


def _comment_ts_seconds(comment: dict[str, Any]) -> int | None:
    for key in ("create_time", "timestamp", "ctime", "time"):
        value = comment.get(key)
        if value in ("", None):
            continue
        try:
            raw = int(float(str(value).replace(",", "")))
        except ValueError:
            continue
        return raw // 1000 if raw > 10_000_000_000 else raw
    return None


def _content_ts_seconds(content: dict[str, Any]) -> int | None:
    for key in ("create_time", "timestamp", "time"):
        value = content.get(key)
        if value in ("", None):
            continue
        try:
            raw = int(float(str(value).replace(",", "")))
        except ValueError:
            continue
        return raw // 1000 if raw > 10_000_000_000 else raw
    return None


def _filter_recent_comments(comments: Any) -> list[dict[str, Any]]:
    cutoff = _comment_cutoff_ts()
    if cutoff <= 0 or not isinstance(comments, list):
        return comments if isinstance(comments, list) else []
    result: list[dict[str, Any]] = []
    for comment in comments:
        if not isinstance(comment, dict):
            result.append(comment)
            continue
        ts = _comment_ts_seconds(comment)
        if ts is None or ts >= cutoff:
            result.append(comment)
    return result


def _filter_recent_contents(contents: list[Any]) -> tuple[list[Any], bool]:
    cutoff = _content_cutoff_ts()
    if cutoff <= 0:
        return contents, False
    result: list[Any] = []
    reached_old_content = False
    for content in contents:
        if not isinstance(content, dict):
            result.append(content)
            continue
        ts = _content_ts_seconds(content)
        if ts is None or ts >= cutoff:
            result.append(content)
            continue
        reached_old_content = True
    return result, reached_old_content


def _patch_comment_response_method(client_class: Any, method_name: str, comments_key: str, stop_key: str, stop_value: Any) -> None:
    target = getattr(client_class, method_name)
    patch_flag = f"_ai_customer_comment_cutoff_{method_name}"
    if getattr(target, patch_flag, False):
        return

    async def filtered_method(self, *args, **kwargs):
        response = await target(self, *args, **kwargs)
        if not isinstance(response, dict):
            return response
        comments = response.get(comments_key)
        filtered = _filter_recent_comments(comments)
        if isinstance(comments, list):
            response[comments_key] = filtered
            if comments and not filtered:
                response[stop_key] = stop_value
        return response

    setattr(filtered_method, patch_flag, True)
    setattr(client_class, method_name, filtered_method)


def _patch_comment_cutoff() -> None:
    if _comment_cutoff_ts() <= 0:
        return
    try:
        from media_platform.douyin import client as douyin_client
        from media_platform.kuaishou import client as kuaishou_client
        from media_platform.xhs import client as xhs_client
    except ModuleNotFoundError:
        sys.path.insert(0, os.getcwd())
        from media_platform.douyin import client as douyin_client
        from media_platform.kuaishou import client as kuaishou_client
        from media_platform.xhs import client as xhs_client

    _patch_comment_response_method(douyin_client.DouYinClient, "get_aweme_comments", "comments", "has_more", 0)
    _patch_comment_response_method(douyin_client.DouYinClient, "get_sub_comments", "comments", "has_more", 0)
    _patch_comment_response_method(xhs_client.XiaoHongShuClient, "get_note_comments", "comments", "has_more", False)
    _patch_comment_response_method(xhs_client.XiaoHongShuClient, "get_note_sub_comments", "comments", "has_more", False)
    _patch_comment_response_method(kuaishou_client.KuaiShouClient, "get_video_comments", "rootCommentsV2", "pcursorV2", "no_more")
    _patch_comment_response_method(kuaishou_client.KuaiShouClient, "get_video_sub_comments", "subCommentsV2", "pcursorV2", "no_more")


def _patch_douyin_creator_video_limit() -> None:
    limit = _creator_video_limit()
    if limit <= 0:
        return

    try:
        from media_platform.douyin import client as douyin_client
        from tools import utils
    except ModuleNotFoundError:
        # sitecustomize can run before the script cwd is visible on sys.path.
        sys.path.insert(0, os.getcwd())
        from media_platform.douyin import client as douyin_client
        from tools import utils

    target = douyin_client.DouYinClient.get_all_user_aweme_posts
    if getattr(target, "_ai_customer_limited", False):
        return

    async def limited_get_all_user_aweme_posts(self, sec_user_id: str, callback=None):
        # Limit each creator so creator-mode tasks do not scan the full account history.
        current_limit = _creator_video_limit()
        if current_limit <= 0:
            return await target(self, sec_user_id, callback)

        posts_has_more = 1
        max_cursor = ""
        result = []
        while posts_has_more == 1 and len(result) < current_limit:
            aweme_post_res = await self.get_user_aweme_posts(sec_user_id, max_cursor)
            posts_has_more = aweme_post_res.get("has_more", 0)
            max_cursor = aweme_post_res.get("max_cursor")
            aweme_list = aweme_post_res.get("aweme_list") if aweme_post_res.get("aweme_list") else []
            aweme_list, reached_old_content = _filter_recent_contents(aweme_list)
            utils.logger.info(
                f"[AI_Customer.creator_limit] sec_user_id:{sec_user_id} page video len:{len(aweme_list)} limit:{current_limit} content_cutoff:{_content_cutoff_ts() or 0}"
            )
            if not aweme_list:
                break
            remaining = current_limit - len(result)
            selected = aweme_list[:remaining]
            if callback and selected:
                await callback(selected)
            result.extend(selected)
            if reached_old_content:
                posts_has_more = 0

        utils.logger.info(
            f"[AI_Customer.creator_limit] sec_user_id:{sec_user_id} limited video total:{len(result)}"
        )
        return result

    limited_get_all_user_aweme_posts._ai_customer_limited = True  # type: ignore[attr-defined]
    douyin_client.DouYinClient.get_all_user_aweme_posts = limited_get_all_user_aweme_posts


def _patch_douyin_http_resilience() -> None:
    if not _dy_resilient_http_enabled():
        return
    try:
        import httpx
        from media_platform.douyin import client as douyin_client
        from media_platform.douyin import core as douyin_core
        from tools import utils
    except ModuleNotFoundError:
        sys.path.insert(0, os.getcwd())
        import httpx
        from media_platform.douyin import client as douyin_client
        from media_platform.douyin import core as douyin_core
        from tools import utils

    get_aweme_detail = douyin_core.DouYinCrawler.get_aweme_detail
    if not getattr(get_aweme_detail, "_ai_customer_http_resilient", False):

        async def resilient_get_aweme_detail(self, aweme_id: str, semaphore):
            try:
                return await get_aweme_detail(self, aweme_id, semaphore)
            except httpx.HTTPError as exc:
                utils.logger.error(
                    f"[AI_Customer.http_resilience] skip aweme detail {aweme_id}: {exc.__class__.__name__} {exc}"
                )
                return None

        resilient_get_aweme_detail._ai_customer_http_resilient = True  # type: ignore[attr-defined]
        douyin_core.DouYinCrawler.get_aweme_detail = resilient_get_aweme_detail

    get_comments = douyin_core.DouYinCrawler.get_comments
    if not getattr(get_comments, "_ai_customer_http_resilient", False):

        async def resilient_get_comments(self, aweme_id: str, semaphore):
            try:
                return await get_comments(self, aweme_id, semaphore)
            except httpx.HTTPError as exc:
                utils.logger.error(
                    f"[AI_Customer.http_resilience] skip aweme comments {aweme_id}: {exc.__class__.__name__} {exc}"
                )
                return None

        resilient_get_comments._ai_customer_http_resilient = True  # type: ignore[attr-defined]
        douyin_core.DouYinCrawler.get_comments = resilient_get_comments

    get_user_aweme_posts = douyin_client.DouYinClient.get_user_aweme_posts
    if not getattr(get_user_aweme_posts, "_ai_customer_http_resilient", False):

        async def resilient_get_user_aweme_posts(self, sec_user_id: str, max_cursor: str = ""):
            try:
                return await get_user_aweme_posts(self, sec_user_id, max_cursor)
            except httpx.HTTPError as exc:
                utils.logger.error(
                    f"[AI_Customer.http_resilience] stop creator posts {sec_user_id}: {exc.__class__.__name__} {exc}"
                )
                return {"has_more": 0, "max_cursor": max_cursor, "aweme_list": []}

        resilient_get_user_aweme_posts._ai_customer_http_resilient = True  # type: ignore[attr-defined]
        douyin_client.DouYinClient.get_user_aweme_posts = resilient_get_user_aweme_posts


def _patch_douyin_sleep_interval() -> None:
    value = _douyin_detail_sleep_seconds()
    if value is None:
        return
    try:
        import config
        from tools import utils
    except ModuleNotFoundError:
        sys.path.insert(0, os.getcwd())
        import config
        from tools import utils

    # MediaCrawler reads this global in Douyin detail/comment throttling paths.
    config.CRAWLER_MAX_SLEEP_SEC = value
    utils.logger.info(f"[AI_Customer.sleep_interval] CRAWLER_MAX_SLEEP_SEC set to {value:g}")


_patch_douyin_sleep_interval()
_patch_douyin_creator_video_limit()
_patch_comment_cutoff()
_patch_douyin_http_resilience()
