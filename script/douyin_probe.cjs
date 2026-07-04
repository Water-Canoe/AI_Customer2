#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");

const DEFAULT_TARGET_URL = "https://www.douyin.com/?recommend=1";
const DEFAULT_WAIT_MS = 8000;

function parseArgs(argv) {
  const args = {
    targetUrl: DEFAULT_TARGET_URL,
    profileDir: path.resolve(process.cwd(), "runtime", "douyin_probe_profile"),
    output: path.resolve(process.cwd(), "runtime", "douyin_probe", "latest.json"),
    screenshot: path.resolve(process.cwd(), "runtime", "douyin_probe", "latest.png"),
    waitMs: DEFAULT_WAIT_MS,
    channel: "",
    cdpUrl: process.env.DOUYIN_CDP_URL || "",
    advance: false,
    headless: false,
    keepOpen: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const item = argv[index];
    const next = argv[index + 1];
    if (item === "--target-url" && next) {
      args.targetUrl = next;
      index += 1;
    } else if (item === "--profile-dir" && next) {
      args.profileDir = path.resolve(process.cwd(), next);
      index += 1;
    } else if (item === "--output" && next) {
      args.output = path.resolve(process.cwd(), next);
      index += 1;
    } else if (item === "--screenshot" && next) {
      args.screenshot = path.resolve(process.cwd(), next);
      index += 1;
    } else if (item === "--wait-ms" && next) {
      args.waitMs = Math.max(0, Number(next) || DEFAULT_WAIT_MS);
      index += 1;
    } else if (item === "--channel" && next) {
      args.channel = next;
      index += 1;
    } else if (item === "--cdp" && next) {
      args.cdpUrl = next;
      index += 1;
    } else if (item === "--advance") {
      args.advance = true;
    } else if (item === "--headless") {
      args.headless = true;
    } else if (item === "--keep-open") {
      args.keepOpen = true;
    } else if (item === "--help" || item === "-h") {
      printHelp();
      process.exit(0);
    } else {
      throw new Error(`Unknown argument: ${item}`);
    }
  }

  return args;
}

function printHelp() {
  console.log(`Usage:
  node script/douyin_probe.cjs [options]

Options:
  --target-url <url>       Douyin page to open. Default: ${DEFAULT_TARGET_URL}
  --profile-dir <path>     Dedicated browser profile directory.
  --output <path>          JSON result path.
  --screenshot <path>      Screenshot path.
  --wait-ms <number>       Wait time after opening the page.
  --channel <name>         chrome, msedge, or bundled Playwright browser.
  --cdp <url>              Connect to an existing browser CDP endpoint.
  --advance                Safely test next-video navigation only.
  --headless               Run browser headless.
  --keep-open              Keep the browser open after probing.
`);
}

function loadPlaywright() {
  try {
    return require("playwright");
  } catch (error) {
    const hint = [
      "Cannot load the playwright package.",
      "Install it explicitly before running this probe, for example:",
      "  npm install --no-save playwright",
      "or set NODE_PATH to a temporary node_modules directory that contains playwright.",
    ].join("\n");
    error.message = `${hint}\n\nOriginal error: ${error.message}`;
    throw error;
  }
}

function ensureParentDir(filePath) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

async function openPageWithContext(playwright, args) {
  if (args.cdpUrl) {
    const browser = await playwright.chromium.connectOverCDP(args.cdpUrl);
    const context = browser.contexts()[0] || (await browser.newContext());
    const page = context.pages()[0] || (await context.newPage());
    return { browser, context, page, launchMode: "cdp" };
  }

  fs.mkdirSync(args.profileDir, { recursive: true });
  const candidates = args.channel
    ? [{ label: args.channel, channel: args.channel }]
    : [
        { label: "chrome", channel: "chrome" },
        { label: "msedge", channel: "msedge" },
        { label: "bundled", channel: "" },
      ];
  const failures = [];

  for (const candidate of candidates) {
    try {
      const context = await playwright.chromium.launchPersistentContext(args.profileDir, {
        channel: candidate.channel || undefined,
        headless: args.headless,
        viewport: { width: 1440, height: 900 },
        locale: "zh-CN",
      });
      const page = context.pages()[0] || (await context.newPage());
      return { browser: null, context, page, launchMode: `persistent:${candidate.label}` };
    } catch (error) {
      failures.push(`${candidate.label}: ${error.message}`);
    }
  }

  throw new Error(`No usable Chromium channel was found.\n${failures.join("\n")}`);
}

async function visibleSelectorState(page, selector) {
  return page.evaluate((selectorValue) => {
    const nodes = Array.from(document.querySelectorAll(selectorValue));
    const inspect = (node) => {
      const rect = node.getBoundingClientRect();
      const style = window.getComputedStyle(node);
      return {
        visible:
          rect.width > 0 &&
          rect.height > 0 &&
          style.display !== "none" &&
          style.visibility !== "hidden",
        text: (node.textContent || "").replace(/\s+/g, " ").trim().slice(0, 160),
      };
    };
    const inspected = nodes.map(inspect);
    return {
      selector: selectorValue,
      count: nodes.length,
      visibleCount: inspected.filter((item) => item.visible).length,
      firstText: inspected.find((item) => item.text)?.text || "",
    };
  }, selector);
}

