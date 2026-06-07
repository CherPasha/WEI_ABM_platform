import json
import logging
import time
import httpx

from app.database import supabase
from app.config import settings
from app.services.company_parser import parse_xlsx_to_companies
from app.services.name_resolver import find_company_real_name, NAME_UNAVAILABLE
from app.services.postings_finder import find_all_postings_for_company
from app.services.yandex_search import find_all_news_for_company
from app.services.hunter_service import find_contacts_for_domain, verify_email
from app.services.contact_enrichment import enrich_contacts_for_company
from app.services.llm import LLMClient

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


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


def _update_session(session_id: str, **fields):
    """Update session fields in Supabase."""
    _supabase_call_with_retry(
        lambda: supabase.table("sessions").update(fields).eq("id", session_id).execute()
    )


def _batch_insert(table: str, rows: list[dict]):
    """Insert rows in batches to avoid payload limits."""
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        _supabase_call_with_retry(
            lambda b=batch: supabase.table(table).insert(b).execute()
        )


def _fetch_all_companies(session_id: str) -> list[dict]:
    """Fetch all companies for a session, paginating past Supabase's 1000-row limit."""
    all_rows = []
    page_size = 1000
    offset = 0
    while True:
        result = _supabase_call_with_retry(
            lambda o=offset: supabase.table("companies")
            .select("id, legal_name, known_names, website_url")
            .eq("session_id", session_id)
            .order("created_at")
            .range(o, o + page_size - 1)
            .execute()
        )
        rows = result.data
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size
    return all_rows


def _session_exists(session_id: str) -> bool:
    """Return True if the session row still exists in the DB."""
    result = _supabase_call_with_retry(
        lambda: supabase.table("sessions").select("id").eq("id", session_id).execute()
    )
    return bool(result.data)


def _get_session_flags(session_id: str) -> dict:
    """Fetch the run_* flags for a session."""
    result = (
        supabase.table("sessions")
        .select("run_postings, run_news, run_contacts, run_enrichment, run_verification, project_id")
        .eq("id", session_id)
        .execute()
    )
    return result.data[0] if result.data else {}


