"""Конфигурация микросервиса in-poland.com. Значения из .env / окружения."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Прокси (как у mobilede-parser-service) ---------------------------
    proxy_url: str | None = Field(default=None, alias="PROXY_URL")
    proxy_pool_raw: str | None = Field(default=None, alias="PROXY_POOL")
    proxy_refresh_url: str | None = Field(default=None, alias="PROXY_REFRESH_URL")
    refresh_wait_s: float = Field(default=4.0, alias="REFRESH_WAIT_S")
    # Фиксированная подстановка {session} в PROXY_URL (как PROXY_SESSION в bootstrap_cf.mjs)
    proxy_session: str | None = Field(default=None, alias="PROXY_SESSION")

    max_retries: int = Field(default=3, alias="MAX_RETRIES")
    retry_backoff: float = Field(default=1.5, alias="RETRY_BACKOFF")
    request_timeout: int = Field(default=30, alias="REQUEST_TIMEOUT")

    # --- Браузер ---------------------------------------------------------
    # in-poland.com: Cloudflare часто режет Camoufox. По умолчанию auto =
    # сначала Chromium, потом Camoufox. Можно: chromium | camoufox | auto
    browser_engine: str = Field(default="auto", alias="BROWSER_ENGINE")
    browser_headless: bool = Field(default=True, alias="BROWSER_HEADLESS")
    nav_timeout_ms: int = Field(default=90000, alias="NAV_TIMEOUT_MS")
    challenge_wait_s: int = Field(default=60, alias="CHALLENGE_WAIT_S")
    browser_concurrency: int = Field(default=1, alias="BROWSER_CONCURRENCY")
    browser_max_retries: int = Field(default=3, alias="BROWSER_MAX_RETRIES")
    browser_locale: str = Field(default="ru-RU", alias="BROWSER_LOCALE")
    # Сколько секунд ждать появления .post-preview / .flex-block после goto
    content_wait_s: int = Field(default=45, alias="CONTENT_WAIT_S")

    # Cookies / Cloudflare: storage_state Playwright (cf_clearance и др.)
    # Путь относительно cwd сервиса или абсолютный.
    storage_state_path: Path = Field(
        default=Path(".cache/inpoland-storage.json"),
        alias="STORAGE_STATE_PATH",
    )
    # Держать один Chromium-контекст между запросами (куки + sticky IP)
    sticky_chromium: bool = Field(default=True, alias="STICKY_CHROMIUM")
    # Не крутить {session}/refresh-ip на каждой попытке, пока есть сохранённые куки
    sticky_proxy: bool = Field(default=True, alias="STICKY_PROXY")

    # После N подряд проваленных fetch (каждый с BROWSER_MAX_RETRIES + смена IP)
    # — стоп, чтобы не долбить in-poland. Сброс: новые cookies или /api/v1/circuit/reset
    circuit_fail_limit: int = Field(default=3, alias="CIRCUIT_FAIL_LIMIT")
    circuit_enabled: bool = Field(default=True, alias="CIRCUIT_ENABLED")

    # --- Сбор ссылок -----------------------------------------------------
    collect_max_pages_default: int = Field(default=10, alias="COLLECT_MAX_PAGES_DEFAULT")
    collect_max_pages_limit: int = Field(default=20, alias="COLLECT_MAX_PAGES_LIMIT")

    # --- Доступ ----------------------------------------------------------
    api_key: str | None = Field(default=None, alias="API_KEY")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def proxy_pool(self) -> list[str]:
        pool: list[str] = []
        if self.proxy_url:
            pool.append(self.proxy_url.strip())
        if self.proxy_pool_raw:
            for chunk in re.split(r"[,\n]", self.proxy_pool_raw):
                proxy = chunk.strip()
                if proxy:
                    pool.append(proxy)
        seen: set[str] = set()
        result: list[str] = []
        for proxy in pool:
            if proxy not in seen:
                seen.add(proxy)
                result.append(proxy)
        return result


settings = Settings()
