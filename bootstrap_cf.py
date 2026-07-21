#!/usr/bin/env python3
"""Ручной обход Cloudflare (минимум зависимостей: только playwright + dotenv).

Windows (Python 3.11/3.12 предпочтительно, но с этим скриптом хватит и 3.14):
  python -m venv .venv
  .\\.venv\\Scripts\\activate
  pip install playwright python-dotenv
  playwright install chromium
  # PROXY_URL в .env (тот же что на VPS)
  python bootstrap_cf.py

Потом scp .cache/inpoland-storage.json на VPS и restart сервиса.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

START_URL = os.getenv("BOOTSTRAP_URL", "https://in-poland.com/category/novosti/")
OUT = Path(os.getenv("STORAGE_STATE_PATH", ".cache/inpoland-storage.json"))
PROXY_URL = os.getenv("PROXY_URL", "").strip()
LOCALE = os.getenv("BROWSER_LOCALE", "ru-RU")


def looks_like_cf(html: str) -> bool:
    low = html.lower()
    return any(
        m in low
        for m in (
            "just a moment",
            "cf-browser-verification",
            "challenge-platform",
            "cdn-cgi/challenge",
            "checking your browser",
        )
    )


def proxy_cfg(url: str) -> dict | None:
    if not url:
        return None
    url = url.replace("{session}", "bootstrap1")
    p = urlparse(url)
    scheme = (p.scheme or "http").lower()
    port = p.port or (443 if scheme == "https" else 80)
    cfg: dict = {"server": f"{scheme}://{p.hostname}:{port}"}
    if p.username:
        cfg["username"] = p.username
    if p.password:
        cfg["password"] = p.password
    return cfg


async def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    proxy = proxy_cfg(PROXY_URL)

    print("=== bootstrap CF in-poland.com ===")
    print(f"URL:   {START_URL}")
    print(f"Proxy: {'yes' if proxy else 'NO — пропиши PROXY_URL в .env!'}")
    print(f"Save:  {OUT.resolve()}")
    print()
    if not proxy:
        print("WARNING: без PROXY_URL cookies могут не подойти для VPS (другой IP).")
    print("Откроется Chromium → нажми галочку Cloudflare → жди ленту новостей.")
    print()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            locale=LOCALE,
            viewport={"width": 1366, "height": 900},
            proxy=proxy,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto(START_URL, wait_until="commit", timeout=180_000)

        print("Жду .post-preview (до 5 мин)…")
        ok = False
        for i in range(300):
            try:
                html = await page.content()
            except Exception:
                await asyncio.sleep(1)
                continue
            if looks_like_cf(html):
                if i % 10 == 0:
                    print(f"  CF ещё активен ({i}s) — кликни галочку в окне")
                await asyncio.sleep(1)
                continue
            try:
                await page.wait_for_selector(".post-preview", timeout=2000)
                html = await page.content()
                if "post-preview" in html:
                    ok = True
                    break
            except Exception:
                pass
            await asyncio.sleep(1)

        if not ok:
            title = ""
            try:
                title = await page.title()
            except Exception:
                pass
            print(f"FAIL: лента не появилась (title={title!r}). Cookies НЕ сохранены.")
            await browser.close()
            return 1

        await context.storage_state(path=str(OUT))
        cookies = await context.cookies()
        names = {c.get("name") for c in cookies}
        print(f"OK → {OUT.resolve()}")
        print(f"cookies={len(cookies)} cf_clearance={'cf_clearance' in names}")
        await browser.close()

    print()
    print("Скопируй на VPS:")
    print(
        f'  scp "{OUT}" '
        "u@31.130.203.134:/home/u/inpoland-parser-service/.cache/inpoland-storage.json"
    )
    print("  ssh u@31.130.203.134 \"sudo systemctl restart inpoland-parser\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
