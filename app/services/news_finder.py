import time
import logging
from urllib.parse import urlparse

import requests
from lxml import etree

logger = logging.getLogger(__name__)

YANDEX_NEWS_RSS_URL = "https://news.yandex.ru/search.rss"
WITHIN_DAYS = 90  # look back this many days
MAX_RETRIES = 3


def find_news_by_search_term(search_term: str) -> list[dict]:
    """Fetch Yandex News RSS for a search term. Returns raw parsed items."""
    params = {
        "text": search_term,
        "within": WITHIN_DAYS,
    }

    retries = MAX_RETRIES
    while retries > 0:
        try:
            response = requests.get(
                YANDEX_NEWS_RSS_URL,
                params=params,
                timeout=30,
                headers={"User-Agent": "Mozilla/5.0"},
            )

            if response.status_code == 200:
                return _parse_rss(response.content)

            elif response.status_code == 429:
                retries -= 1
                wait = 30 * (MAX_RETRIES - retries)
                logger.warning(
                    "Yandex News rate limited for '%s'. Waiting %ds (%d retries left).",
                    search_term, wait, retries,
                )
                time.sleep(wait)

            else:
                logger.warning(
                    "Yandex News returned status %d for '%s'.",
                    response.status_code, search_term,
                )
                break

        except Exception as e:
            logger.error("Error fetching news for '%s': %s", search_term, e)
            break

    return []


def _parse_rss(content: bytes) -> list[dict]:
    """Parse Yandex News RSS and extract article fields."""
    try:
        root = etree.fromstring(content)
    except Exception as e:
        logger.error("Failed to parse Yandex News RSS: %s", e)
        return []

    items = []
    for item in root.findall(".//item"):
        url = _text(item, "link")
        if not url:
            continue
        source_el = item.find("source")
        source_name = source_el.text.strip() if source_el is not None and source_el.text else urlparse(url).netloc
        items.append({
            "url": url,
            "title": _text(item, "title"),
            "headline": _text(item, "description"),
            "domain": source_name,
            "pubdate": _text(item, "pubDate"),
        })

    return items


def _text(element, tag: str) -> str:
    node = element.find(tag)
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def extract_news_fields(item: dict, search_term: str) -> dict:
    """Map raw Yandex result fields to database columns."""
    return {
        "article_url": item.get("url", ""),
        "search_term": search_term,
        "title": item.get("title", ""),
        "source_name": item.get("domain", ""),
        "snippet": item.get("headline", ""),
        "published_at": item.get("pubdate", ""),
        "raw_data": item,
    }


def find_all_news_for_company(known_names: list[str]) -> list[dict]:
    """Search Yandex for all known names of a company, deduplicate by article URL."""
    seen_urls: set[str] = set()
    all_articles = []

    for name in known_names:
        if not name or name == "Название не доступно":
            continue
        raw_items = find_news_by_search_term(name)
        for item in raw_items:
            url = item.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_articles.append(extract_news_fields(item, name))

    return all_articles
