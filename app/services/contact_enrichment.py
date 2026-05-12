import json
import logging
import re
import time

import httpx
from google import genai

from app.config import settings
from app.services.hunter_service import clean_domain, is_valid_domain

logger = logging.getLogger(__name__)

MODEL = "models/gemini-2.5-flash"

# ── Cyrillic → Latin transliteration (GOST 7.79-2000 System B) ──

TRANSLIT_MAP = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
}


def transliterate(text: str) -> str:
    """Convert Cyrillic text to Latin characters."""
    result = []
    for ch in text.lower():
        if ch in TRANSLIT_MAP:
            result.append(TRANSLIT_MAP[ch])
        elif ch.isascii() and ch.isalpha():
            result.append(ch)
        # skip non-alpha characters (spaces, punctuation, etc.)
    return "".join(result)


def split_name(full_name: str) -> tuple[str, str]:
    """Split 'Имя Фамилия' into (first, last). Returns transliterated parts."""
    parts = full_name.strip().split()
    if len(parts) >= 2:
        first = transliterate(parts[0])
        last = transliterate(parts[1])
    elif len(parts) == 1:
        first = transliterate(parts[0])
        last = ""
    else:
        first, last = "", ""
    return first, last


# ── Email pattern detection ──

PATTERNS = [
    ("first.last",  lambda f, l: f"{f}.{l}"),
    ("f.last",      lambda f, l: f"{f[0]}.{l}" if f else ""),
    ("first_last",  lambda f, l: f"{f}_{l}"),
    ("flast",       lambda f, l: f"{f[0]}{l}" if f else ""),
    ("firstl",      lambda f, l: f"{f}{l[0]}" if l else ""),
    ("first",       lambda f, l: f),
    ("last.first",  lambda f, l: f"{l}.{f}"),
    ("last_first",  lambda f, l: f"{l}_{f}"),
    ("lastf",       lambda f, l: f"{l}{f[0]}" if f else ""),
]


def detect_email_pattern(contacts: list[dict]) -> str | None:
    """Analyze existing contacts to detect the company's email naming pattern.

    Returns the pattern name (e.g. 'first.last') or None if detection fails.
    """
    usable = [
        c for c in contacts
        if c.get("email") and c.get("first_name") and c.get("last_name")
    ]
    if len(usable) < 2:
        return None

    votes: dict[str, int] = {}
    for c in usable:
        local = c["email"].split("@")[0].lower()
        first = c["first_name"].lower()
        last = c["last_name"].lower()

        for name, gen in PATTERNS:
            try:
                if gen(first, last) == local:
                    votes[name] = votes.get(name, 0) + 1
                    break
            except (IndexError, TypeError):
                continue

    if not votes:
        return None

    winner = max(votes, key=votes.get)
    if votes[winner] >= len(usable) / 2:
        return winner
    return None


def generate_email(first: str, last: str, pattern: str, domain: str) -> str:
    """Generate an email address from transliterated name parts + pattern + domain."""
    generators = dict(PATTERNS)
    gen = generators.get(pattern)
    if not gen or not first:
        return ""
    try:
        local = gen(first, last)
    except (IndexError, TypeError):
        return ""
    if not local:
        return ""
    return f"{local}@{domain}"


# ── Gemini role lookup ──

ROLE_PROMPT_TEMPLATE = (
    "Ты опытный аналитик российского рынка.\n"
    "Компания: {company_name} (также известна как: {known_names}).\n"
    "Найди имена и фамилии людей, занимающих следующие должности в этой компании:\n"
    "{roles}\n\n"
    "Ответь строго в формате JSON-массива, без дополнительного текста:\n"
    '[{{"name": "Имя Фамилия", "role": "Должность"}}]\n\n'
    "Если не можешь найти человека на какую-то должность, не включай его в массив.\n"
    "Не выдумывай имена — указывай только те, в которых уверен."
)


