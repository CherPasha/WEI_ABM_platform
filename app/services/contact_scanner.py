import logging
import time

import httpx

from app.database import supabase
from app.services.hunter_service import find_contacts_for_domain, verify_email
from app.services.contact_enrichment import enrich_contacts_for_company
from app.services.llm import LLMClient

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500

_NETWORK_ERRORS = (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError, httpx.ConnectTimeout)


def _supabase_call_with_retry(fn, max_retries: int = 4):
    """Execute a Supabase call, retrying on transient network errors."""
    for attempt in range(max_retries):
        try:
            return fn()
        except _NETWORK_ERRORS as e:
            wait = 3 * (attempt + 1)
            logger.warning("Supabase network error (attempt %d/%d), retrying in %ds: %s", attempt + 1, max_retries, wait, e)
            time.sleep(wait)
    return fn()  # final attempt — let it raise if it fails


def _update_scan(scan_id: str, **fields) -> None:
    _supabase_call_with_retry(lambda: supabase.table("contact_scans").update(fields).eq("id", scan_id).execute())


def _batch_insert_contacts(rows: list[dict]) -> None:
    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i:i + _BATCH_SIZE]
        _supabase_call_with_retry(lambda b=batch: supabase.table("contacts").insert(b).execute())


def _get_existing_emails(company_id: str) -> set[str]:
    """Return lower-cased emails already stored for this company."""
    emails: set[str] = set()
    offset = 0
    while True:
        rows = (
            supabase.table("contacts")
            .select("email")
            .eq("company_id", company_id)
            .not_.is_("email", "null")
            .range(offset, offset + 999)
            .execute()
        ).data
        for r in rows:
            if r.get("email"):
                emails.add(r["email"].lower())
        if len(rows) < 1000:
            break
        offset += 1000
    return emails


def _get_existing_names(company_id: str) -> set[tuple[str, str]]:
    """Return (lower first_name, lower last_name) pairs already stored for this company."""
    names: set[tuple[str, str]] = set()
    offset = 0
    while True:
        rows = (
            supabase.table("contacts")
            .select("first_name, last_name")
            .eq("company_id", company_id)
            .range(offset, offset + 999)
            .execute()
        ).data
        for r in rows:
            fn = (r.get("first_name") or "").lower()
            ln = (r.get("last_name") or "").lower()
            if fn or ln:
                names.add((fn, ln))
        if len(rows) < 1000:
            break
        offset += 1000
    return names


def _fetch_companies(project_id: str, keyword_only: bool) -> list[dict]:
    """Fetch all companies across all sessions for a project."""
    sessions = (
        supabase.table("sessions")
        .select("id")
        .eq("project_id", project_id)
        .execute()
    ).data
    if not sessions:
        return []

    session_ids = [s["id"] for s in sessions]
    all_companies: list[dict] = []
    batch_size = 200

    for i in range(0, len(session_ids), batch_size):
        batch = session_ids[i:i + batch_size]
        offset = 0
        while True:
            query = (
                supabase.table("companies")
                .select("id, legal_name, known_names, website_url, keyword_hit_count")
                .in_("session_id", batch)
            )
            if keyword_only:
                query = query.gt("keyword_hit_count", 0)
            rows = query.range(offset, offset + 999).execute().data
            all_companies.extend(rows)
            if len(rows) < 1000:
                break
            offset += 1000

    return all_companies