def process_session(session_id: str, file_bytes: bytes, filename: str):
    """Run the full processing pipeline for a session. Called as a background task."""
    try:
        # --- Step 1: Parse xlsx and insert companies ---
        _update_session(session_id, status="parsing")
        companies = parse_xlsx_to_companies(file_bytes, filename)
        total = len(companies)
        _update_session(session_id, total_companies=total)

        # Insert companies into Supabase and get back their IDs
        for company in companies:
            company["session_id"] = session_id

        _batch_insert("companies", companies)

        # Fetch the inserted companies to get their UUIDs (ordered for consistent resume)
        db_companies = _fetch_all_companies(session_id)

        # Read which stages are enabled
        flags = _get_session_flags(session_id)
        run_postings = flags.get("run_postings", True)
        run_news = flags.get("run_news", True)
        run_contacts = flags.get("run_contacts", True)
        run_enrichment = flags.get("run_enrichment", True)
        run_verification = flags.get("run_verification", True)

        # --- Step 2: Resolve company names ---
        # Always runs — needed by postings and news stages
        _update_session(session_id, status="resolving_names", names_done=0)
        llm_client = LLMClient()

        for i, company in enumerate(db_companies):
            legal_name = company.get("legal_name", "")
            if not legal_name:
                continue

            real_name = find_company_real_name(llm_client, legal_name)
            known_names = company.get("known_names") or [legal_name]

            if real_name and real_name != NAME_UNAVAILABLE and real_name not in known_names:
                known_names.append(real_name)
            elif not known_names:
                known_names = [legal_name]

            supabase.table("companies").update(
                {"known_names": known_names}
            ).eq("id", company["id"]).execute()

            company["known_names"] = known_names
            _update_session(session_id, names_done=i + 1)

        # --- Step 3: Find postings ---
        if not _session_exists(session_id):
            logger.warning("Session %s was deleted during processing, aborting", session_id)
            return

        if run_postings:
            _update_session(session_id, status="finding_postings", postings_done=0)

            for i, company in enumerate(db_companies):
                try:
                    known_names = company.get("known_names") or []
                    postings = find_all_postings_for_company(known_names)

                    if postings:
                        for p in postings:
                            p["session_id"] = session_id
                            p["company_id"] = company["id"]
                            if isinstance(p.get("raw_data"), dict):
                                p["raw_data"] = json.dumps(p["raw_data"], ensure_ascii=False)

                        _batch_insert("postings", postings)

                except Exception as e:
                    logger.error(
                        "Failed to fetch/store postings for company '%s' (session %s): %s",
                        company.get("legal_name"), session_id, e,
                    )

                _update_session(session_id, postings_done=i + 1)
        else:
            _update_session(session_id, postings_done=total)

        # --- Step 3.5: Find news articles ---
        if not _session_exists(session_id):
            logger.warning("Session %s was deleted during processing, aborting", session_id)
            return

        if run_news:
            _update_session(session_id, status="finding_news", news_done=0)

            for i, company in enumerate(db_companies):
                try:
                    known_names = company.get("known_names") or []
                    articles = find_all_news_for_company(known_names)

                    if articles:
                        for a in articles:
                            a["session_id"] = session_id
                            a["company_id"] = company["id"]
                            if isinstance(a.get("raw_data"), dict):
                                a["raw_data"] = json.dumps(a["raw_data"], ensure_ascii=False)

                        _batch_insert("news_articles", articles)

                except Exception as e:
                    logger.error(
                        "Failed to fetch/store news for company '%s' (session %s): %s",
                        company.get("legal_name"), session_id, e,
                    )

                _update_session(session_id, news_done=i + 1)
        else:
            _update_session(session_id, news_done=total)

        # --- Step 4: Find contacts via Hunter.io ---
        if not _session_exists(session_id):
            logger.warning("Session %s was deleted during processing, aborting", session_id)
            return

        if run_contacts:
            _update_session(session_id, status="finding_contacts", contacts_done=0)

            for i, company in enumerate(db_companies):
                website_url = company.get("website_url")
                if not website_url:
                    _update_session(session_id, contacts_done=i + 1)
                    continue

                contacts = find_contacts_for_domain(website_url)
                time.sleep(5)  # Rate limit between Hunter.io calls

                if contacts:
                    for c in contacts:
                        c["session_id"] = session_id
                        c["company_id"] = company["id"]

                    _batch_insert("contacts", contacts)

                _update_session(session_id, contacts_done=i + 1)
        else:
            _update_session(session_id, contacts_done=total)

        # --- Step 5: Contact enrichment via Gemini ---
        if run_enrichment:
            project_id = flags.get("project_id")
            project = supabase.table("projects").select("target_roles").eq("id", project_id).execute()
            target_roles = project.data[0].get("target_roles") or []

            if target_roles:
                _update_session(session_id, status="enriching_contacts", enrichment_done=0)

                for i, company in enumerate(db_companies):
                    try:
                        existing = (
                            supabase.table("contacts")
                            .select("*")
                            .eq("company_id", company["id"])
                            .execute()
                        ).data
                        new_contacts = enrich_contacts_for_company(
                            llm_client, company, target_roles, session_id, existing,
                        )
                        if new_contacts:
                            _batch_insert("contacts", new_contacts)
                    except Exception as e:
                        logger.error(
                            "Enrichment failed for company '%s' (session %s): %s",
                            company.get("legal_name"), session_id, e,
                        )
                    _update_session(session_id, enrichment_done=i + 1)
            else:
                _update_session(session_id, enrichment_done=total)
        else:
            _update_session(session_id, enrichment_done=total)

        # --- Step 6: Verify email addresses via Hunter.io ---
        if not _session_exists(session_id):
            logger.warning("Session %s was deleted during processing, aborting", session_id)
            return

        if run_verification:
            contacts_to_verify = _supabase_call_with_retry(
                lambda: supabase.table("contacts")
                .select("id, email")
                .eq("session_id", session_id)
                .not_.is_("email", "null")
                .order("id")
                .execute()
            ).data

            _update_session(
                session_id,
                status="verifying_emails",
                verification_done=0,
                total_verification=len(contacts_to_verify),
            )

            for i, contact in enumerate(contacts_to_verify):
                try:
                    result = verify_email(contact["email"])
                    if result is not None:
                        _supabase_call_with_retry(
                            lambda r=result, cid=contact["id"]: supabase.table("contacts")
                            .update(r)
                            .eq("id", cid)
                            .execute()
                        )
                except Exception as e:
                    logger.error(
                        "Email verification failed for contact %s ('%s'): %s",
                        contact["id"], contact["email"], e,
                    )

                time.sleep(0.2)  # Hunter.io rate limit: 300 req/min
                _update_session(session_id, verification_done=i + 1)
        else:
            _update_session(session_id, verification_done=0, total_verification=0)

        # --- Done ---
        _update_session(session_id, status="completed")
        logger.info("Session %s completed successfully", session_id)

    except Exception as e:
        logger.exception("Session %s failed: %s", session_id, e)
        _update_session(session_id, status="failed", error_message=str(e)[:500])