async function collectProbeState(page) {
  const bodyText = await page.locator("body").innerText({ timeout: 2500 }).catch(() => "");
  const title = await page.title().catch(() => "");
  const url = page.url();
  const selectorStates = {};
  const selectors = {
    activeVideo: '[data-e2e="feed-active-video"]',
    slideList: '[data-e2e="slideList"]',
    likeButton: '[data-e2e="video-player-digg"]',
    collectButton: '[data-e2e="video-player-collect"]',
    commentButton: '[data-e2e="feed-comment-icon"]',
    commentSideCard: "#videoSideCard, #videoSideBar",
    loginPanel: '#login-panel-new, [id^="login-full-panel-"]',
  };

  for (const [key, selector] of Object.entries(selectors)) {
    selectorStates[key] = await visibleSelectorState(page, selector).catch((error) => ({
      selector,
      error: error.message,
    }));
  }

  const domState = await page.evaluate(() => {
    const normalize = (value) => (value || "").replace(/\s+/g, " ").trim();
    const active = document.querySelector('[data-e2e="feed-active-video"]');
    const buttons = Array.from(document.querySelectorAll("button, [role='button']"));
    const followButton = buttons.find((item) => normalize(item.textContent).includes("关注"));
    const text = normalize(document.body?.innerText || "");
    return {
      activeVideoId: active?.getAttribute("data-e2e-vid") || "",
      activeVideoText: normalize(active?.textContent || "").slice(0, 600),
      followVisible: Boolean(followButton),
      followText: normalize(followButton?.textContent || "").slice(0, 80),
      liveSuspected: /正在直播|直播中|live\.douyin\.com/i.test(text),
      adSuspected: /广告|赞助|了解详情|立即购买|去购买/.test(text),
      bodyPreview: text.slice(0, 800),
    };
  });

  const loginPromptVisible =
    /登录后|扫码登录|立即登录|手机号登录/.test(`${title}\n${bodyText}`) ||
    (selectorStates.loginPanel?.visibleCount || 0) > 0;
  // 登录短信验证码不等于风控验证码，这里只识别人机/安全验证信号。
  const verificationRequired = /安全验证|人机验证|验证码中间页|verify_check|secsdk-captcha|captcha|\/verify/i.test(
    `${url}\n${title}\n${bodyText}`,
  );

  return {
    collectedAt: new Date().toISOString(),
    url,
    title,
    loginPromptVisible,
    verificationRequired,
    selectorStates,
    domState,
  };
}

async function readActiveVideoId(page) {
  return page.evaluate(
    () =>
      document
        .querySelector('[data-e2e="feed-active-video"]')
        ?.getAttribute("data-e2e-vid") || "",
  );
}

async function advanceFeed(page) {
  const beforeAwemeId = await readActiveVideoId(page);
  const attempts = [];

  async function tryAfter(mode, action) {
    await action();
    await page.waitForTimeout(1600);
    const afterAwemeId = await readActiveVideoId(page);
    const changed = Boolean(afterAwemeId && afterAwemeId !== beforeAwemeId);
    attempts.push({ mode, beforeAwemeId, afterAwemeId, changed });
    return changed;
  }

  const slideList = page.locator('[data-e2e="slideList"]').first();
  if (await slideList.isVisible().catch(() => false)) {
    await slideList.hover().catch(() => {});
  }

  if (await tryAfter("mouse-wheel-1400", () => page.mouse.wheel(0, 1400))) {
    return { advanced: true, attempts };
  }
  if (await tryAfter("mouse-wheel-2200", () => page.mouse.wheel(0, 2200))) {
    return { advanced: true, attempts };
  }
  if (await tryAfter("keyboard-page-down", () => page.keyboard.press("PageDown"))) {
    return { advanced: true, attempts };
  }
  if (await tryAfter("keyboard-arrow-down", () => page.keyboard.press("ArrowDown"))) {
    return { advanced: true, attempts };
  }

  return { advanced: false, attempts };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const playwright = loadPlaywright();
  const { browser, context, page, launchMode } = await openPageWithContext(playwright, args);
  const result = {
    targetUrl: args.targetUrl,
    launchMode,
    profileDir: args.profileDir,
    engagementActions: "disabled",
    before: null,
    navigation: null,
    after: null,
    screenshotPath: "",
  };

  try {
    await page.goto(args.targetUrl, { waitUntil: "domcontentloaded", timeout: 60_000 });
    await page.waitForTimeout(args.waitMs);
    result.before = await collectProbeState(page);

    if (args.advance) {
      result.navigation = await advanceFeed(page);
      result.after = await collectProbeState(page);
    }

    if (args.screenshot) {
      ensureParentDir(args.screenshot);
      await page.screenshot({ path: args.screenshot, fullPage: false });
      result.screenshotPath = args.screenshot;
    }

    ensureParentDir(args.output);
    fs.writeFileSync(args.output, `${JSON.stringify(result, null, 2)}\n`, "utf8");
    console.log(`[douyin-probe] wrote ${args.output}`);
    if (result.screenshotPath) {
      console.log(`[douyin-probe] screenshot ${result.screenshotPath}`);
    }
  } finally {
    if (args.keepOpen) {
      console.log("[douyin-probe] keeping browser open because --keep-open was set");
      return;
    }
    await context.close().catch(() => {});
    if (browser) {
      await browser.close().catch(() => {});
    }
  }
}

main().catch((error) => {
  console.error(`[douyin-probe] ${error.stack || error.message}`);
  process.exit(1);
});
