"""FastAPI: парсер in-poland.com (отдельный порт от mobile.de)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse

from .browser import browser_manager
from .config import settings
from .errors import ParserError
from .models import (
    ArticleData,
    ArticleLink,
    CollectAllRequest,
    CollectAllResponse,
    CollectRequest,
    CollectResponse,
    ParseRequest,
    ParseResponse,
)
from .parser import (
    DEFAULT_CATEGORIES,
    category_slug_from_url,
)

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("inpoland_parser")

API_DESCRIPTION = """
Микросервис сбора новостей [in-poland.com](https://in-poland.com/) через прокси
(Camoufox / Chromium). Не связан с mobile.de (тот на `:8000`).

Заголовок API: `X-API-Key` (значение из `.env` → `API_KEY`).

---

## Если сломались Cloudflare cookies

Признаки: в логах `Cloudflare/challenge`, `403`, `Processing...`,
`health.status` = `paused` / `circuit.open` = true.

### 1. Рабочая машина (VPN выключен)

В каталоге клона репозитория `inpoland-parser-service`
(нужны `PROXY_URL` в `.env` — тот же Smartproxy, что на VPS):

```bash
node bootstrap_cf.mjs
```

Откроется Firefox через прокси → пройдите Cloudflare вручную.
Когда видна лента новостей — нажмите **Enter** в терминале.
Файл: `.cache/inpoland-storage.json`.

### 2. Залить cookies на VPS

```bash
scp .cache/inpoland-storage.json USER@VPS_HOST:~/inpoland-parser-service/.cache/inpoland-storage.json
```

(`USER` / `VPS_HOST` — ваши; путь к сервису на сервере может отличаться.)

### 3. На VPS

Окно логов:

```bash
sudo journalctl -u inpoland-parser -f
```

В другом терминале:

```bash
cd ~/inpoland-parser-service   # или ваш путь к сервису
sudo systemctl restart inpoland-parser
curl -sS http://127.0.0.1:8001/health
# нужно: "cookies_present": true

API_KEY=$(grep -E '^API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '\r')
curl -sS -X POST http://127.0.0.1:8001/api/v1/circuit/reset -H "X-API-Key: $API_KEY"
```

### 4. Проверка

Cron/import на сайте снова пойдут сами, либо вручную
`import_inpoland.php` на dziendol.

Пока эти cookies живы — **не меняйте** `session-...` в `PROXY_URL`
(`cf_clearance` привязан к IP прокси). Задайте одинаковый `PROXY_SESSION`
на Windows и VPS.

---

## Автообновление cookies (Windows Task Scheduler)

В репозитории:

```bash
node bootstrap_cf.mjs --auto
```

Полный цикл (bootstrap → scp → restart → circuit reset):

1. Заполните плейсхолдеры в `scripts/refresh_cookies.ps1`
2. SSH-ключ на VPS (`BatchMode`)
3. В `.env` (Windows + VPS): `PROXY_SESSION=inpoland1`
4. Task Scheduler каждые 2 часа:
   - Program: `powershell.exe`
   - Arguments: `-ExecutionPolicy Bypass -File "C:\\path\\to\\inpoland-parser-service\\scripts\\refresh_cookies.ps1"`
   - «Только для вошедшего пользователя», если браузер не headless

Логи джобы: `logs/refresh_cookies_*.log` в каталоге репо.
Если лента не открылась — scp **не** выполняется (старые cookies на VPS не затираются).

---

## Circuit (автопауза)

После `CIRCUIT_FAIL_LIMIT` подряд проваленных fetch сервис
перестаёт ходить на сайт (`503`, `circuit.open`).
Сброс: новые cookies (см. выше) и/или `POST /api/v1/circuit/reset`.
"""


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Старт in-poland parser: инициализирую браузер...")
    if not settings.api_key:
        logger.warning(
            "API_KEY не задан — эндпоинты ОТКРЫТЫ без авторизации! "
            "Задайте API_KEY в .env."
        )
    await browser_manager.start()
    try:
        yield
    finally:
        await browser_manager.stop()
        logger.info("Сервис остановлен")


app = FastAPI(
    title="in-poland.com parser",
    version="1.0.0",
    description=API_DESCRIPTION,
    lifespan=lifespan,
)


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(
            status_code=401,
            detail="Неверный или отсутствующий API-ключ (заголовок X-API-Key)",
        )


@app.exception_handler(ParserError)
async def parser_error_handler(_, exc: ParserError) -> JSONResponse:
    logger.warning("ParserError -> %s: %s", exc.status_code, exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
async def health() -> dict:
    try:
        proxy = await browser_manager.check_proxy()
    except Exception as exc:  # noqa: BLE001
        proxy = {"configured": False, "ok": False, "detail": str(exc)}
    circuit = browser_manager.circuit_status()
    if circuit.get("open"):
        status = "paused"
    elif proxy.get("ok"):
        status = "ok"
    else:
        status = "degraded"
    return {
        "status": status,
        "service": "inpoland-parser",
        "proxy": proxy,
        "browser_engine": settings.browser_engine,
        "circuit": circuit,
    }


@app.post("/api/v1/circuit/reset", dependencies=[Depends(require_api_key)])
async def circuit_reset() -> dict:
    """Снять автопаузу (circuit) после заливки новых cookies.

    См. блок «Если сломались Cloudflare cookies» в описании API (/docs).
    """
    return {"ok": True, "circuit": browser_manager.reset_circuit("api reset")}


def _clamp_pages(requested: int | None) -> int:
    pages = requested or settings.collect_max_pages_default
    return max(1, min(pages, settings.collect_max_pages_limit))


@app.post(
    "/api/v1/collect",
    response_model=CollectResponse,
    dependencies=[Depends(require_api_key)],
)
async def collect(request: CollectRequest) -> CollectResponse:
    category_url = str(request.category_url).rstrip("/") + "/"
    slug = (request.section_slug or "").strip() or category_slug_from_url(category_url)
    max_pages = _clamp_pages(request.max_pages)
    logger.info("Collect %s (%s), pages=%d", category_url, slug, max_pages)

    items, pages_fetched = await browser_manager.collect_category(
        category_url, max_pages, slug
    )
    articles = [ArticleLink(**it) for it in items]
    return CollectResponse(
        category_url=category_url,
        section_slug=slug,
        count=len(articles),
        pages_fetched=pages_fetched,
        engine=browser_manager.last_engine,
        articles=articles,
    )


@app.post(
    "/api/v1/collect_all",
    response_model=CollectAllResponse,
    dependencies=[Depends(require_api_key)],
)
async def collect_all(request: CollectAllRequest) -> CollectAllResponse:
    """Все категории; priority_slug (novosti) первым."""
    max_pages = _clamp_pages(request.max_pages)
    priority = (request.priority_slug or "novosti").lower()

    cats = list(DEFAULT_CATEGORIES)
    cats.sort(key=lambda c: 0 if c["section_slug"] == priority else 1)

    all_articles: list[ArticleLink] = []
    seen: set[str] = set()
    pages_total = 0

    for cat in cats:
        items, pages_fetched = await browser_manager.collect_category(
            cat["category_url"], max_pages, cat["section_slug"]
        )
        pages_total += pages_fetched
        for it in items:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            all_articles.append(ArticleLink(**it))

    return CollectAllResponse(
        count=len(all_articles),
        pages_fetched=pages_total,
        articles=all_articles,
    )


@app.post(
    "/api/v1/parse",
    response_model=ParseResponse,
    dependencies=[Depends(require_api_key)],
)
async def parse(request: ParseRequest) -> ParseResponse:
    url = str(request.url)
    logger.info("Parse article: %s", url)
    article, html = await browser_manager.parse_article(url)
    return ParseResponse(
        source_url=url,
        engine=browser_manager.last_engine,
        article=ArticleData(**article),
        html=html,
    )
