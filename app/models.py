"""Pydantic-схемы API in-poland parser."""

from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl


class CollectRequest(BaseModel):
    category_url: HttpUrl = Field(
        ...,
        description="Лента категории, напр. https://in-poland.com/category/novosti/",
    )
    max_pages: int | None = Field(
        default=None,
        ge=1,
        description="Сколько страниц ленты пройти (/page/N/)",
    )
    section_slug: str | None = Field(
        default=None,
        description="Slug раздела на dziendol (если не задан — выводится из URL)",
    )


class CollectAllRequest(BaseModel):
    max_pages: int | None = Field(default=None, ge=1)
    priority_slug: str = Field(
        default="novosti",
        description="Категория с приоритетом (собирается первой)",
    )


class ArticleLink(BaseModel):
    url: str
    title: str = ""
    date: str = ""
    excerpt: str = ""
    image: str = ""
    section_slug: str = "novosti"
    category_url: str = ""


class CollectResponse(BaseModel):
    category_url: str
    section_slug: str
    count: int
    pages_fetched: int = 0
    engine: str = ""
    articles: list[ArticleLink] = Field(default_factory=list)


class CollectAllResponse(BaseModel):
    count: int
    pages_fetched: int = 0
    articles: list[ArticleLink] = Field(default_factory=list)


class ParseRequest(BaseModel):
    url: HttpUrl = Field(..., description="Ссылка на статью in-poland.com")


class ArticleData(BaseModel):
    ok: bool = False
    url: str = ""
    title: str = ""
    text: str = ""
    date: str = ""


class ParseResponse(BaseModel):
    source_url: str
    engine: str = ""
    article: ArticleData
    html: str = Field(
        default="",
        description="Сырой HTML страницы (для POST в in-poland.php?import=1&kind=article)",
    )
