/**
 * Ручной обход Cloudflare для in-poland.com (Node + Playwright).
 * Не нужен Python 3.12 / MSVC.
 *
 *   cd D:\work\git\inpoland-parser-service
 *   npm install playwright dotenv
 *   npx playwright install chromium
 *   # PROXY_URL в .env
 *   node bootstrap_cf.mjs
 */
import { chromium } from "playwright";
import dotenv from "dotenv";
import fs from "node:fs";
import path from "node:path";
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

function looksLikeCf(html) {
  const low = (html || "").toLowerCase();
  return (
    low.includes("just a moment") ||
    low.includes("cf-browser-verification") ||
    low.includes("challenge-platform") ||
    low.includes("cdn-cgi/challenge") ||
    low.includes("checking your browser")
  );
}

function proxyFromUrl(url) {
  if (!url) return undefined;
  const u = new URL(url.replace("{session}", "bootstrap1"));
  const server = `${u.protocol}//${u.hostname}:${u.port || (u.protocol === "https:" ? 443 : 80)}`;
  const cfg = { server };
  if (u.username) cfg.username = decodeURIComponent(u.username);
  if (u.password) cfg.password = decodeURIComponent(u.password);
  return cfg;
}

async function main() {
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  const proxy = proxyFromUrl(PROXY_URL);

  console.log("=== bootstrap CF (Node) ===");
  console.log("URL:  ", START_URL);
  console.log("Proxy:", proxy ? "yes" : "NO — пропиши PROXY_URL в .env");
  console.log("Save: ", OUT);
  console.log("");
  console.log("Окно Chromium → галочка CF → жду ленту (.post-preview)…");

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

  let ok = false;
  for (let i = 0; i < 300; i++) {
    let html = "";
    try {
      html = await page.content();
    } catch {
      await page.waitForTimeout(1000);
      continue;
    }
    if (looksLikeCf(html)) {
      if (i % 10 === 0) console.log(`  CF ещё активен (${i}s) — кликни галочку`);
      await page.waitForTimeout(1000);
      continue;
    }
    try {
      await page.waitForSelector(".post-preview", { timeout: 2000 });
      html = await page.content();
      if (html.includes("post-preview")) {
        ok = true;
        break;
      }
    } catch {
      /* keep waiting */
    }
    await page.waitForTimeout(1000);
  }

  if (!ok) {
    console.error("FAIL: лента не появилась. Cookies НЕ сохранены.");
    await browser.close();
    process.exit(1);
  }

  await context.storageState({ path: OUT });
  const cookies = await context.cookies();
  const hasCf = cookies.some((c) => c.name === "cf_clearance");
  console.log(`OK → ${OUT}`);
  console.log(`cookies=${cookies.length} cf_clearance=${hasCf}`);
  await browser.close();

  console.log("");
  console.log("Дальше:");
  console.log(
    `  scp "${OUT}" u@31.130.203.134:/home/u/inpoland-parser-service/.cache/inpoland-storage.json`
  );
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
