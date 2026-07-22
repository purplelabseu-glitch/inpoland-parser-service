/**
 * Ручной обход Cloudflare (локально) → cookies для VPS.
 *
 * По умолчанию — Playwright Firefox (тот же движок-семейство, что Camoufox на VPS).
 *
 *   cd D:\work\git\inpoland-parser-service
 *   # PROXY_URL в .env = как на VPS (Smartproxy)
 *   npx playwright install firefox
 *   node bootstrap_cf.mjs
 *
 * Другие варианты:
 *   set BOOTSTRAP_BROWSER=firefox
 *   set BOOTSTRAP_BROWSER=chrome
 *   set BOOTSTRAP_BROWSER=msedge
 *   set BOOTSTRAP_BROWSER=chromium
 *
 * VPN выключить. Если лента видна — Enter в терминале.
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

function proxyFromUrl(url) {
  if (!url) return undefined;
  const session = `boot${Date.now().toString(36)}`;
  const u = new URL(url.replaceAll("{session}", session));
  const server = `${u.protocol}//${u.hostname}:${u.port || (u.protocol === "https:" ? 443 : 80)}`;
  const cfg = { server };
  if (u.username) cfg.username = decodeURIComponent(u.username);
  if (u.password) cfg.password = decodeURIComponent(u.password);
  return cfg;
}

async function listingReady(page) {
  const checks = [
    ".post-preview",
    "a.post",
    ".post-preview h2 a",
    "article",
    ".flex-block",
  ];
  for (const sel of checks) {
    try {
      const n = await page.locator(sel).count();
      if (n > 0) return { ok: true, sel, n };
    } catch {
      /* ignore */
    }
  }
  try {
    const html = await page.content();
    if (html.includes("post-preview") || /in-poland\.com\/\d{4}\//i.test(html)) {
      return { ok: true, sel: "html-marker", n: 1 };
    }
  } catch {
    /* ignore */
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
  const headless = false;
  const args = ["--disable-blink-features=AutomationControlled"];

  if (BROWSER_KIND === "firefox" || BROWSER_KIND === "ff") {
    const exe = systemFirefoxPath();
    try {
      if (exe) {
        const browser = await firefox.launch({ headless, executablePath: exe });
        console.log("Browser: system-firefox", exe);
        return { browser, family: "firefox" };
      }
    } catch (e) {
      console.warn(`Системный Firefox не стартовал (${e.message}), берём Playwright Firefox`);
    }
    const browser = await firefox.launch({ headless });
    console.log("Browser: playwright-firefox");
    return { browser, family: "firefox" };
  }

  // Chrome / Edge / Chromium
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
      console.log("Browser:", ch || "playwright-chromium");
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
  console.log("URL:    ", START_URL);
  console.log("Kind:   ", BROWSER_KIND);
  console.log("Proxy:  ", proxy ? "yes (Smartproxy)" : "NO — без PROXY_URL cookies на VPS не сработают!");
  console.log("Save:   ", OUT);
  console.log("VPS tip: BROWSER_ENGINE=camoufox  (тот же Firefox-family)");
  console.log("");

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
  await page.goto(START_URL, { waitUntil: "commit", timeout: 180000 });

  let forceSave = false;
  waitEnter().then(() => {
    forceSave = true;
  });

  let ok = false;
  for (let i = 0; i < 300; i++) {
    if (forceSave) {
      ok = true;
      console.log("Сохраняю по Enter…");
      break;
    }
    const info = await listingReady(page);
    if (info.ok) {
      ok = true;
      console.log(`Лента OK: ${info.sel} x${info.n}`);
      break;
    }
    if (i % 5 === 0) {
      const title = await page.title().catch(() => "");
      console.log(`  жду… ${i}s  title=${JSON.stringify(title)}  url=${page.url()}`);
    }
    await page.waitForTimeout(1000);
  }

  if (!ok) {
    console.error("FAIL: лента не найдена и Enter не нажали. Cookies НЕ сохранены.");
    await browser.close();
    process.exit(1);
  }

  await context.storageState({ path: OUT });
  const cookies = await context.cookies();
  const hasCf = cookies.some((c) => c.name === "cf_clearance");
  console.log(`OK → ${OUT}`);
  console.log(`cookies=${cookies.length} cf_clearance=${hasCf}`);
  if (!hasCf) {
    console.warn("WARNING: нет cf_clearance — на VPS может снова быть 403.");
  }
  await browser.close();
  process.exit(0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
