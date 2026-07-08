from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cloakbrowser import launch_persistent_context_async

APP_DIR = Path(__file__).resolve().parent
DEFAULT_PROFILE_DIR = APP_DIR / "runtime" / "cloak_profile"
RECO_URL = "https://www.kuaishou.com/new-reco"
RECO_ACTIONS = {"follow", "like", "favorite"}


def parse_actions(value: str) -> list[str]:
    actions = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in actions if item not in RECO_ACTIONS]
    if unknown:
        raise ValueError(f"Unknown action(s): {', '.join(unknown)}")
    return actions


async def open_context(profile_dir: Path = DEFAULT_PROFILE_DIR) -> Any:
    profile_dir.mkdir(parents=True, exist_ok=True)
    # CloakBrowser supplies the Chromium build and Playwright-compatible context.
    return await launch_persistent_context_async(
        str(profile_dir),
        headless=False,
        locale="zh-CN",
        timezone="Asia/Shanghai",
        viewport=None,
        humanize=True,
        human_preset="careful",
        args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
    )


async def visible_center(page: Any, kind: str) -> dict[str, Any] | None:
    return await page.evaluate(
        """
        (kind) => {
          const visible = [...document.querySelectorAll('*')].map(el => {
            const r = el.getBoundingClientRect();
            const cls = String(el.className || '');
            const text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
            return {el, r, cls, text};
          }).filter(x =>
            x.r.width > 5 &&
            x.r.height > 5 &&
            x.r.bottom > 0 &&
            x.r.right > 0 &&
            x.r.y > 250 &&
            x.r.y < 720 &&
            x.r.x > 1000
          );
          let hit = null;
          if (kind === 'follow') {
            hit = visible.find(x => x.cls === 'btn' && x.r.width >= 15 && x.r.height >= 10 && x.r.y > 390 && x.r.y < 440);
          } else if (kind === 'like') {
            hit = visible.find(x => x.cls.includes('hover-tip like')) || visible.find(x => x.cls.includes('like-btn'));
          } else if (kind === 'favorite') {
            hit = visible.find(x => x.cls.includes('hover-tip favorite')) || visible.find(x => x.cls.includes('star'));
          } else if (kind === 'comment') {
            hit = visible.find(x => x.cls.includes('commentPanel'));
          }
          if (!hit) return null;
          return {
            x: hit.r.x + hit.r.width / 2,
            y: hit.r.y + hit.r.height / 2,
            cls: hit.cls,
            text: hit.text,
            box: [hit.r.x, hit.r.y, hit.r.width, hit.r.height]
          };
        }
        """,
        kind,
    )


async def capture_response(resp: Any, events: list[dict[str, Any]]) -> None:
    req = resp.request
    if req.resource_type not in {"xhr", "fetch"}:
        return
    parsed = urlparse(resp.url)
    if parsed.netloc != "www.kuaishou.com":
        return
    path = parsed.path.lower()
    if path not in {
        "/rest/v/relation/follow",
        "/rest/v/photo/like",
        "/rest/v/photo/collect",
        "/rest/v/photo/comment/add",
    }:
        return
    try:
        body = await resp.text()
    except Exception as exc:  # pragma: no cover - runtime-only network edge.
        body = f"<body read error {exc!r}>"
    events.append({"method": req.method, "status": resp.status, "url": resp.url, "body": body[:1200]})


async def click_reco_action(page: Any, action: str, dry_run: bool) -> dict[str, Any]:
    point = await visible_center(page, action)
    result: dict[str, Any] = {"action": action, "point": point, "clicked": False}
    if point and not dry_run:
        await page.mouse.click(point["x"], point["y"])
        await page.wait_for_timeout(3000)
        result["clicked"] = True
    return result


async def send_text_comment(page: Any, message: str, dry_run: bool) -> dict[str, Any]:
    point = await visible_center(page, "comment")
    if not point:
        raise RuntimeError("Comment button was not visible")
    if dry_run:
        return {"action": "comment", "point": point, "clicked": False}

    await page.mouse.click(point["x"], point["y"])
    await page.wait_for_timeout(3000)
    editor = page.locator(".comment-input input").last
    await editor.fill(message)
    await page.wait_for_timeout(800)
    send = await page.evaluate(
        """
        () => {
          const el = document.querySelector('.comment-input .send-btn');
          if (!el) return null;
          const r = el.getBoundingClientRect();
          return {x: r.x + r.width / 2, y: r.y + r.height / 2, box: [r.x, r.y, r.width, r.height]};
        }
        """
    )
    if not send:
        raise RuntimeError("Send button was not visible after filling comment")
    await page.mouse.click(send["x"], send["y"])
    await page.wait_for_timeout(5000)
    return {"action": "comment", "point": point, "send": send, "clicked": True}


async def run_reco(actions: list[str], comment: str | None, dry_run: bool) -> dict[str, Any]:
    context = await open_context()
    page = context.pages[0] if context.pages else await context.new_page()
    events: list[dict[str, Any]] = []
    page.on("response", lambda resp: asyncio.create_task(capture_response(resp, events)))
    try:
        page.set_default_timeout(12000)
        await page.goto(RECO_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)

        results = [await click_reco_action(page, action, dry_run) for action in actions]
        if comment:
            results.append(await send_text_comment(page, comment, dry_run))
        await page.wait_for_timeout(3000)
        return {"ok": True, "results": results, "events": events}
    finally:
        await context.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kuaishou web recommendation interaction automation")
    parser.add_argument("--actions", default="", help="Comma-separated: follow,like,favorite")
    parser.add_argument("--comment", default="", help="Text comment to send on the recommendation video")
    parser.add_argument("--dry-run", action="store_true", help="Locate controls without clicking")
    parser.add_argument("--self-check", action="store_true", help="Run cheap argument checks")
    return parser


def self_check() -> None:
    assert parse_actions("follow,like,favorite") == ["follow", "like", "favorite"]
    try:
        parse_actions("follow,dm")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown action should fail")


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    if args.self_check:
        self_check()
        return {"ok": True, "self_check": True}
    actions = parse_actions(args.actions) if args.actions else []
    if not actions and not args.comment:
        raise ValueError("Provide --actions, --comment, or --self-check")
    return await run_reco(actions, args.comment.strip() or None, args.dry_run)


def main() -> None:
    args = build_parser().parse_args()
    result = asyncio.run(async_main(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
