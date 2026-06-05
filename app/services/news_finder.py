"""Backward-compatible shim.

The news stage moved to ``yandex_search`` (Yandex Search API + Playwright).
This module is kept so existing imports of ``find_all_news_for_company`` keep
working. Import from ``app.services.yandex_search`` directly in new code.
"""
from app.services.yandex_search import (  # noqa: F401
    extract_search_result,
    find_all_news_for_company,
    scrape_article,
    search_yandex,
)
