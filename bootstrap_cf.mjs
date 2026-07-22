/**
 * Обход Cloudflare (локально) → cookies для VPS.
 *
 * Ручной:
 *   node bootstrap_cf.mjs
 *   (Enter, когда лента видна)
 *
 * Авто (Task Scheduler / cron), без Enter:
 *   node bootstrap_cf.mjs --auto
 *   ждёт ленту до BOOTSTRAP_AUTO_WAIT_S (по умолчанию 90), иначе exit 1
 *
 * Важно для IP: задайте одинаковый PROXY_SESSION на Windows и VPS
 * (или уберите {session} из PROXY_URL и пропишите фиксированный session-...).
 * Иначе cookies с одного IP, а парсер на другом.
 *
 * VPN выключить. В .env нужен PROXY_URL.
 */
import { chromium, firefox } from "playwright";
import dotenv from "dotenv";
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

dotenv.config();

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const START_URL =
  process.env.BOOTSTRAP_URL || "https://in-poland.com/category/novosti/";
const OUT = path.resolve(
  process.env.STORAGE_STATE_PATH ||
    path.join(__dirname, ".cache", "inpoland-storage.json")
);
const PROXY_URL = (process.env.PROXY_URL || "").trim();
const LOCALE = process.env.BROWSER_LOCALE || "ru-RU";
const BROWSER_KIND = (
  process.env.BOOTSTRAP_BROWSER ||
  process.env.BOOTSTRAP_CHANNEL ||
  "firefox"
).toLowerCase();
const AUTO = process.argv.includes("--auto");
const AUTO_WAIT_S = Math.max(
  15,
  parseInt(process.env.BOOTSTRAP_AUTO_WAIT_S || "90", 10) || 90
);
const HEADLESS =
  process.env.BOOTSTRAP_HEADLESS === "1" ||
  process.env.BOOTSTRAP_HEADLESS === "true";
const REQUIRE_CF =
  process.env.BOOTSTRAP_REQUIRE_CF === "1" ||
  process.env.BOOTSTRAP_REQUIRE_CF === "true";

function proxyFromUrl(url) {
  if (!url) return undefined;
  // Стабильная session — чтобы IP совпал с VPS (см. PROXY_SESSION в .env)
  const session =
    (process.env.PROXY_SESSION || process.env.BOOTSTRAP_SESSION || "").trim() ||
    `boot${Date.now().toString(36)}`;
  const u = new URL(url.replaceAll("{session}", session));
  const server = `${u.protocol}//${u.hostname}:${u.port || (u.protocol === "https:" ? 443 : 80)}`;
  const cfg = { server };
  if (u.username) cfg.username = decodeURIComponent(u.username);
  if (u.password) cfg.password = decodeURIComponent(u.password);
  console.log("Proxy session:", session);
  return cfg;
}

async function dismissConsent(page) {
  const candidates = [
    'button:has-text("Принять")',
    'button:has-text("Соглас")',
    'button:has-text("Accept")',
    'button:has-text("Allow")',
    'button:has-text("OK")',
    '[id*="accept"]',
    '[class*="accept"]',
    'a:has-text("Принять")',
  ];
  for (const sel of candidates) {
    try {
      const loc = page.locator(sel).first();
      if ((await loc.count()) > 0 && (await loc.isVisible())) {
        await loc.click({ timeout: 2000 });
        console.log("Clicked consent:", sel);
        await page.waitForTimeout(500);
        return;
      }
    } catch {
      /* ignore */
    }
  }
}

async function listingReady(page) {
  let title = "";
  try {
    title = (await page.title()) || "";
  } catch {
    return { ok: false };
  }
  const titleLow = title.toLowerCase();

  // Listing title = success (do NOT scan page HTML for CF script URLs — false negatives)
  if (
    titleLow.includes("публикац") ||
    titleLow.includes("категор") ||
    titleLow.includes("publikac") ||
    titleLow.includes("kategor") ||
    (titleLow.includes("новост") && titleLow.includes("польш"))
  ) {
    return { ok: true, sel: "title", n: 1 };
  }

  // Obvious CF interstitial only
  if (/just a moment/i.test(title) || /^$/i.test(title.trim())) {
    try {
      const html = (await page.content()).toLowerCase();
      if (html.includes("processing...") && html.length < 2000) {
        return { ok: false };
      }
    } catch {
      return { ok: false };
    }
  }

  const checks = [".post-preview", ".post-preview h2 a", "a.post"];
  for (const sel of checks) {
    try {
      const n = await page.locator(sel).count();
      if (n >= 3) return { ok: true, sel, n };
    } catch {
      /* ignore */
    }
  }
  return { ok: false };
}

function waitEnter() {
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    rl.question(
      "\n>>> Если лента УЖЕ видна в окне — нажми Enter здесь, чтобы сохранить cookies\n",
      () => {
        rl.close();
        resolve();
      }
    );
  });
}