def run_contact_scan(scan_id: str) -> None:
    """Run a per-project contact scan. Called as a FastAPI BackgroundTask."""
    try:
        scan_result = supabase.table("contact_scans").select("*").eq("id", scan_id).execute()
        if not scan_result.data:
            logger.error("Contact scan %s not found", scan_id)
            return
        scan = scan_result.data[0]
        project_id: str = scan["project_id"]
        use_roles: bool = scan["use_roles"]
        keyword_only: bool = scan["keyword_only"]

        project_result = (
            supabase.table("projects").select("target_roles").eq("id", project_id).execute()
        )
        target_roles: list[str] = (
            project_result.data[0].get("target_roles") or []
            if project_result.data else []
        )

        companies = _fetch_companies(project_id, keyword_only)
        total = len(companies)
        _update_scan(scan_id, total_companies=total)

        if total == 0:
            _update_scan(scan_id, status="completed")
            return

        llm_client = LLMClient() if use_roles and target_roles else None
        contacts_added = 0

        # ── Phase 1: Hunter.io domain search + optional LLM enrichment per company ──
        for i, company in enumerate(companies):
            company_id: str = company["id"]
            website_url: str | None = company.get("website_url")
            existing_emails = _get_existing_emails(company_id)
            # Also track (first_name_lower, last_name_lower) for emailless dedup
            existing_names = _get_existing_names(company_id)

            # Hunter.io domain search
            if website_url:
                try:
                    hunter_contacts = find_contacts_for_domain(website_url)
                    time.sleep(5)  # Hunter.io rate limit between domain searches

                    new_hunter = []
                    for c in hunter_contacts:
                        email = (c.get("email") or "").lower()
                        if email and email in existing_emails:
                            continue
                        if not email:
                            fn = (c.get("first_name") or "").lower()
                            ln = (c.get("last_name") or "").lower()
                            if (fn, ln) in existing_names:
                                continue
                            existing_names.add((fn, ln))
                        c["contact_scan_id"] = scan_id
                        c["company_id"] = company_id
                        new_hunter.append(c)
                        if email:
                            existing_emails.add(email)

                    if new_hunter:
                        _batch_insert_contacts(new_hunter)
                        contacts_added += len(new_hunter)

                except Exception as e:
                    logger.error(
                        "Hunter.io failed for company '%s' (scan %s): %s",
                        company.get("legal_name"), scan_id, e,
                    )

            _update_scan(scan_id, hunter_done=i + 1)

            # LLM role enrichment
            if use_roles and target_roles and llm_client:
                try:
                    existing_contacts = (
                        supabase.table("contacts")
                        .select("*")
                        .eq("company_id", company_id)
                        .execute()
                    ).data
                    # pass session_id=None; enrich_contacts_for_company stores it in returned dicts,
                    # which we then pop and replace with contact_scan_id before inserting
                    enriched = enrich_contacts_for_company(
                        llm_client, company, target_roles, None, existing_contacts
                    )
                    new_enriched = []
                    for c in enriched:
                        email = (c.get("email") or "").lower()
                        if email and email in existing_emails:
                            continue
                        if not email:
                            fn = (c.get("first_name") or "").lower()
                            ln = (c.get("last_name") or "").lower()
                            if (fn, ln) in existing_names:
                                continue
                            existing_names.add((fn, ln))
                        c.pop("session_id", None)   # remove the None session_id
                        c["contact_scan_id"] = scan_id
                        c["company_id"] = company_id
                        new_enriched.append(c)
                        if email:
                            existing_emails.add(email)

                    if new_enriched:
                        _batch_insert_contacts(new_enriched)
                        contacts_added += len(new_enriched)

                except Exception as e:
                    logger.error(
                        "Enrichment failed for company '%s' (scan %s): %s",
                        company.get("legal_name"), scan_id, e,
                    )

                _update_scan(scan_id, enrichment_done=i + 1)

        # ── Phase 2: Email verification ──
        contacts_to_verify = []
        _offset = 0
        while True:
            page = (
                supabase.table("contacts")
                .select("id, email")
                .eq("contact_scan_id", scan_id)
                .not_.is_("email", "null")
                .range(_offset, _offset + 999)
                .execute()
            ).data
            contacts_to_verify.extend(page)
            if len(page) < 1000:
                break
            _offset += 1000

        total_verification = len(contacts_to_verify)
        _update_scan(
            scan_id,
            total_verification=total_verification,
            contacts_added=contacts_added,
        )

        for i, contact in enumerate(contacts_to_verify):
            try:
                result = verify_email(contact["email"])
                if result is not None:
                    _supabase_call_with_retry(
                        lambda cid=contact["id"], r=result: supabase.table("contacts").update(r).eq("id", cid).execute()
                    )
            except Exception as e:
                logger.error(
                    "Verification failed for contact %s ('%s'): %s",
                    contact["id"], contact["email"], e,
                )

            time.sleep(0.2)  # Hunter.io rate limit: 300 req/min
            _update_scan(scan_id, verification_done=i + 1)

        _update_scan(scan_id, status="completed")
        logger.info("Contact scan %s completed, %d contacts added", scan_id, contacts_added)

    except Exception as e:
        logger.exception("Contact scan %s failed: %s", scan_id, e)
        try:
            _update_scan(scan_id, status="failed", error_message=str(e)[:500])
        except Exception:
            pass
