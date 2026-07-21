"""Браузерный fetch для in-poland.com: Chromium + cookies (CF) → fallback Camoufox.

Cloudflare: сохраняем storage_state (cf_clearance и др.), sticky-контекст Chromium
и по возможности один sticky IP прокси — как warm Chrome-сессия в inpoland-bot.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from pathlib import Path
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


def _expect_selector(expect: str) -> str | None:
    if expect == "listing":
        return ".post-preview, a.post"
    if expect == "article":
        return ".flex-block, figure figcaption, h1"
    return None


class BrowserManager:
    def __init__(self) -> None:
        self._relay: Socks5Relay | None = None
        self._camoufox_cm: AsyncCamoufox | None = None
        self._camoufox = None
        self._pw = None
        self._chromium = None
        # sticky Chromium context (cookies + same proxy)
        self._chromium_context = None
        self._sticky_proxy_cfg: dict[str, str] | None = None
        self._semaphore = asyncio.Semaphore(settings.browser_concurrency)
        self._proxy_template: str | None = settings.proxy_url
        self.last_engine: str = ""
        self._storage_path = Path(settings.storage_state_path)

    @staticmethod
    def _apply_session(template: str) -> str:
        if "{session}" not in template:
            return template
        return template.replace("{session}", secrets.token_hex(6))

    async def start(self) -> None:
        engine = (settings.browser_engine or "auto").lower()

        if engine in ("auto", "chromium", "chrome"):
            try:
                await self._ensure_chromium()
            except Exception as exc:  # noqa: BLE001
                if engine == "chromium":
                    raise
                logger.warning("Chromium на старте недоступен: %s", exc)

        if engine in ("auto", "camoufox", "firefox"):
            self._camoufox_cm = AsyncCamoufox(
                headless=settings.browser_headless,
                geoip=False,
                locale=settings.browser_locale,
            )
            self._camoufox = await self._camoufox_cm.__aenter__()
            logger.info("Camoufox запущен (headless=%s)", settings.browser_headless)

        if self._camoufox is None and self._chromium is None:
            raise ParserError(
                f"Нет доступного браузера (BROWSER_ENGINE={settings.browser_engine})"
            )

        if self._storage_path.is_file():
            logger.info("Найден storage_state (cookies): %s", self._storage_path)
        else:
            logger.info(
                "storage_state пока нет (%s) — после первого успешного обхода CF сохранится",
                self._storage_path,
            )

    async def _ensure_chromium(self) -> None:
        if self._chromium is not None:
            return
        if self._pw is None:
            self._pw = await async_playwright().start()
        try:
            self._chromium = await self._pw.chromium.launch(
                headless=settings.browser_headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            logger.info("Chromium запущен (headless=%s)", settings.browser_headless)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Chromium недоступен: %s", exc)
            self._chromium = None
            raise

    async def stop(self) -> None:
        await self._close_sticky_chromium()
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

    async def _close_sticky_chromium(self, *, save: bool = False) -> None:
        if self._chromium_context is not None:
            if save:
                try:
                    await self._save_storage(self._chromium_context)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Не удалось сохранить cookies: %s", exc)
            try:
                await self._chromium_context.close()
            except Exception:  # noqa: BLE001
                pass
            self._chromium_context = None
            # sticky proxy cfg оставляем — тот же IP полезен с куками

    async def _save_storage(self, context) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(self._storage_path))
        logger.info("Cookies сохранены: %s", self._storage_path)

    async def _resolve_proxy_config(self, *, force_new_session: bool = False) -> dict[str, str] | None:
        if not self._proxy_template:
            return None

        # sticky: переиспользуем уже поднятый proxy cfg / relay
        if (
            settings.sticky_proxy
            and not force_new_session
            and self._sticky_proxy_cfg is not None
        ):
            return self._sticky_proxy_cfg

        if force_new_session or self._sticky_proxy_cfg is None:
            await self._rotate_proxy_session()

        upstream = _relay_upstream(self._apply_session(self._proxy_template))
        parsed = urlparse(upstream)
        scheme = (parsed.scheme or "").lower()
        if scheme in ("http", "https"):
            cfg = _playwright_proxy_from_url(upstream)
        elif scheme.startswith("socks5"):
            if self._relay is None or self._relay.upstream_url != upstream:
                if self._relay is not None:
                    await self._relay.stop()
                self._relay = await Socks5Relay(upstream).start()
            cfg = {"server": self._relay.address}
        else:
            raise ProxyError(f"Неподдерживаемая схема PROXY_URL: {scheme}")

        self._sticky_proxy_cfg = cfg
        return cfg

    async def _rotate_proxy_session(self) -> None:
        if not self._proxy_template:
            return
        if self._relay is not None:
            await self._relay.stop()
            self._relay = None
        self._sticky_proxy_cfg = None
        if "{session}" in self._proxy_template:
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
                "cookies_file": str(self._storage_path),
                "cookies_present": self._storage_path.is_file(),
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
        if engine == "camoufox":
            if self._camoufox is not None:
                order.append(("camoufox", self._camoufox))
        elif engine == "chromium":
            if self._chromium is not None:
                order.append(("chromium", self._chromium))
        else:
            if self._chromium is not None:
                order.append(("chromium", self._chromium))
            if self._camoufox is not None:
                order.append(("camoufox", self._camoufox))
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

    async def _safe_content(self, page) -> str:
        for _ in range(10):
            try:
                return await page.content()
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "navigating" in msg or "changing the content" in msg:
                    await asyncio.sleep(0.5)
                    continue
                raise
        return await page.content()

    async def _get_chromium_context(self, *, force_new: bool = False):
        if force_new:
            await self._close_sticky_chromium()

        if settings.sticky_chromium and self._chromium_context is not None:
            return self._chromium_context, False  # shared, don't close

        proxy_cfg = await self._resolve_proxy_config(force_new_session=force_new)
        ctx_kwargs: dict = {
            "locale": settings.browser_locale,
            "viewport": {"width": 1366, "height": 900},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        if proxy_cfg:
            ctx_kwargs["proxy"] = proxy_cfg
        if self._storage_path.is_file():
            ctx_kwargs["storage_state"] = str(self._storage_path)
            logger.info("Подставляю cookies из %s", self._storage_path)

        context = await self._chromium.new_context(**ctx_kwargs)
        if settings.sticky_chromium:
            self._chromium_context = context
            return context, False
        return context, True  # caller must close

    async def _fetch_chromium(self, url: str, expect: str, *, reset_session: bool) -> str:
        context, must_close = await self._get_chromium_context(force_new=reset_session)
        page = await context.new_page()
        selector = _expect_selector(expect)
        try:
            logger.info(
                "goto [chromium] %s (expect=%s, cookies=%s, reset=%s)",
                url,
                expect,
                self._storage_path.is_file(),
                reset_session,
            )
            await page.goto(
                url,
                wait_until="commit",
                timeout=settings.nav_timeout_ms,
            )
            html = await self._wait_for_content(page, "chromium", expect, selector)
            await self._save_storage(context)
            return html
        finally:
            await page.close()
            if must_close:
                await context.close()

    async def _fetch_camoufox(self, url: str, expect: str) -> str:
        proxy_cfg = await self._resolve_proxy_config(force_new_session=False)
        ctx_kwargs: dict = {
            "locale": settings.browser_locale,
            "viewport": {"width": 1366, "height": 900},
        }
        if proxy_cfg:
            ctx_kwargs["proxy"] = proxy_cfg
        if self._storage_path.is_file():
            ctx_kwargs["storage_state"] = str(self._storage_path)

        context = await self._camoufox.new_context(**ctx_kwargs)
        page = await context.new_page()
        selector = _expect_selector(expect)
        try:
            logger.info("goto [camoufox] %s (expect=%s)", url, expect)
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=settings.nav_timeout_ms,
            )
            html = await self._wait_for_content(page, "camoufox", expect, selector)
            try:
                await self._save_storage(context)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Camoufox: cookies не сохранены: %s", exc)
            return html
        finally:
            await context.close()

    async def _wait_for_content(self, page, engine_name: str, expect: str, selector: str | None) -> str:
        deadline = max(settings.challenge_wait_s, settings.content_wait_s)
        html = ""
        for tick in range(deadline):
            # 1) Сначала контент (CF-маркеры в DOM часто ложные)
            if selector:
                try:
                    loc = page.locator(selector)
                    if await loc.count() > 0:
                        html = await self._safe_content(page)
                        if self._html_ok(html, expect) or (
                            expect == "listing" and is_listing_html(html)
                        ):
                            logger.info(
                                "[%s] OK selector=%s count=%d len=%d",
                                engine_name,
                                selector,
                                await loc.count(),
                                len(html),
                            )
                            return html
                except Exception:  # noqa: BLE001
                    pass

            html = await self._safe_content(page)
            if self._html_ok(html, expect):
                logger.info("[%s] OK html len=%d", engine_name, len(html))
                return html

            if looks_like_cloudflare(html):
                if tick % 5 == 0:
                    logger.info("[%s] Cloudflare/challenge… %ds", engine_name, tick)
                await asyncio.sleep(1)
                continue

            await asyncio.sleep(1)

        title = ""
        try:
            title = await page.title()
        except Exception:  # noqa: BLE001
            pass
        # Последний шанс: title уже «живой»
        if expect == "listing" and "публикации" in title.lower():
            html = await self._safe_content(page)
            if is_listing_html(html) or len(html) > 20000:
                logger.info("[%s] OK by title=%r len=%d", engine_name, title, len(html))
                return html
        snippet = (html or "")[:200].replace("\n", " ")
        raise UpstreamForbiddenError(
            f"Нет контента / CF ({engine_name}, expect={expect}, "
            f"title={title!r}, html[:200]={snippet!r})"
        )

    async def fetch_html(self, url: str, expect: str = "any") -> str:
        engines = self._engines_order()
        if not engines:
            raise ParserError("Браузер не инициализирован")

        attempts = max(settings.browser_max_retries, 1)
        last_error: Exception | None = None

        async with self._semaphore:
            for engine_name, _browser in engines:
                for attempt in range(1, attempts + 1):
                    # cf_clearance привязан к IP — при живых cookies НЕ refresh-ip
                    has_cookies = self._storage_path.is_file()
                    reset = attempt > 1 and not has_cookies
                    if attempt > 1 and has_cookies:
                        logger.info(
                            "Повтор без смены IP (есть cookies / cf_clearance)"
                        )
                        await self._close_sticky_chromium(save=False)
                        await asyncio.sleep(settings.retry_backoff)
                    elif reset:
                        await self._close_sticky_chromium(save=False)
                        await self._refresh_ip()
                        await self._resolve_proxy_config(force_new_session=True)
                        await asyncio.sleep(settings.retry_backoff)

                    logger.info(
                        "Fetch [%s] попытка %d/%d -> %s (reset_session=%s, cookies=%s)",
                        engine_name,
                        attempt,
                        attempts,
                        url,
                        reset,
                        has_cookies,
                    )
                    try:
                        if engine_name == "chromium":
                            html = await self._fetch_chromium(
                                url, expect, reset_session=(attempt > 1)
                            )
                        else:
                            html = await self._fetch_camoufox(url, expect)

                        if self._html_ok(html, expect):
                            self.last_engine = engine_name
                            return html
                        raise UpstreamForbiddenError(
                            f"Страница без контента / CF ({engine_name}, expect={expect})"
                        )
                    except (UpstreamForbiddenError, ProxyError) as exc:
                        last_error = exc
                        logger.warning("%s", exc)
                    except Exception as exc:  # noqa: BLE001
                        last_error = ProxyError(
                            f"Ошибка навигации ({engine_name}): {exc}"
                        )
                        logger.warning("%s", last_error)

                logger.warning(
                    "Движок %s исчерпал попытки — следующий (если есть)",
                    engine_name,
                )
                await self._close_sticky_chromium()

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
