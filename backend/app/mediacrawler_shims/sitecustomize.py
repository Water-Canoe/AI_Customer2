from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Callable


def _creator_video_limit() -> int:
    raw = os.getenv("AI_CUSTOMER_CREATOR_CONTENT_LIMIT", "") or os.getenv("AI_CUSTOMER_DY_CREATOR_VIDEO_LIMIT", "0")
    try:
        value = int(raw or "0")
    except ValueError:
        return 0
    return max(0, value)


def _creator_platform() -> str:
    return os.getenv("AI_CUSTOMER_CREATOR_PLATFORM", "").strip().lower()


def _skip_content_ids() -> set[str]:
    raw = os.getenv("AI_CUSTOMER_SKIP_CONTENT_IDS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


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


def _filter_skipped_contents(contents: list[Any], id_getter: Callable[[Any], str]) -> tuple[list[Any], int]:
    # 过滤项目库已经采过的内容，让 creator 翻页继续寻找新的作品。
    skipped_ids = _skip_content_ids()
    if not skipped_ids:
        return contents, 0
    result: list[Any] = []
    skipped_count = 0
    for content in contents:
        content_id = id_getter(content)
        if content_id and content_id in skipped_ids:
            skipped_count += 1
            continue
        result.append(content)
    return result, skipped_count


def _douyin_content_id(content: Any) -> str:
    if not isinstance(content, dict):
        return ""
    aweme_info = content.get("aweme_info") if isinstance(content.get("aweme_info"), dict) else {}
    return str(content.get("aweme_id") or aweme_info.get("aweme_id") or "")


def _xhs_content_id(content: Any) -> str:
    if not isinstance(content, dict):
        return ""
    return str(content.get("note_id") or content.get("id") or "")


def _ks_content_id(content: Any) -> str:
    if not isinstance(content, dict):
        return ""
    photo = content.get("photo") if isinstance(content.get("photo"), dict) else {}
    return str(photo.get("id") or content.get("id") or "")


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
    platform = _creator_platform()
    if platform and platform != "dy":
        return
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
            aweme_list, skipped_known = _filter_skipped_contents(aweme_list, _douyin_content_id)
            utils.logger.info(
                f"[AI_Customer.creator_limit] sec_user_id:{sec_user_id} page video len:{len(aweme_list)} skipped_known:{skipped_known} limit:{current_limit} content_cutoff:{_content_cutoff_ts() or 0}"
            )
            if not aweme_list:
                if reached_old_content:
                    posts_has_more = 0
                continue
            remaining = current_limit - len(result)
            if remaining <= 0:
                break
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


def _patch_xhs_creator_note_limit() -> None:
    platform = _creator_platform()
    if platform and platform != "xhs":
        return
    limit = _creator_video_limit()
    if limit <= 0:
        return

    try:
        from media_platform.xhs import client as xhs_client
        from tools import utils
    except ModuleNotFoundError:
        sys.path.insert(0, os.getcwd())
        from media_platform.xhs import client as xhs_client
        from tools import utils

    target = xhs_client.XiaoHongShuClient.get_all_notes_by_creator
    if getattr(target, "_ai_customer_limited", False):
        return

    async def limited_get_all_notes_by_creator(
        self,
        user_id: str,
        crawl_interval: float = 1.0,
        callback=None,
        xsec_token: str = "",
        xsec_source: str = "pc_feed",
    ):
        current_limit = _creator_video_limit()
        if current_limit <= 0:
            return await target(self, user_id, crawl_interval, callback, xsec_token, xsec_source)

        result = []
        notes_has_more = True
        notes_cursor = ""
        while notes_has_more and len(result) < current_limit:
            notes_res = await self.get_notes_by_creator(
                user_id, notes_cursor, xsec_token=xsec_token, xsec_source=xsec_source
            )
            if not notes_res:
                utils.logger.error(
                    f"[AI_Customer.creator_limit] xhs creator unavailable user_id:{user_id}"
                )
                break
            notes_has_more = notes_res.get("has_more", False)
            notes_cursor = notes_res.get("cursor", "")
            if "notes" not in notes_res:
                utils.logger.info(f"[AI_Customer.creator_limit] xhs notes missing user_id:{user_id} res:{notes_res}")
                break
            notes = notes_res["notes"]
            notes, reached_old_content = _filter_recent_contents(notes)
            notes, skipped_known = _filter_skipped_contents(notes, _xhs_content_id)
            utils.logger.info(
                f"[AI_Customer.creator_limit] xhs user_id:{user_id} page notes len:{len(notes)} skipped_known:{skipped_known} limit:{current_limit}"
            )
            if not notes:
                if reached_old_content:
                    notes_has_more = False
                await asyncio.sleep(crawl_interval)
                continue
            remaining = current_limit - len(result)
            if remaining <= 0:
                break
            selected = notes[:remaining]
            if callback and selected:
                await callback(selected)
            result.extend(selected)
            if reached_old_content:
                notes_has_more = False
            await asyncio.sleep(crawl_interval)
        utils.logger.info(f"[AI_Customer.creator_limit] xhs user_id:{user_id} limited notes total:{len(result)}")
        return result

    limited_get_all_notes_by_creator._ai_customer_limited = True  # type: ignore[attr-defined]
    xhs_client.XiaoHongShuClient.get_all_notes_by_creator = limited_get_all_notes_by_creator


def _patch_ks_creator_video_limit() -> None:
    platform = _creator_platform()
    if platform and platform != "ks":
        return
    limit = _creator_video_limit()
    if limit <= 0:
        return

    try:
        from media_platform.kuaishou import client as kuaishou_client
        from tools import utils
    except ModuleNotFoundError:
        sys.path.insert(0, os.getcwd())
        from media_platform.kuaishou import client as kuaishou_client
        from tools import utils

    target = kuaishou_client.KuaiShouClient.get_all_videos_by_creator
    if getattr(target, "_ai_customer_limited", False):
        return

    async def limited_get_all_videos_by_creator(self, user_id: str, crawl_interval: float = 1.0, callback=None):
        current_limit = _creator_video_limit()
        if current_limit <= 0:
            return await target(self, user_id, crawl_interval, callback)

        result = []
        pcursor = ""
        while pcursor != "no_more" and len(result) < current_limit:
            videos_res = await self.get_video_by_creater(user_id, pcursor)
            if not videos_res:
                utils.logger.error(
                    f"[AI_Customer.creator_limit] ks creator unavailable user_id:{user_id}"
                )
                break
            vision_profile_photo_list = videos_res.get("visionProfilePhotoList", {})
            pcursor = vision_profile_photo_list.get("pcursor", "")
            videos = vision_profile_photo_list.get("feeds", [])
            videos, skipped_known = _filter_skipped_contents(videos, _ks_content_id)
            utils.logger.info(
                f"[AI_Customer.creator_limit] ks user_id:{user_id} page videos len:{len(videos)} skipped_known:{skipped_known} limit:{current_limit}"
            )
            if not videos:
                await asyncio.sleep(crawl_interval)
                continue
            remaining = current_limit - len(result)
            if remaining <= 0:
                break
            selected = videos[:remaining]
            if callback and selected:
                await callback(selected)
            result.extend(selected)
            await asyncio.sleep(crawl_interval)
        utils.logger.info(f"[AI_Customer.creator_limit] ks user_id:{user_id} limited videos total:{len(result)}")
        return result

    limited_get_all_videos_by_creator._ai_customer_limited = True  # type: ignore[attr-defined]
    kuaishou_client.KuaiShouClient.get_all_videos_by_creator = limited_get_all_videos_by_creator


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

    # MyCrawler reads this global in Douyin detail/comment throttling paths.
    config.CRAWLER_MAX_SLEEP_SEC = value
    utils.logger.info(f"[AI_Customer.sleep_interval] CRAWLER_MAX_SLEEP_SEC set to {value:g}")


_patch_douyin_sleep_interval()
_patch_douyin_creator_video_limit()
_patch_xhs_creator_note_limit()
_patch_ks_creator_video_limit()
_patch_comment_cutoff()
_patch_douyin_http_resilience()
