import time
import logging
import re
import requests
from app.config import settings

logger = logging.getLogger(__name__)

HUNTER_URL = "https://api.hunter.io/v2/domain-search"


def parse_hunter_item(item: dict) -> dict:
    """Parse a single email item from the Hunter.io API response."""
    return {
        "email": item.get("value"),
        "confidence": item.get("confidence"),
        "first_name": item.get("first_name"),
        "last_name": item.get("last_name"),
        "position": item.get("position"),
        "position_raw": item.get("position_raw"),
        "seniority": item.get("seniority"),
        "department": item.get("department"),
        "linkedin": item.get("linkedin"),
        "phone_number": item.get("phone_number"),
        "source": "hunter",
    }


def clean_domain(url: str) -> str:
    """Extract bare domain from a URL string."""
    url = url.strip()
    # Remove protocol
    for prefix in ("https://", "http://"):
        if url.lower().startswith(prefix):
            url = url[len(prefix):]
    # Remove www.
    if url.lower().startswith("www."):
        url = url[4:]
    # Remove path
    url = url.split("/")[0]
    return url


def is_valid_domain(domain: str) -> bool:
    """Check that a domain looks valid (not numeric garbage)."""
    if not domain or re.match(r"^\d+$", domain):
        return False
    if "." not in domain:
        return False
    return True


def find_contacts_for_domain(website_url: str) -> list[dict]:
    """Query Hunter.io for contacts at a given domain."""
    domain = clean_domain(website_url)
    if not is_valid_domain(domain):
        return []

    params = {
        "domain": domain,
        "type": "personal",
        "seniority": "senior,executive",
        "api_key": settings.HUNTER_API_KEY,
    }

    try:
        response = requests.get(HUNTER_URL, params=params, timeout=30)
        if response.status_code == 200:
            data = response.json().get("data", {})
            emails = data.get("emails", [])
            return [parse_hunter_item(item) for item in emails]
        else:
            logger.warning(
                "Hunter.io returned status %d for domain '%s': %s",
                response.status_code, domain, response.text[:200],
            )
            return []
    except Exception as e:
        logger.error("Error querying Hunter.io for '%s': %s", domain, e)
        return []
