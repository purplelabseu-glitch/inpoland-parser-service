"""Браузерный fetch для in-poland.com: Camoufox → fallback Playwright Chromium.

Прокси — как в mobilede-parser-service: HTTP напрямую, SOCKS через локальный релей.
Без ручного CDP, headless, под cron/systemd.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from urllib.parse import urlparse

import httpx
from camoufox.async_api import AsyncCamoufox
from playwright.async_api import async_playwright

from .config import settings
from .errors import ParserError, ProxyError, UpstreamForbiddenError
from .parser import (
    extract_article,
    extract_listing_items,
    is_article_html,
    is_listing_html,
    listing_page_url,
    looks_like_cloudflare,
)
from .proxy_relay import Socks5Relay

logger = logging.getLogger(__name__)


def _relay_upstream(proxy_url: str) -> str:
    return proxy_url.replace("socks5h://", "socks5://", 1)


def _playwright_proxy_from_url(proxy_url: str) -> dict[str, str]:
    parsed = urlparse(proxy_url)
    scheme = (parsed.scheme or "").lower()
    if scheme in ("http", "https"):
        port = parsed.port or (443 if scheme == "https" else 80)
        cfg: dict[str, str] = {"server": f"{scheme}://{parsed.hostname}:{port}"}
        if parsed.username:
            cfg["username"] = parsed.username
        if parsed.password:
            cfg["password"] = parsed.password
        return cfg
    raise ValueError(f"Unsupported proxy scheme for direct mode: {scheme}")


class BrowserManager:
    def __init__(self) -> None:
        self._relay: Socks5Relay | None = None
        self._camoufox_cm: AsyncCamoufox | None = None
        self._camoufox = None
        self._pw = None
        self._chromium = None
        self._semaphore = asyncio.Semaphore(settings.browser_concurrency)
        self._proxy_template: str | None = settings.proxy_url
        self.last_engine: str = ""

    @staticmethod
    def _apply_session(template: str) -> str:
        if "{session}" not in template:
            return template
        return template.replace("{session}", secrets.token_hex(6))

    async def start(self) -> None:
        engine = (settings.browser_engine or "auto").lower()
        if engine in ("auto", "camoufox", "firefox"):
            self._camoufox_cm = AsyncCamoufox(
                headless=settings.browser_headless,
                geoip=False,
                locale=settings.browser_locale,
            )
            self._camoufox = await self._camoufox_cm.__aenter__()
            logger.info("Camoufox запущен (headless=%s)", settings.browser_headless)

        if engine in ("auto", "chromium", "chrome"):
            self._pw = await async_playwright().start()
            self._chromium = await self._pw.chromium.launch(
                headless=settings.browser_headless,
            )
            logger.info("Chromium запущен (headless=%s)", settings.browser_headless)

        if self._camoufox is None and self._chromium is None:
            raise ParserError(f"Неизвестный BROWSER_ENGINE={settings.browser_engine}")

    async def stop(self) -> None:
        if self._camoufox_cm is not None:
            await self._camoufox_cm.__aexit__(None, None, None)
            self._camoufox_cm = None
            self._camoufox = None
        if self._chromium is not None:
            await self._chromium.close()
            self._chromium = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
        if self._relay is not None:
            await self._relay.stop()
            self._relay = None
        logger.info("Браузеры остановлены")

    async def _resolve_proxy_config(self) -> dict[str, str] | None:
        if not self._proxy_template:
            return None
        upstream = _relay_upstream(self._apply_session(self._proxy_template))
        parsed = urlparse(upstream)
        scheme = (parsed.scheme or "").lower()
        if scheme in ("http", "https"):
            return _playwright_proxy_from_url(upstream)
        if scheme.startswith("socks5"):
            if self._relay is None or self._relay.upstream_url != upstream:
                if self._relay is not None:
                    await self._relay.stop()
                self._relay = await Socks5Relay(upstream).start()
            return {"server": self._relay.address}
        raise ProxyError(f"Неподдерживаемая схема PROXY_URL: {scheme}")

    async def _rotate_proxy_session(self) -> None:
        if not self._proxy_template or "{session}" not in self._proxy_template:
            return
        if self._relay is not None:
            await self._relay.stop()
            self._relay = None
        logger.info("Прокси-сессия: новый {session}")

    async def _refresh_ip(self) -> None:
        url = settings.proxy_refresh_url
        if not url:
            return
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
            logger.info("Запрошена смена IP (HTTP %s)", resp.status_code)
        except httpx.HTTPError as exc:
            logger.warning("Не удалось дёрнуть refresh-ip: %s", exc)
        await asyncio.sleep(settings.refresh_wait_s)

    async def check_proxy(self) -> dict[str, str | bool]:
        if not self._proxy_template:
            return {"configured": False, "ok": True, "detail": "PROXY_URL not set (direct)"}
        upstream = _relay_upstream(self._apply_session(self._proxy_template))
        parsed = urlparse(upstream)
        try:
            async with httpx.AsyncClient(proxy=upstream, timeout=15) as client:
                resp = await client.get("https://api.ipify.org?format=json")
            ok = resp.status_code == 200
            return {
                "configured": True,
                "ok": ok,
                "scheme": parsed.scheme or "",
                "host": parsed.hostname or "",
                "status": str(resp.status_code),
                "detail": resp.text[:120] if ok else resp.text[:200],
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "configured": True,
                "ok": False,
                "scheme": parsed.scheme or "",
                "host": parsed.hostname or "",
                "detail": f"{type(exc).__name__}: {exc}",
            }

    def _engines_order(self) -> list[tuple[str, object]]:
        engine = (settings.browser_engine or "auto").lower()
        order: list[tuple[str, object]] = []
        if engine == "chromium":
            if self._chromium is not None:
                order.append(("chromium", self._chromium))
        elif engine == "camoufox":
            if self._camoufox is not None:
                order.append(("camoufox", self._camoufox))
        else:
            if self._camoufox is not None:
                order.append(("camoufox", self._camoufox))
            if self._chromium is not None:
                order.append(("chromium", self._chromium))
        return order

    @staticmethod
    def _html_ok(html: str, expect: str) -> bool:
        if not html or len(html) < 500:
            return False
        if looks_like_cloudflare(html):
            return False
        if expect == "listing":
            return is_listing_html(html)
        if expect == "article":
            return is_article_html(html)
        return True

    async def _fetch_with_browser(
        self, browser, url: str, engine_name: str
    ) -> str:
        proxy_cfg = await self._resolve_proxy_config()
        ctx_kwargs: dict = {"locale": settings.browser_locale}
        if proxy_cfg:
            ctx_kwargs["proxy"] = proxy_cfg
        if engine_name == "chromium":
            ctx_kwargs["user_agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        context = await browser.new_context(**ctx_kwargs)
        page = await context.new_page()
        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=settings.nav_timeout_ms,
            )
            html = ""
            for _ in range(settings.challenge_wait_s):
                html = await page.content()
                if looks_like_cloudflare(html):
                    await asyncio.sleep(1)
                    continue
                if len(html) > 1500:
                    return html
                await asyncio.sleep(1)
            if looks_like_cloudflare(html):
                raise UpstreamForbiddenError(
                    f"Cloudflare не пройден ({engine_name})"
                )
            return html
        finally:
            await context.close()

    async def fetch_html(self, url: str, expect: str = "any") -> str:
        """Открывает URL. expect: listing | article | any — для валидации HTML."""
        engines = self._engines_order()
        if not engines:
            raise ParserError("Браузер не инициализирован")

        attempts = max(settings.browser_max_retries, 1)
        last_error: Exception | None = None

        async with self._semaphore:
            for engine_name, browser in engines:
                for attempt in range(1, attempts + 1):
                    await self._rotate_proxy_session()
                    logger.info(
                        "Fetch [%s] попытка %d/%d -> %s",
                        engine_name,
                        attempt,
                        attempts,
                        url,
                    )
                    try:
                        html = await self._fetch_with_browser(
                            browser, url, engine_name
                        )
                        if self._html_ok(html, expect):
                            self.last_engine = engine_name
                            return html
                        raise UpstreamForbiddenError(
                            f"Страница без контента / CF ({engine_name}, expect={expect})"
                        )
                    except (UpstreamForbiddenError, ProxyError) as exc:
                        last_error = exc
                        logger.warning("%s", exc)
                        if attempt < attempts:
                            await self._refresh_ip()
                            await asyncio.sleep(settings.retry_backoff)
                    except Exception as exc:  # noqa: BLE001
                        last_error = ProxyError(f"Ошибка навигации ({engine_name}): {exc}")
                        logger.warning("%s", last_error)
                        if attempt < attempts:
                            await self._refresh_ip()
                            await asyncio.sleep(settings.retry_backoff)
                logger.warning(
                    "Движок %s исчерпал попытки — пробуем следующий (если есть)",
                    engine_name,
                )

            assert last_error is not None
            raise last_error

    async def collect_category(
        self, category_url: str, max_pages: int, section_slug: str
    ) -> tuple[list[dict], int]:
        all_items: list[dict] = []
        seen: set[str] = set()
        pages_fetched = 0

        for page in range(1, max_pages + 1):
            page_url = listing_page_url(category_url, page)
            logger.info(
                "Сбор [%s] страница %d/%d: %s",
                section_slug,
                page,
                max_pages,
                page_url,
            )
            html = await self.fetch_html(page_url, expect="listing")
            pages_fetched += 1
            items = extract_listing_items(html)
            new = [it for it in items if it["url"] not in seen]
            if not new:
                logger.info("Страница %d без новых ссылок — стоп", page)
                break
            for it in new:
                seen.add(it["url"])
                it["section_slug"] = section_slug
                it["category_url"] = category_url.rstrip("/") + "/"
                all_items.append(it)

        return all_items, pages_fetched

    async def parse_article(self, url: str) -> tuple[dict, str]:
        html = await self.fetch_html(url, expect="article")
        article = extract_article(html, source_url=url)
        return article, html


browser_manager = BrowserManager()
