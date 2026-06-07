import time
import logging
import requests
from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hh.ru/vacancies"
MAX_PAGES = 20  # Cap at 2000 postings per search term


def get_app_token() -> str:
    """Obtain a client_credentials OAuth token from HH.ru."""
    response = requests.post(
        "https://api.hh.ru/token",
        data={
            "grant_type": "client_credentials",
            "client_id": settings.HH_CLIENT_ID,
            "client_secret": settings.HH_CLIENT_SECRET,
        },
        headers={
            "User-Agent": settings.HH_USER_AGENT,
            "HH-User-Agent": settings.HH_USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"HH did not return access_token: {payload}")
    return token


def find_postings_by_search_term(search_term: str) -> list[dict]:
    """Search hh.ru for job postings matching a search term. Returns raw API items."""
    page_counter = 0  # hh.ru uses 0-based pagination
    listings = []
    rate_limit_retries = 3

    while page_counter < MAX_PAGES:
        params = {
            "text": search_term,
            "per_page": 100,
            "page": page_counter,
        }
        try:
            response = requests.get(BASE_URL, params=params, timeout=30)

            if response.status_code == 200:
                data = response.json()
                items = data.get("items", [])
                if not items:
                    break
                listings.extend(items)
                # Stop if we've reached the last page
                pages_total = data.get("pages", 1)
                if page_counter >= pages_total - 1:
                    break
                page_counter += 1
                time.sleep(3)

            elif response.status_code in (429, 503):
                if rate_limit_retries > 0:
                    wait = 60 * (4 - rate_limit_retries)  # 60, 120, 180s
                    logger.warning(
                        "hh.ru rate limited (status %d) for '%s'. Waiting %ds (%d retries left).",
                        response.status_code, search_term, wait, rate_limit_retries,
                    )
                    time.sleep(wait)
                    rate_limit_retries -= 1
                else:
                    logger.warning("hh.ru rate limit retries exhausted for '%s'. Stopping.", search_term)
                    break

            else:
                logger.warning(
                    "hh.ru returned status %d for term '%s'. Stopping.",
                    response.status_code, search_term,
                )
                break

        except Exception as e:
            logger.error("Error fetching postings for '%s': %s", search_term, e)
            break

    return listings


def extract_posting_fields(item: dict, search_term: str) -> dict:
    """Extract flat fields from an hh.ru vacancy item for database storage."""
    salary = item.get("salary") or {}
    snippet = item.get("snippet") or {}
    employer = item.get("employer") or {}
    area = item.get("area") or {}
    experience = item.get("experience") or {}

    return {
        "hh_id": str(item.get("id", "")),
        "search_term": search_term,
        "title": item.get("name"),
        "employer_name": employer.get("name"),
        "area_name": area.get("name"),
        "salary_from": salary.get("from"),
        "salary_to": salary.get("to"),
        "salary_currency": salary.get("currency"),
        "snippet_requirement": snippet.get("requirement"),
        "snippet_responsibility": snippet.get("responsibility"),
        "url": item.get("alternate_url"),
        "published_at": item.get("published_at"),
        "raw_data": item,
    }


def find_all_postings_for_company(known_names: list[str]) -> list[dict]:
    """Search hh.ru for all known names of a company, deduplicate by hh_id."""
    seen_ids = set()
    all_postings = []

    for name in known_names:
        if not name or name == "Название не доступно":
            continue
        raw_items = find_postings_by_search_term(name)
        for item in raw_items:
            hh_id = str(item.get("id", ""))
            if hh_id not in seen_ids:
                seen_ids.add(hh_id)
                all_postings.append(extract_posting_fields(item, name))

    return all_postings
