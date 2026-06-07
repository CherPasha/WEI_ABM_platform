import time
import logging
import re
import requests
from datetime import datetime, timezone
from app.config import settings

logger = logging.getLogger(__name__)

HUNTER_URL = "https://api.hunter.io/v2/domain-search"
HUNTER_VERIFY_URL = "https://api.hunter.io/v2/email-verifier"


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


def verify_email(email: str) -> dict | None:
    """Verify an email address via Hunter.io Email Verifier API v2.

    Returns a dict of verification fields ready to UPDATE into a contacts row,
    or None if verification failed or timed out.

    Handles HTTP 202 (verification in progress) by polling up to 3 times with
    a 2-second delay between attempts.
    """
    params = {"email": email, "api_key": settings.HUNTER_API_KEY}

    for attempt in range(3):
        try:
            response = requests.get(HUNTER_VERIFY_URL, params=params, timeout=30)

            if response.status_code == 200:
                data = response.json().get("data", {})
                return {
                    "email_status":      data.get("status"),
                    "email_score":       data.get("score"),
                    "email_regexp":      data.get("regexp"),
                    "email_gibberish":   data.get("gibberish"),
                    "email_disposable":  data.get("disposable"),
                    "email_webmail":     data.get("webmail"),
                    "email_mx_records":  data.get("mx_records"),
                    "email_smtp_server": data.get("smtp_server"),
                    "email_smtp_check":  data.get("smtp_check"),
                    "email_accept_all":  data.get("accept_all"),
                    "email_block":       data.get("block"),
                    "email_verified_at": datetime.now(timezone.utc).isoformat(),
                }

            if response.status_code == 202:
                logger.info(
                    "Hunter.io email verification in progress for '%s' (attempt %d/3)",
                    email, attempt + 1,
                )
                time.sleep(2)
                continue

            logger.warning(
                "Hunter.io email verify returned %d for '%s': %s",
                response.status_code, email, response.text[:200],
            )
            return None

        except Exception as e:
            logger.error("Error verifying email '%s': %s", email, e)
            return None

    logger.warning("Hunter.io email verification timed out after 3 polls for '%s'", email)
    return None