function systemFirefoxPath() {
  const candidates = [
    process.env.FIREFOX_PATH,
    "C:\\Program Files\\Mozilla Firefox\\firefox.exe",
    "C:\\Program Files (x86)\\Mozilla Firefox\\firefox.exe",
    "/usr/bin/firefox",
    "/usr/lib/firefox/firefox",
  ].filter(Boolean);
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

async function launchBrowser() {
  const headless = HEADLESS;
  const args = ["--disable-blink-features=AutomationControlled"];

  if (BROWSER_KIND === "firefox" || BROWSER_KIND === "ff") {
    // System Firefox often fails under Playwright on Windows — use bundled first.
    // Set BOOTSTRAP_SYSTEM_FIREFOX=1 to try installed Firefox first.
    const preferSystem =
      process.env.BOOTSTRAP_SYSTEM_FIREFOX === "1" ||
      process.env.BOOTSTRAP_SYSTEM_FIREFOX === "true";
    if (preferSystem) {
      const exe = systemFirefoxPath();
      try {
        if (exe) {
          const browser = await firefox.launch({ headless, executablePath: exe });
          console.log("Browser: system-firefox", exe, "headless=", headless);
          return { browser, family: "firefox" };
        }
      } catch (e) {
        console.warn(`System Firefox failed (${e.message}), using Playwright Firefox`);
      }
    }
    const browser = await firefox.launch({ headless });
    console.log("Browser: playwright-firefox headless=", headless);
    return { browser, family: "firefox" };
  }

  const common = { headless, args };
  const channels = [];
  if (BROWSER_KIND === "chromium") channels.push(null);
  else if (BROWSER_KIND === "msedge" || BROWSER_KIND === "edge") {
    channels.push("msedge", "chrome", null);
  } else if (BROWSER_KIND === "chrome") {
    channels.push("chrome", "msedge", null);
  } else {
    channels.push("chrome", "msedge", null);
  }

  let lastErr = null;
  for (const ch of channels) {
    try {
      const opts = { ...common };
      if (ch) opts.channel = ch;
      const browser = await chromium.launch(opts);
      console.log("Browser:", ch || "playwright-chromium", "headless=", headless);
      return { browser, family: "chromium" };
    } catch (e) {
      lastErr = e;
      console.warn(`Не удалось channel=${ch || "chromium"}: ${e.message}`);
    }
  }
  throw lastErr || new Error("Cannot launch browser");
}

async function main() {
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  const proxy = proxyFromUrl(PROXY_URL);

  console.log("=== bootstrap CF ===");
  console.log("Mode:   ", AUTO ? `auto (wait ${AUTO_WAIT_S}s)` : "manual (Enter)");
  console.log("URL:    ", START_URL);
  console.log("Kind:   ", BROWSER_KIND);
  console.log("Proxy:  ", proxy ? "yes (Smartproxy)" : "NO — без PROXY_URL cookies на VPS не сработают!");
  console.log("Save:   ", OUT);
  console.log("");

  if (!proxy) {
    console.error("FAIL: PROXY_URL пустой — автозаливку на VPS делать нельзя.");
    process.exit(2);
  }

  const { browser } = await launchBrowser();
  const context = await browser.newContext({
    locale: LOCALE,
    viewport: { width: 1366, height: 900 },
    proxy,
    colorScheme: "light",
  });

  await context.addInitScript(() => {
    Object.defineProperty(navigator, "webdriver", { get: () => undefined });
  });

  const page = await context.newPage();
  const gotoAttempts = AUTO ? 3 : 1;
  let gotoOk = false;
  let lastGotoErr = null;
  for (let g = 1; g <= gotoAttempts; g++) {
    try {
      console.log(`goto attempt ${g}/${gotoAttempts}...`);
      await page.goto(START_URL, { waitUntil: "commit", timeout: 180000 });
      gotoOk = true;
      break;
    } catch (e) {
      lastGotoErr = e;
      console.warn(`goto failed (${e.message})`);
      if (g < gotoAttempts) {
        await page.waitForTimeout(2000 * g);
      }
    }
  }
  if (!gotoOk) {
    await browser.close();
    throw lastGotoErr || new Error("goto failed");
  }

  let forceSave = false;
  if (!AUTO) {
    waitEnter().then(() => {
      forceSave = true;
    });
  }

  const maxWait = AUTO ? AUTO_WAIT_S : 300;
  // Give CF/proxy a moment before first "ready" check
  await page.waitForTimeout(AUTO ? 2000 : 1000);
  await dismissConsent(page);
  let ok = false;
  for (let i = 0; i < maxWait; i++) {
    if (forceSave) {
      ok = true;
      console.log("Saving on Enter…");
      break;
    }
    if (i > 0 && i % 10 === 0) {
      await dismissConsent(page);
    }
    const info = await listingReady(page);
    if (info.ok) {
      ok = true;
      console.log(`Лента OK: ${info.sel} x${info.n}`);
      break;
    }
    if (i % 5 === 0) {
      const title = await page.title().catch(() => "");
      console.log(`  wait… ${i}s  title=${JSON.stringify(title)}  url=${page.url()}`);
    }
    try {
      await page.waitForTimeout(1000);
    } catch (e) {
      console.error("Browser/page closed while waiting — do not close Firefox until script finishes.");
      throw e;
    }
  }

  if (!ok) {
    console.error(
      AUTO
        ? `FAIL(--auto): лента не появилась за ${AUTO_WAIT_S}s. Cookies НЕ сохранены.`
        : "FAIL: лента не найдена и Enter не нажали. Cookies НЕ сохранены."
    );
    await browser.close();
    process.exit(1);
  }

  await context.storageState({ path: OUT });
  const cookies = await context.cookies();
  const hasCf = cookies.some((c) => c.name === "cf_clearance");
  console.log(`OK → ${OUT}`);
  console.log(`cookies=${cookies.length} cf_clearance=${hasCf} names=${cookies.map((c) => c.name).join(",")}`);
  if (!hasCf) {
    console.warn("WARNING: no cf_clearance cookie (page OK anyway — uploading).");
    if (AUTO && REQUIRE_CF) {
      console.error("FAIL(--auto): BOOTSTRAP_REQUIRE_CF=true and no cf_clearance.");
      try {
        fs.unlinkSync(OUT);
      } catch {
        /* ignore */
      }
      await browser.close();
      process.exit(1);
    }
  }
  await browser.close();
  process.exit(0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
