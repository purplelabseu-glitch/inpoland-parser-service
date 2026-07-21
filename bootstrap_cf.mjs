/**
 * Ручной обход Cloudflare для in-poland.com (Node + Playwright).
 *
 *   cd D:\work\git\inpoland-parser-service
 *   # PROXY_URL в .env (scp с VPS!)
 *   node bootstrap_cf.mjs
 *
 * Если лента уже на экране — нажми Enter в этом терминале, cookies сохранятся сразу.
 */
import { chromium } from "playwright";
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

function proxyFromUrl(url) {
  if (!url) return undefined;
  const u = new URL(url.replace("{session}", "bootstrap1"));
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

async function main() {
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  const proxy = proxyFromUrl(PROXY_URL);

  console.log("=== bootstrap CF (Node) ===");
  console.log("URL:  ", START_URL);
  console.log("Proxy:", proxy ? "yes" : "NO — без PROXY_URL cookies с VPS не сработают!");
  console.log("Save: ", OUT);
  console.log("");

  const browser = await chromium.launch({
    headless: false,
    args: ["--disable-blink-features=AutomationControlled"],
  });
  const context = await browser.newContext({
    locale: LOCALE,
    viewport: { width: 1366, height: 900 },
    proxy,
    userAgent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  });
  const page = await context.newPage();
  await page.goto(START_URL, { waitUntil: "commit", timeout: 180000 });

  // Enter в терминале = принудительное сохранение
  let forceSave = false;
  const enterPromise = waitEnter().then(() => {
    forceSave = true;
  });

  let ok = false;
  let info = null;
  for (let i = 0; i < 300; i++) {
    if (forceSave) {
      ok = true;
      info = { sel: "manual-Enter", n: 0 };
      console.log("Сохраняю по Enter…");
      break;
    }
    info = await listingReady(page);
    if (info.ok) {
      ok = true;
      console.log(`Лента OK: ${info.sel} x${info.n}`);
      break;
    }
    if (i % 5 === 0) {
      const title = await page.title().catch(() => "");
      const url = page.url();
      console.log(`  жду… ${i}s  title=${JSON.stringify(title)}  url=${url}`);
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
  await browser.close();
  // не ждём Enter дальше
  process.exit(0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
