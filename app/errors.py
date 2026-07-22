"""Доменные исключения. Все они мапятся на HTTP-ответы в main.py."""

from __future__ import annotations


class ParserError(Exception):
    """Базовая ошибка сервиса."""

    status_code: int = 502
    detail: str = "Ошибка парсера"

    def __init__(self, detail: str | None = None) -> None:
        if detail:
            self.detail = detail
        super().__init__(self.detail)


class UpstreamForbiddenError(ParserError):
    """Сайт вернул 403 (Akamai заблокировал запрос)."""

    status_code = 403
    detail = "Целевой сайт вернул 403 (заблокирован Akamai / прокси спалился)"


class UpstreamNotFoundError(ParserError):
    """Сайт вернул 404 (объявление снято/неверная ссылка)."""

    status_code = 404
    detail = "Объявление не найдено (404)"


class ProxyError(ParserError):
    """Прокси недоступен или соединение оборвалось."""

    status_code = 502
    detail = "Прокси недоступен или сетевая ошибка при обращении к сайту"


class ParsingFailedError(ParserError):
    """Страница скачалась, но распарсить её не удалось."""

    status_code = 422
    detail = "Не удалось извлечь данные со страницы (изменилась вёрстка?)"


class ServicePausedError(ParserError):
    """Слишком много CF/403 подряд — сервис остановил запросы к сайту."""

    status_code = 503
    detail = (
        "Парсер на паузе после серии CF/403. Обновите cookies (bootstrap) "
        "и вызовите POST /api/v1/circuit/reset"
    )
