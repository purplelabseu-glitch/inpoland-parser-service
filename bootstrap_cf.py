#!/usr/bin/env python3
"""Ручной обход Cloudflare: видимый Chromium → галочка → сохранение cookies.

Запускать на машине С экраном (Windows / VNC), с тем же PROXY_URL что на VPS.
Потом файл .cache/inpoland-storage.json скопировать на VPS.

Пример (Windows, из папки проекта):
  .\\.venv\\Scripts\\activate
  pip install -r requirements.txt
  playwright install chromium
  copy .env с VPS или прописать PROXY_URL
  python bootstrap_cf.py

На VPS после копирования файла:
  sudo systemctl restart inpoland-parser
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

from app.config import settings  # noqa: E402
from app.parser import is_listing_html, looks_like_cloudflare  # noqa: E402

START_URL = "https://in-poland.com/category/novosti/"


def _proxy_cfg(proxy_url: str | None) -> dict | None:
    if not proxy_url:
        return None
    # без {session} — один sticky IP на весь ручной проход
    proxy_url = proxy_url.replace("{session}", "bootstrap1")
    p = urlparse(proxy_url)
    scheme = (p.scheme or "http").lower()
    port = p.port or (443 if scheme == "https" else 80)
    cfg: dict = {"server": f"{scheme}://{p.hostname}:{port}"}
    if p.username:
        cfg["username"] = p.username
    if p.password:
        cfg["password"] = p.password
    return cfg


async def main() -> int:
    out = Path(settings.storage_state_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    proxy = _proxy_cfg(settings.proxy_url)
    print("=== bootstrap Cloudflare для in-poland.com ===")
    print(f"URL:    {START_URL}")
    print(f"Proxy:  {'yes' if proxy else 'NO (direct)'}")
    print(f"Save →  {out.resolve()}")
    print()
    print("1) Откроется окно Chromium")
    print("2) Пройди Cloudflare (галочка «человек»)")
    print("3) Дождись ленты новостей (.post-preview)")
    print("4) Скрипт сам сохранит cookies и закроется")
    print()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            locale=settings.browser_locale,
            viewport={"width": 1366, "height": 900},
            proxy=proxy,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto(START_URL, wait_until="commit", timeout=120_000)

        print("Жду ленту (до 5 минут). Кликни галочку CF в окне браузера…")
        ok = False
        for i in range(300):
            try:
                html = await page.content()
            except Exception:
                await asyncio.sleep(1)
                continue
            if looks_like_cloudflare(html):
                if i % 10 == 0:
                    print(f"  …ещё Cloudflare ({i}s)")
                await asyncio.sleep(1)
                continue
            try:
                await page.wait_for_selector(".post-preview", timeout=2000)
                html = await page.content()
                if is_listing_html(html):
                    ok = True
                    break
            except Exception:
                pass
            await asyncio.sleep(1)

        if not ok:
            print("FAIL: лента не появилась. Cookies НЕ сохранены.")
            await browser.close()
            return 1

        await context.storage_state(path=str(out))
        print(f"OK: cookies сохранены → {out.resolve()}")
        # проверка cf_clearance
        cookies = await context.cookies()
        names = {c.get("name") for c in cookies}
        print(f"Cookies: {len(cookies)}, cf_clearance={'cf_clearance' in names}")
        await browser.close()

    print()
    print("Дальше скопируй файл на VPS:")
    print(f"  scp {out} u@31.130.203.134:/home/u/inpoland-parser-service/.cache/inpoland-storage.json")
    print("  ssh … 'sudo systemctl restart inpoland-parser'")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
