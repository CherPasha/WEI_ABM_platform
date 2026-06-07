"""Yandex Search API lookup + Playwright page scraping for the news stage.

Flow: query Yandex Search API per company name -> normalize/dedupe URLs ->
open unique pages with Playwright -> return rows ready for ``news_articles``.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from itertools import cycle
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from app.config import settings

logger = logging.getLogger(__name__)

YANDEX_SEARCH_URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"
REQUEST_TIMEOUT = 15  # seconds, Yandex API HTTP timeout

RESULTS_PER_COMPANY = 50
PAGE_SIZE = 10  # Yandex returns ~10 docs per page by default
MAX_PAGES = (RESULTS_PER_COMPANY + PAGE_SIZE - 1) // PAGE_SIZE
MAX_AGE = timedelta(days=365 * 5)  # best-effort freshness window
FULL_TEXT_MAX_CHARS = 50000  # cap scraped page text to keep DB rows sane

# Tracking query params dropped before deduplication.
_TRACKING_PARAMS = {"yclid", "gclid", "fbclid", "_openstat", "ref", "referrer"}
_TRACKING_PREFIXES = ("utm_",)


# --- Yandex Search API ------------------------------------------------------

def _request_search_page(query: str, page: int) -> str | None:
    """One Yandex Search API call. Returns decoded XML text or None.

    Retries on 429/503 with exponential backoff. Secrets are never logged.
    """
    payload = {
        "query": {
            "searchType": "SEARCH_TYPE_RU",
            "queryText": query,
            "page": page,
        },
        "folderId": settings.yandex_folder_id,  # see docs/news_pipeline.md
        "responseFormat": "FORMAT_XML",
    }
    headers = {"Authorization": f"Api-Key {settings.YANDEX_SEARCH_API_KEY}"}

    backoff = 1.0
    for attempt in range(4):
        try:
            response = requests.post(
                YANDEX_SEARCH_URL, json=payload, headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.warning("Yandex Search request failed for %r (page %s): %s", query, page, exc)
            return None

        if response.status_code == 200:
            raw = (response.json() or {}).get("rawData")
            if not raw:
                logger.warning("Yandex Search returned empty rawData for %r (page %s)", query, page)
                return None
            try:
                return base64.b64decode(raw).decode("utf-8", errors="replace")
            except (ValueError, TypeError) as exc:
                logger.warning("Failed to decode Yandex rawData for %r: %s", query, exc)
                return None

        if response.status_code in (429, 503):
            logger.info("Yandex Search throttled (%s), retry in %.1fs", response.status_code, backoff)
            time.sleep(backoff)
            backoff *= 2
            continue

        if response.status_code in (401, 403):
            logger.error(
                "Yandex Search API auth failed (HTTP %s). "
                "Check YANDEX_SEARCH_API_KEY and YANDEX_FOLDER_ID. "
                "YANDEX_FOLDER_ID must be the Cloud folder ID (starts with b1g...), "
                "NOT the API key ID (starts with aje...). "
                "Current folder_id starts with: %r",
                response.status_code,
                settings.yandex_folder_id[:6] if settings.yandex_folder_id else "EMPTY",
            )
        else:
            logger.warning("Yandex Search returned status %s for %r", response.status_code, query)
        return None

    logger.warning("Yandex Search gave up after retries for %r (page %s)", query, page)
    return None


def _text(element: ET.Element | None) -> str | None:
    """Flatten an XML node to plain text, dropping <hlword> highlight tags."""
    if element is None:
        return None
    text = "".join(element.itertext()).strip()
    return text or None


def _parse_docs(xml_text: str) -> list[dict[str, Any]]:
    """Parse Yandex XML response into raw doc dicts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("Failed to parse Yandex XML: %s", exc)
        return []

    docs: list[dict[str, Any]] = []
    for doc in root.iter("doc"):
        url = _text(doc.find("url"))
        if not url:
            continue
        passages = [t for t in (_text(p) for p in doc.iter("passage")) if t]
        docs.append({
            "url": url,
            "domain": _text(doc.find("domain")),
            "title": _text(doc.find("title")),
            "headline": _text(doc.find("headline")),
            "passage": " ".join(passages) or None,
            "modtime": _text(doc.find("modtime")),
        })
    return docs


def search_yandex(query: str) -> list[dict[str, Any]]:
    """Query Yandex Search API and return up to RESULTS_PER_COMPANY raw docs."""
    results: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        xml_text = _request_search_page(query, page)
        if not xml_text:
            break
        docs = _parse_docs(xml_text)
        if not docs:
            break
        results.extend(docs)
        if len(results) >= RESULTS_PER_COMPANY:
            break
        time.sleep(0.3)  # be polite between pages
    return results[:RESULTS_PER_COMPANY]


def extract_search_result(item: dict[str, Any], search_term: str) -> dict[str, Any]:
    """Map a raw Yandex doc to the intermediate search-result format."""
    return {
        "url": item.get("url"),
        "title": item.get("title"),
        "snippet": item.get("passage") or item.get("headline"),
        "search_term": search_term,
        "raw_data": item,
    }


