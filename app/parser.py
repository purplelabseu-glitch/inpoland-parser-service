"""Парсинг лент и статей in-poland.com (BeautifulSoup)."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

BASE = "https://in-poland.com"

CATEGORY_PATH_TO_SLUG: dict[str, str] = {
    "/category/novosti/": "novosti",
    "/category/legalnoe-prebyvanie/": "karta-pobytu",
    "/category/rabota-v-polshe/": "rabota",
    "/category/posobija/": "posobija",
    "/category/nedvizhimost/": "nedvizhimost",
    "/category/uchim-polski/": "polskij-yazyk",
    "/category/puteshestvija-i-otdyh/": "puteshestvija",
    "/category/medicina/": "medicina",
    "/category/informacia-dla-roditeley/": "dlya-roditeley",
    "/category/obrazovanie/": "obrazovanie",
    "/category/pokupki/": "pokupki",
    "/category/transport/": "transport",
}

DEFAULT_CATEGORIES: list[dict[str, str]] = [
    {"category_url": f"{BASE}{path}", "section_slug": slug}
    for path, slug in CATEGORY_PATH_TO_SLUG.items()
]


def category_slug_from_url(url: str) -> str:
    path = (urlparse(url).path or "").lower()
    if not path.endswith("/"):
        path += "/"
    for needle, slug in CATEGORY_PATH_TO_SLUG.items():
        if needle in path:
            return slug
    return "novosti"


def listing_page_url(category_url: str, page: int) -> str:
    base = category_url.rstrip("/") + "/"
    if page <= 1:
        return base
    return f"{base}page/{page}/"


def looks_like_cloudflare(html: str) -> bool:
    low = html.lower()
    markers = (
        "just a moment",
        "cf-browser-verification",
        "challenge-platform",
        "cdn-cgi/challenge",
        "attention required! | cloudflare",
        "checking your browser",
    )
    return any(m in low for m in markers)


def is_listing_html(html: str) -> bool:
    return "post-preview" in html


def is_article_html(html: str) -> bool:
    return "flex-block" in html or "<figcaption" in html.lower()


def extract_listing_items(html: str, base_url: str = BASE) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, str]] = []
    seen: set[str] = set()

    for preview in soup.select(".post-preview"):
        link = preview.select_one("h2 a[href]") or preview.select_one("a.post[href]") or preview.select_one("a[href]")
        if not link:
            continue
        href = (link.get("href") or "").strip()
        if not href:
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        title = (link.get_text(" ", strip=True) or link.get("title") or "").strip()
        if not title:
            continue
        date_el = preview.select_one(".date")
        date = date_el.get_text(" ", strip=True) if date_el else ""
        excerpt_el = preview.select_one(".description")
        excerpt = excerpt_el.get_text(" ", strip=True) if excerpt_el else ""
        img = preview.select_one("img")
        image = ""
        if img:
            image = (
                img.get("data-src")
                or img.get("data-lazy-src")
                or img.get("src")
                or ""
            ).strip()
            if image.startswith("/"):
                image = urljoin(base_url, image)
        seen.add(url)
        items.append(
            {
                "url": url,
                "title": title,
                "date": date,
                "excerpt": excerpt,
                "image": image,
            }
        )
    return items


def extract_article(html: str, source_url: str = "", base_url: str = BASE) -> dict:
    soup = BeautifulSoup(html, "lxml")

    title = ""
    fig = soup.select_one("figure figcaption")
    if fig:
        title = fig.get_text(" ", strip=True)
    if not title:
        h1 = soup.select_one("h1")
        if h1:
            title = h1.get_text(" ", strip=True)

    date_raw = ""
    date_el = soup.select_one(".date")
    if date_el:
        date_raw = (date_el.get_text(" ", strip=True) or date_el.get("title") or "").strip()
    date_ymd = ""
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", date_raw)
    if m:
        date_ymd = f"{m.group(3)}-{m.group(2)}-{m.group(1)} 00:00:00"

    texts: list[str] = []
    for block in soup.select(".flex-block"):
        for child in block.find_all(recursive=False):
            if not getattr(child, "name", None):
                continue
            cls = " " + " ".join(child.get("class") or []).lower() + " "
            if " ad-wrapper " in cls or " telegram-btn " in cls:
                continue
            if " share-social " in cls:
                break
            tag = child.name.lower()
            if tag in ("ul", "ol"):
                for li in child.find_all("li"):
                    t = li.get_text(" ", strip=True)
                    if t:
                        texts.append(t)
            elif tag in ("p", "h2", "h3"):
                t = child.get_text(" ", strip=True)
                if t:
                    texts.append(t)

    if not texts:
        for p in soup.select(".flex-block p"):
            t = p.get_text(" ", strip=True)
            if t:
                texts.append(t)

    text = "\n\n".join(texts).strip()
    return {
        "ok": bool(title and text),
        "url": source_url or base_url,
        "title": title,
        "text": text,
        "date": date_ymd or date_raw,
    }
