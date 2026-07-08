from __future__ import annotations

import asyncio
from pathlib import Path

from cloakbrowser import launch_persistent_context_async


async def main() -> None:
    profile = Path(__file__).resolve().parent / "runtime" / "cloak_profile"
    profile.mkdir(parents=True, exist_ok=True)
    # Keep this profile separate from Douyin so login cookies do not collide.
    context = await launch_persistent_context_async(
        str(profile),
        headless=False,
        locale="zh-CN",
        timezone="Asia/Shanghai",
        viewport=None,
        humanize=True,
        human_preset="careful",
        args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
    )
    page = context.pages[0] if context.pages else await context.new_page()
    # The login window is intentionally long lived for manual QR/SMS handling.
    await page.goto("https://www.kuaishou.com/", wait_until="domcontentloaded", timeout=60000)
    print("Kuaishou login browser opened. Keep this process running.", flush=True)
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
