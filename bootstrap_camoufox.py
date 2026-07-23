#!/usr/bin/env python3
"""Bootstrap CF cookies через Camoufox — тот же движок, что на VPS.

Зачем: cf_clearance с Playwright Firefox часто не принимается Camoufox на сервере.
Этот скрипт берёт cookies уже в Camoufox → заливка на VPS совпадает по fingerprint.

Windows (лучше Python 3.11/3.12):
  cd D:\\work\\inpoland-parser-service   # или git-клон
  python -m venv .venv
  .\\.venv\\Scripts\\activate
  pip install -U pip
  pip install camoufox[geoip] python-dotenv
  camoufox fetch
  # в .env: PROXY_URL с session-{session}, PROXY_SESSION=как на VPS
  python bootstrap_camoufox.py

Потом:
  scp .cache/inpoland-storage.json u@VPS:~/inpoland-parser-service/.cache/
  # на VPS: restart + circuit reset
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

load_dotenv()

START_URL = os.getenv("BOOTSTRAP_URL", "https://in-poland.com/category/novosti/")
OUT = Path(os.getenv("STORAGE_STATE_PATH", ".cache/inpoland-storage.json"))
PROXY_URL = (os.getenv("PROXY_URL") or "").strip()
PROXY_SESSION = (os.getenv("PROXY_SESSION") or "inpoland7").strip()
LOCALE = os.getenv("BROWSER_LOCALE", "ru-RU")
HEADLESS = (os.getenv("BOOTSTRAP_HEADLESS") or "false").lower() in ("1", "true", "yes")
AUTO = "--auto" in sys.argv
AUTO_WAIT_S = max(15, int(os.getenv("BOOTSTRAP_AUTO_WAIT_S") or "120"))


def proxy_cfg(url: str) -> dict | None:
    if not url:
        return None
    url = url.replace("{session}", PROXY_SESSION)
    p = urlparse(url)
    scheme = (p.scheme or "http").lower()
    port = p.port or (443 if scheme == "https" else 80)
    cfg: dict = {"server": f"{scheme}://{p.hostname}:{port}"}
    if p.username:
        cfg["username"] = unquote(p.username)
    if p.password:
        cfg["password"] = unquote(p.password)
    return cfg


async def listing_ready(page) -> bool:
    try:
        title = ((await page.title()) or "").lower()
    except Exception:
        return False
    if any(
        x in title
        for x in ("публикац", "категор", "publikac", "kategor")
    ) or ("новост" in title and "польш" in title):
        return True
    for sel in (".post-preview", "a.post"):
        try:
            if await page.locator(sel).count() >= 3:
                return True
        except Exception:
            pass
    return False


async def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    proxy = proxy_cfg(PROXY_URL)

    print("=== bootstrap CF via Camoufox ===")
    print(f"Mode:    {'auto' if AUTO else 'manual (Enter)'}")
    print(f"URL:     {START_URL}")
    print(f"Session: {PROXY_SESSION}")
    print(f"Proxy:   {'yes' if proxy else 'NO'}")
    print(f"Headless:{HEADLESS}")
    print(f"Save:    {OUT.resolve()}")
    print()

    if not proxy:
        print("FAIL: PROXY_URL пустой — cookies без того же IP на VPS бесполезны.")
        return 2

    from camoufox.async_api import AsyncCamoufox

    async with AsyncCamoufox(
        headless=HEADLESS,
        geoip=False,
        locale=LOCALE,
    ) as browser:
        ctx_kwargs: dict = {
            "locale": LOCALE,
            "viewport": {"width": 1366, "height": 900},
            "proxy": proxy,
        }
        context = await browser.new_context(**ctx_kwargs)
        page = await context.new_page()
        print("goto…")
        await page.goto(START_URL, wait_until="commit", timeout=180_000)

        max_wait = AUTO_WAIT_S if AUTO else 300
        print(f"Жду ленту до {max_wait}s… (если CF — кликни/подожди в окне)")
        ok = False
        for i in range(max_wait):
            if await listing_ready(page):
                ok = True
                print(f"Лента OK на {i}s")
                break
            if i % 5 == 0:
                title = ""
                try:
                    title = await page.title()
                except Exception:
                    pass
                print(f"  wait… {i}s  title={title!r}")
            await asyncio.sleep(1)

        if not ok and not AUTO:
            print("\n>>> Лента не распознана. Если в окне УЖЕ видны новости — нажми Enter")
            try:
                await asyncio.to_thread(input)
                ok = True
            except EOFError:
                pass

        if not ok:
            print("FAIL: лента не появилась. Cookies НЕ сохранены.")
            return 1

        await context.storage_state(path=str(OUT))
        cookies = await context.cookies()
        names = [c.get("name", "") for c in cookies]
        has_cf = "cf_clearance" in names
        print(f"OK → {OUT.resolve()}")
        print(f"cookies={len(cookies)} cf_clearance={has_cf} names={','.join(names)}")
        if not has_cf:
            print("WARNING: нет cf_clearance — на VPS может не хватить.")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