# --- URL normalization & dedup ----------------------------------------------

def _normalize_url(url: str) -> str:
    """Normalize a URL for dedup: lowercase host, drop tracking params/fragment."""
    parts = urlsplit(url.strip())
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
        and not k.lower().startswith(_TRACKING_PREFIXES)
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((
        parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), "",
    ))


def _dedupe_by_url(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the first result per normalized URL."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for result in results:
        url = result.get("url")
        if not url:
            continue
        key = _normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(result)
    return unique


# --- Playwright scraping ----------------------------------------------------

def _playwright_proxy(proxy: str | None) -> dict[str, str] | None:
    """Turn a proxy URL into Playwright's proxy config (with optional creds)."""
    if not proxy:
        return None
    parts = urlsplit(proxy)
    host = parts.hostname or ""
    if not host:
        return None
    server = f"{parts.scheme or 'http'}://{host}"
    if parts.port:
        server += f":{parts.port}"
    config: dict[str, str] = {"server": server}
    if parts.username:
        config["username"] = parts.username
    if parts.password:
        config["password"] = parts.password
    return config


async def _block_heavy(route) -> None:
    """Abort image/font/media requests to save proxy traffic."""
    if route.request.resource_type in ("image", "font", "media"):
        await route.abort()
    else:
        await route.continue_()


def _classify_error(exc: Exception) -> str:
    """Map a Playwright exception to a short skip reason for telemetry."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timeout" in msg:
        return "timeout"
    if "proxy" in msg or "tunnel" in msg or "err_proxy" in msg:
        return "bad_proxy"
    if "err_name_not_resolved" in msg or "dns" in msg or "addr" in msg:
        return "dns_error"
    if "err_connection" in msg or "refused" in msg or "reset" in msg:
        return "connection"
    return "error"


async def _extract_page_data(page, url: str) -> tuple[dict[str, Any], str]:
    """Open a page and pull whatever metadata is available.

    Returns (data, status). status is "ok", "no_content", "blocked" (403/429/
    captcha-like) or "http_<code>". Raises on network/timeout errors so the
    caller can classify them.
    """
    response = await page.goto(
        url, wait_until="domcontentloaded", timeout=settings.PLAYWRIGHT_TIMEOUT_MS,
    )

    code = response.status if response else 0
    if code in (401, 403, 407, 429, 451, 503):
        return {}, "blocked"  # access denied / rate limited / captcha-like wall
    if code >= 400:
        return {}, f"http_{code}"

    data = await page.evaluate(
        """() => {
            const meta = (sel) => {
                const el = document.querySelector(sel);
                return el ? el.getAttribute('content') : null;
            };
            const firstP = document.querySelector('article p, main p, p');
            // Main content container for full text; fall back through article -> main -> body.
            const body = document.querySelector('article')
                || document.querySelector('main')
                || document.body;
            return {
                title: meta('meta[property="og:title"]') || document.title || null,
                source_name: meta('meta[property="og:site_name"]'),
                snippet: meta('meta[name="description"]')
                    || meta('meta[property="og:description"]')
                    || (firstP ? firstP.innerText : null),
                published_at: meta('meta[property="article:published_time"]')
                    || meta('meta[itemprop="datePublished"]')
                    || (document.querySelector('time[datetime]')
                        ? document.querySelector('time[datetime]').getAttribute('datetime')
                        : null),
                full_text: body ? body.innerText : null,
            };
        }"""
    )

    snippet = (data.get("snippet") or "").strip() or None
    if snippet and len(snippet) > 500:
        snippet = snippet[:500].rstrip() + "…"

    # Collapse whitespace and cap length to keep DB rows sane.
    full_text = " ".join((data.get("full_text") or "").split()) or None
    if full_text and len(full_text) > FULL_TEXT_MAX_CHARS:
        full_text = full_text[:FULL_TEXT_MAX_CHARS].rstrip() + "…"

    title = (data.get("title") or "").strip() or None
    page_data = {
        "title": title,
        "source_name": (data.get("source_name") or "").strip() or urlsplit(page.url).hostname,
        "snippet": snippet,
        "published_at": (data.get("published_at") or "").strip() or None,
        "full_text": full_text,
        "final_url": page.url,
    }
    status = "ok" if (title or snippet or full_text) else "no_content"
    return page_data, status


async def scrape_article(url: str, proxy: str | None = None) -> dict[str, Any]:
    """Scrape a single page in its own browser. Returns {} on any failure."""
    from playwright.async_api import async_playwright

    proxy_config = _playwright_proxy(proxy)
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True, proxy=proxy_config,
            )
            try:
                page = await browser.new_page()
                await page.route("**/*", _block_heavy)
                data, _ = await _extract_page_data(page, url)
                return data
            finally:
                await browser.close()
    except Exception as exc:  # noqa: BLE001 - external pages fail in many ways
        logger.warning("Failed to scrape %s: %s", url, exc)
        return {}


async def _scrape_one(browser, semaphore, url: str, proxy: str | None) -> tuple[str, dict[str, Any], str]:
    """Scrape one URL in a fresh (proxied) context. Returns (url, data, status)."""
    proxy_config = _playwright_proxy(proxy)
    async with semaphore:
        context = None
        try:
            context = await browser.new_context(proxy=proxy_config)
            page = await context.new_page()
            await page.route("**/*", _block_heavy)
            data, status = await _extract_page_data(page, url)
            if status not in ("ok", "no_content"):
                logger.info("Skipped %s: %s", url, status)
            return url, data, status
        except Exception as exc:  # noqa: BLE001 - external pages fail in many ways
            reason = _classify_error(exc)
            logger.warning("Failed to scrape %s (%s): %s", url, reason, exc)
            return url, {}, reason
        finally:
            if context is not None:
                await context.close()


async def _scrape_all(urls: list[str]) -> tuple[dict[str, dict[str, Any]], Counter]:
    """Scrape URLs concurrently. Returns ({url: data}, stats Counter by status)."""
    from playwright.async_api import async_playwright

    proxies = settings.proxy_list
    proxy_cycle = cycle(proxies) if proxies else None
    semaphore = asyncio.Semaphore(max(1, settings.PLAYWRIGHT_CONCURRENCY))

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            tasks = [
                _scrape_one(browser, semaphore, url, next(proxy_cycle) if proxy_cycle else None)
                for url in urls
            ]
            results = await asyncio.gather(*tasks)
        finally:
            await browser.close()

    data = {url: page for url, page, _ in results}
    stats = Counter(status for _, _, status in results)
    return data, stats


def _run_async(coro):
    """Run a coroutine even if called from within a running event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # Already inside a loop (e.g. async caller): run in a separate thread.
    box: dict[str, Any] = {}
    thread = threading.Thread(target=lambda: box.update(result=asyncio.run(coro)))
    thread.start()
    thread.join()
    return box.get("result")


# --- Date helpers -----------------------------------------------------------

def _parse_date(value: str | None) -> datetime | None:
    """Best-effort parse of ISO / Yandex modtime dates."""
    if not value:
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return datetime.strptime(text, "%Y%m%dT%H%M%S")  # Yandex modtime
    except ValueError:
        return None


def _is_too_old(published: datetime | None) -> bool:
    """True only when we have a date and it is clearly outside the window."""
    if published is None:
        return False
    now = datetime.now(timezone.utc)
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return now - published > MAX_AGE


# --- Orchestrator -----------------------------------------------------------

def find_all_news_for_company(known_names: list[str]) -> list[dict[str, Any]]:
    """Public entrypoint for session_processor: search + scrape -> news rows."""
    if not settings.has_yandex:
        logger.warning("Yandex Search API not configured, skipping news stage")
        return []

    # 1-2. Search every known name.
    search_results: list[dict[str, Any]] = []
    for name in known_names:
        if not name:
            continue
        logger.info("Yandex Search for %r", name)
        for item in search_yandex(name):
            search_results.append(extract_search_result(item, name))

    # 3. Dedupe before opening any page.
    unique = _dedupe_by_url(search_results)
    if not unique:
        logger.info("News stats: search=%s unique=0", len(search_results))
        return []

    # 4. Scrape in one Playwright session.
    # If Playwright is unavailable or crashes, fall back to search-result data only
    # (title + snippet from Yandex; full_text stays None).
    urls = [r["url"] for r in unique]
    try:
        scraped, stats = _run_async(_scrape_all(urls)) or ({}, Counter())
        ok = stats.get("ok", 0) + stats.get("no_content", 0)
        skipped = {k: v for k, v in stats.items() if k not in ("ok", "no_content")}
        logger.info(
            "News stats: search=%s unique=%s scraped_ok=%s skipped=%s reasons=%s",
            len(search_results), len(unique), ok, sum(skipped.values()), dict(skipped) or "{}",
        )
    except Exception as exc:
        logger.warning(
            "Playwright scraping failed (%s) — saving %s search results without full text",
            exc, len(unique),
        )
        scraped = {}

    # 5-6. Merge, apply best-effort freshness filter, build DB rows.
    rows: list[dict[str, Any]] = []
    for result in unique:
        page = scraped.get(result["url"], {})
        published = _parse_date(page.get("published_at")) or _parse_date(result["raw_data"].get("modtime"))
        if _is_too_old(published):
            continue
        rows.append({
            "article_url": page.get("final_url") or result["url"],
            "search_term": result["search_term"],
            "title": page.get("title") or result.get("title"),
            "source_name": page.get("source_name"),
            "snippet": page.get("snippet") or result.get("snippet"),
            "full_text": page.get("full_text"),
            "published_at": published.isoformat() if published else None,
            "raw_data": {"search": result["raw_data"], "page": page},
        })
    return rows