def find_people_by_roles(
    client: genai.Client,
    company_name: str,
    known_names: list[str],
    target_roles: list[str],
) -> list[dict]:
    """Use Gemini to find people holding target roles at a company.

    Returns list of {"name": "...", "role": "..."} dicts.
    """
    prompt = ROLE_PROMPT_TEMPLATE.format(
        company_name=company_name,
        known_names=", ".join(known_names) if known_names else company_name,
        roles="\n".join(f"- {r}" for r in target_roles),
    )

    max_retries = 5
    retry_delay = 15

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
            )
            text = response.text.strip()

            # Strip markdown code fences if present
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

            people = json.loads(text)
            if not isinstance(people, list):
                logger.warning("Gemini returned non-list for '%s': %s", company_name, text[:200])
                return []
            return [p for p in people if isinstance(p, dict) and p.get("name") and p.get("role")]

        except json.JSONDecodeError:
            logger.warning("Failed to parse Gemini JSON for '%s': %s", company_name, text[:200])
            return []

        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as e:
            wait = 5 * (attempt + 1)
            logger.warning(
                "Network error for '%s', retrying in %ds (attempt %d/%d): %s",
                company_name, wait, attempt + 1, max_retries, e,
            )
            time.sleep(wait)

        except Exception as e:
            error_msg = str(e).lower()
            if "safety" in error_msg or "blocked" in error_msg:
                logger.warning("Content blocked by safety filters for: %s", company_name)
                return []

            wait = retry_delay * (2 ** attempt)
            logger.warning(
                "Gemini API error for '%s'. Waiting %ds (attempt %d/%d): %s",
                company_name, wait, attempt + 1, max_retries, e,
            )
            time.sleep(wait)

    logger.error("All retries exhausted for role lookup at '%s'.", company_name)
    return []


# ── Orchestrator ──


def enrich_contacts_for_company(
    client: genai.Client,
    company: dict,
    target_roles: list[str],
    session_id: str,
    existing_contacts: list[dict],
) -> list[dict]:
    """Generate probable contacts for target roles at a company.

    1. Detect email pattern from existing Hunter contacts
    2. Use Gemini to find people in target roles
    3. Transliterate names and generate emails
    4. Return new contact dicts ready for DB insertion
    """
    website_url = company.get("website_url")
    if not website_url:
        return []

    domain = clean_domain(website_url)
    if not is_valid_domain(domain):
        return []

    # Detect email pattern from existing contacts (optional — enrichment proceeds without it)
    pattern = detect_email_pattern(existing_contacts)
    if not pattern:
        logger.info(
            "No email pattern detected for '%s' (domain: %s). Contacts will be saved without email.",
            company.get("legal_name"), domain,
        )

    # Find people via Gemini
    known_names = company.get("known_names") or [company.get("legal_name", "")]
    people = find_people_by_roles(client, company.get("legal_name", ""), known_names, target_roles)
    if not people:
        return []

    # Build set of existing names for dedup
    existing_names = set()
    for c in existing_contacts:
        fn = (c.get("first_name") or "").lower()
        ln = (c.get("last_name") or "").lower()
        if fn or ln:
            existing_names.add((fn, ln))

    new_contacts = []
    for person in people:
        first_lat, last_lat = split_name(person["name"])
        if not first_lat:
            continue

        # Check for duplicate
        if (first_lat, last_lat) in existing_names:
            continue

        email = generate_email(first_lat, last_lat, pattern, domain) if pattern else ""

        new_contacts.append({
            "session_id": session_id,
            "company_id": company["id"],
            "email": email,
            "confidence": 0,
            "first_name": person["name"].split()[0] if person["name"].split() else "",
            "last_name": " ".join(person["name"].split()[1:]) if len(person["name"].split()) > 1 else "",
            "position": person["role"],
            "source": "enriched",
        })
        existing_names.add((first_lat, last_lat))

    return new_contacts