def resume_session(session_id: str):
    """Resume processing from the last saved position of each stage."""
    try:
        # Fetch current session state
        result = (
            supabase.table("sessions")
            .select("total_companies, names_done, postings_done, news_done, contacts_done, enrichment_done, project_id, run_postings, run_news, run_contacts, run_enrichment")
            .eq("id", session_id)
            .execute()
        )
        if not result.data:
            logger.error("Session %s not found for resume", session_id)
            return

        session = result.data[0]
        total = session["total_companies"] or 0
        names_done = session["names_done"] or 0
        postings_done = session["postings_done"] or 0
        news_done = session["news_done"] or 0
        contacts_done = session["contacts_done"] or 0
        enrichment_done = session["enrichment_done"] or 0
        project_id = session["project_id"]

        run_postings = session.get("run_postings", True)
        run_news = session.get("run_news", True)
        run_contacts = session.get("run_contacts", True)
        run_enrichment = session.get("run_enrichment", True)

        if total == 0:
            logger.warning("Session %s has no companies, cannot resume", session_id)
            return

        # Fetch companies in the same stable order used during initial processing
        db_companies = _fetch_all_companies(session_id)

        # ── Stage 1: Resolve names (resume from names_done) ──
        if names_done < total:
            _update_session(session_id, status="resolving_names")
            llm_client = LLMClient()

            for i, company in enumerate(db_companies[names_done:], start=names_done):
                legal_name = company.get("legal_name", "")
                if not legal_name:
                    _update_session(session_id, names_done=i + 1)
                    continue

                real_name = find_company_real_name(llm_client, legal_name)
                known_names = company.get("known_names") or [legal_name]

                if real_name and real_name != NAME_UNAVAILABLE and real_name not in known_names:
                    known_names.append(real_name)
                elif not known_names:
                    known_names = [legal_name]

                supabase.table("companies").update(
                    {"known_names": known_names}
                ).eq("id", company["id"]).execute()

                company["known_names"] = known_names
                _update_session(session_id, names_done=i + 1)

            # Re-fetch so postings stage has up-to-date known_names
            db_companies = _fetch_all_companies(session_id)

        # ── Stage 2: Find postings (resume from postings_done) ──
        if run_postings and postings_done < total:
            _update_session(session_id, status="finding_postings")

            for i, company in enumerate(db_companies[postings_done:], start=postings_done):
                try:
                    known_names = company.get("known_names") or []
                    postings = find_all_postings_for_company(known_names)

                    if postings:
                        for p in postings:
                            p["session_id"] = session_id
                            p["company_id"] = company["id"]
                            if isinstance(p.get("raw_data"), dict):
                                p["raw_data"] = json.dumps(p["raw_data"], ensure_ascii=False)
                        _batch_insert("postings", postings)

                except Exception as e:
                    logger.error(
                        "Failed postings for company '%s' (session %s): %s",
                        company.get("legal_name"), session_id, e,
                    )

                _update_session(session_id, postings_done=i + 1)
        elif not run_postings and postings_done < total:
            _update_session(session_id, postings_done=total)

        # ── Stage 2.5: Find news articles (resume from news_done) ──
        if run_news and news_done < total:
            _update_session(session_id, status="finding_news")

            for i, company in enumerate(db_companies[news_done:], start=news_done):
                try:
                    known_names = company.get("known_names") or []
                    articles = find_all_news_for_company(known_names)

                    if articles:
                        for a in articles:
                            a["session_id"] = session_id
                            a["company_id"] = company["id"]
                            if isinstance(a.get("raw_data"), dict):
                                a["raw_data"] = json.dumps(a["raw_data"], ensure_ascii=False)
                        _batch_insert("news_articles", articles)

                except Exception as e:
                    logger.error(
                        "Failed news for company '%s' (session %s): %s",
                        company.get("legal_name"), session_id, e,
                    )

                _update_session(session_id, news_done=i + 1)
        elif not run_news and news_done < total:
            _update_session(session_id, news_done=total)

        # ── Stage 3: Find contacts (resume from contacts_done) ──
        if run_contacts and contacts_done < total:
            _update_session(session_id, status="finding_contacts")

            for i, company in enumerate(db_companies[contacts_done:], start=contacts_done):
                try:
                    website_url = company.get("website_url")
                    if not website_url:
                        _update_session(session_id, contacts_done=i + 1)
                        continue

                    contacts = find_contacts_for_domain(website_url)
                    time.sleep(5)

                    if contacts:
                        for c in contacts:
                            c["session_id"] = session_id
                            c["company_id"] = company["id"]
                        _batch_insert("contacts", contacts)

                except Exception as e:
                    logger.error(
                        "Failed contacts for company '%s' (session %s): %s",
                        company.get("legal_name"), session_id, e,
                    )

                _update_session(session_id, contacts_done=i + 1)
        elif not run_contacts and contacts_done < total:
            _update_session(session_id, contacts_done=total)

        # ── Stage 4: Contact enrichment (resume from enrichment_done) ──
        if run_enrichment and enrichment_done < total:
            project = supabase.table("projects").select("target_roles").eq("id", project_id).execute()
            target_roles = project.data[0].get("target_roles") or []

            if target_roles:
                _update_session(session_id, status="enriching_contacts")
                llm_client = LLMClient()

                for i, company in enumerate(db_companies[enrichment_done:], start=enrichment_done):
                    try:
                        existing = (
                            supabase.table("contacts")
                            .select("*")
                            .eq("company_id", company["id"])
                            .execute()
                        ).data
                        new_contacts = enrich_contacts_for_company(
                            llm_client, company, target_roles, session_id, existing,
                        )
                        if new_contacts:
                            _batch_insert("contacts", new_contacts)
                    except Exception as e:
                        logger.error(
                            "Enrichment failed for company '%s' (session %s): %s",
                            company.get("legal_name"), session_id, e,
                        )
                    _update_session(session_id, enrichment_done=i + 1)
            else:
                _update_session(session_id, enrichment_done=total)
        elif not run_enrichment and enrichment_done < total:
            _update_session(session_id, enrichment_done=total)

        _update_session(session_id, status="completed")
        logger.info("Session %s resumed and completed successfully", session_id)

    except Exception as e:
        logger.exception("Resume of session %s failed: %s", session_id, e)
        _update_session(session_id, status="failed", error_message=str(e)[:500])
