# inpoland-parser-service

Отдельный микросервис парсинга [in-poland.com](https://in-poland.com/) по схеме [mobilede-parser-service](../mobilede-parser-service): FastAPI + прокси + браузер.

**Не связан с mobile.de** — свой порт (`8001`), свой процесс, свой код.

## Схема

```
cron на dziendol.pl
  → collect_inpoland.php / import_inpoland.php  (vestnik/public/)
  → http://VPS:8001/api/v1/collect|parse
  → in-poland.php (prepare → rewrite → commit → MySQL)
```

## API

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/health` | статус + прокси + `circuit` + cookies |
| POST | `/api/v1/collect` | лента одной категории (`max_pages`, по умолчанию 10) |
| POST | `/api/v1/collect_all` | все категории, `novosti` первой |
| POST | `/api/v1/parse` | HTML + title/text/date статьи |
| POST | `/api/v1/circuit/reset` | снять автопаузу после новых cookies |

Заголовок: `X-API-Key: <API_KEY>` (если задан в `.env`).

Swagger: `/docs` на хосте сервиса (например `http://<VPS>:8001/docs`).

## Если сломались Cloudflare cookies

1. **Рабочая машина** (VPN off), в каталоге репо, с `PROXY_URL` в `.env`:
   ```bash
   node bootstrap_cf.mjs
   ```
   Пройти CF в Firefox → Enter → файл `.cache/inpoland-storage.json`.

2. **Залить на VPS:**
   ```bash
   scp .cache/inpoland-storage.json USER@VPS_HOST:~/inpoland-parser-service/.cache/inpoland-storage.json
   ```

3. **На VPS** — логи: `sudo journalctl -u inpoland-parser -f`  
   Затем:
   ```bash
   cd ~/inpoland-parser-service
   sudo systemctl restart inpoland-parser
   curl -sS http://127.0.0.1:8001/health   # cookies_present: true
   API_KEY=$(grep -E '^API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '\r')
   curl -sS -X POST http://127.0.0.1:8001/api/v1/circuit/reset -H "X-API-Key: $API_KEY"
   ```

4. Проверка import/cron на сайте.

Пока cookies живы — не меняйте `session` в `PROXY_URL`. На Windows и VPS
задайте одинаковый `PROXY_SESSION=inpoland1`.

## Автообновление cookies (Task Scheduler)

1. Заполнить `scripts/refresh_cookies.ps1` (плейсхолдеры `USER` / `VPS_HOST` / путь).
2. SSH-ключ без пароля на VPS.
3. Раз в 2 часа:
   ```text
   powershell.exe -ExecutionPolicy Bypass -File "...\scripts\refresh_cookies.ps1"
   ```
4. Ручной тест: `node bootstrap_cf.mjs --auto`

## Браузер

1. **Camoufox** (как mobile.de)
2. Если Cloudflare / пустая страница → **Playwright Chromium**
3. Headless, без CDP

## Деплой на VPS (рядом с mobilede)

На том же сервере, что `http://31.130.203.134:8000`:

```bash
# скопировать проект, например:
# /opt/inpoland-parser  или рядом с mobilede

cd /path/to/inpoland-parser-service
# скопировать PROXY_URL / PROXY_REFRESH_URL из mobilede .env
cp .env.example .env
nano .env   # PROXY_*, API_KEY

sudo bash build.sh
# сервис: inpoland-parser на :8001
curl http://127.0.0.1:8001/health
```

Docs: `http://31.130.203.134:8001/docs`

## Локально (Windows)

```powershell
cd D:\work\git\inpoland-parser-service
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
python -m camoufox fetch
copy .env.example .env
# прописать PROXY_URL и API_KEY
uvicorn app.main:app --host 0.0.0.0 --port 8001
```
