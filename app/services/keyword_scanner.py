import io
import re
import logging
import time
from datetime import datetime, timezone
from typing import Callable

import httpx
import pandas as pd

from app.database import supabase


class ScanCancelledError(Exception):
    """Raised when a keyword scan is flagged for cancellation by the user."""


_MAX_SENTENCES_PER_KW = 5  # cap stored sentences to bound memory and DataFrame size

logger = logging.getLogger(__name__)

POSTING_TEXT_FIELDS = ["title", "snippet_requirement", "snippet_responsibility"]
NEWS_TEXT_FIELDS = ["title", "snippet", "full_text"]
HTML_TAG_RE = re.compile(r"<[^>]+>")
SENTENCE_SPLIT_RE = re.compile(r"[.!?;]\s+")


def _strip_html(text: str) -> str:
    return HTML_TAG_RE.sub("", text)


def _extract_sentences(text: str, keyword_pattern: re.Pattern) -> list[str]:
    """Find all sentences in text that contain the keyword."""
    sentences = SENTENCE_SPLIT_RE.split(text)
    return [s.strip() for s in sentences if keyword_pattern.search(s)]


def _is_checkpointed(
    company_ids: list[str],
    threshold: datetime,
    scanned_map: dict,
) -> bool:
    """Return True if any company row has keyword_scanned_at >= threshold."""
    cmp_threshold = threshold if threshold.tzinfo else threshold.replace(tzinfo=timezone.utc)
    for cid in company_ids:
        ts_str = scanned_map.get(cid)
        if ts_str is None:
            continue
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cmp_threshold:
                return True
        except (ValueError, TypeError):
            continue
    return False


_RETRY_DELAYS = (2, 5)  # seconds to wait before retry attempts 1 and 2


def _execute(builder, max_retries: int = 2):
    """Execute a PostgREST query builder, retrying on connection-level failures.

    postgrest's send_with_retry only retries on HTTP 503/520 (Cloudflare errors).
    It does not catch httpx.RemoteProtocolError (TCP disconnect / H2 GOAWAY),
    so we add that retry layer here.
    """
    for attempt in range(max_retries + 1):
        try:
            return builder.execute()
        except httpx.RemoteProtocolError:
            if attempt < max_retries:
                delay = _RETRY_DELAYS[attempt]
                logger.warning(
                    "Supabase connection dropped (attempt %d/%d), retrying in %ds",
                    attempt + 1, max_retries + 1, delay,
                )
                time.sleep(delay)
            else:
                raise


def _fetch_all(table: str, column: str, value: str, select: str = "*") -> list[dict]:
    """Fetch all rows matching column=value, paginating through 1000-row limit."""
    all_rows = []
    offset = 0
    page_size = 1000
    while True:
        result = _execute(
            supabase.table(table)
            .select(select)
            .eq(column, value)
            .range(offset, offset + page_size - 1)
        )
        rows = result.data
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size
    return all_rows


def _fetch_all_in(table: str, column: str, values: list[str], select: str = "*", batch_size: int = 200) -> list[dict]:
    """Fetch all rows where column IN (values), paginating in batches."""
    if not values:
        return []
    all_rows = []
    for i in range(0, len(values), batch_size):
        batch_values = values[i: i + batch_size]
        offset = 0
        page_size = 1000
        while True:
            result = _execute(
                supabase.table(table)
                .select(select)
                .in_(column, batch_values)
                .range(offset, offset + page_size - 1)
            )
            rows = result.data
            if not rows:
                break
            all_rows.extend(rows)
            if len(rows) < page_size:
                break
            offset += page_size
    return all_rows


def _compute_company_hits(groups: list[dict], keyword_results: dict) -> tuple[int, int]:
    """Compute total keyword hit count and number of groups with at least one hit."""
    hit_count = sum(kd.get("count", 0) for kd in keyword_results.values())
    hit_groups = sum(
        1 for g in groups
        if any(keyword_results.get(kw, {}).get("count", 0) > 0 for kw in g["keywords"])
    )
    return hit_count, hit_groups


def scan_project_keywords(
    project_id: str,
    scan_started_at: datetime,
    on_total_known: Callable[[int], None],
    on_company_done: Callable[[int], None],
    is_cancelled: Callable[[], bool] = lambda: False,
) -> dict:
    """Scan all postings in a project for keyword matches.

    Returns:
        {
            "groups": [{"name": str, "keywords": [str, ...]}],
            "companies": [
                {
                    "name": str,
                    "inn": str,
                    "results": {
                        keyword: {"count": int, "sentences": [{"field": str, "title": str, "sentence": str}]}
                    }
                }
            ]
        }
    """
    # 1. Fetch keyword groups and keywords
    groups_rows = _fetch_all("keyword_groups", "project_id", project_id, select="id, name")
    if not groups_rows:
        raise ValueError("No keyword groups defined for this project")

    group_ids = [g["id"] for g in groups_rows]
    keywords_rows = _fetch_all_in("keywords", "group_id", group_ids, select="id, group_id, keyword")

    # Build group structure
    groups = []
    all_keywords = []  # list of (keyword_text, group_name)
    for g in groups_rows:
        kws = [k["keyword"] for k in keywords_rows if k["group_id"] == g["id"]]
        groups.append({"name": g["name"], "keywords": kws})
        for kw in kws:
            all_keywords.append((kw, g["name"]))

    if not all_keywords:
        raise ValueError("No keywords defined in any group")

    # Compile patterns once
    keyword_patterns = {
        kw: re.compile(re.escape(kw), re.IGNORECASE)
        for kw, _ in all_keywords
    }

    # 1b. Fetch stop words and compile patterns (empty list = no filtering)
    stop_word_rows = _fetch_all("stop_words", "project_id", project_id, select="word")
    stop_patterns = [
        re.compile(re.escape(r["word"]), re.IGNORECASE)
        for r in stop_word_rows
    ]

    def _is_stopped(pub: dict, fields: list[str]) -> bool:
        """Return True if any stop word matches any text field of this publication."""
        for field in fields:
            text = _strip_html(pub.get(field) or "")
            if not text:
                continue
            for pattern in stop_patterns:
                if pattern.search(text):
                    return True
        return False

    # 2. Fetch all sessions for this project
    sessions = _fetch_all("sessions", "project_id", project_id, select="id")
    session_ids = [s["id"] for s in sessions]
    if not session_ids:
        raise ValueError("No sessions found in this project")

    # 3. Fetch all companies across sessions
    all_companies = _fetch_all_in(
        "companies", "session_id", session_ids,
        select="id, legal_name, inn, known_names, source_company_id"
    )

    # 4. Deduplicate companies by inn (fallback: lower(legal_name))
    dedup: dict[str, dict] = {}
    for c in all_companies:
        inn = (c.get("inn") or "").strip()
        legal_name = c.get("legal_name", "")
        key = f"inn:{inn}" if inn else f"name:{legal_name.strip().lower()}"

        if key not in dedup:
            dedup[key] = {"name": legal_name, "inn": inn, "company_ids": [], "effective_ids": {}}
        cid = c["id"]
        dedup[key]["company_ids"].append(cid)
        # Ghost companies (source_company_id set) fetch postings/news by source ID
        dedup[key]["effective_ids"][cid] = c.get("source_company_id") or cid

    unique_companies = list(dedup.values())

    # 4b. Fetch keyword_scanned_at for all company IDs (one batch query for resume detection)
    all_cids_for_checkpoint = [cid for uc in unique_companies for cid in uc["company_ids"]]
    if all_cids_for_checkpoint:
        try:
            checkpoint_rows = _fetch_all_in(
                "companies", "id", all_cids_for_checkpoint,
                select="id, keyword_scanned_at"
            )
        except Exception as e:
            raise RuntimeError(
                "Failed to fetch keyword_scanned_at checkpoints — "
                "run supabase_migration_keyword_scanned_at.sql"
            ) from e
        scanned_map = {r["id"]: r.get("keyword_scanned_at") for r in checkpoint_rows}
    else:
        scanned_map = {}

    companies_total_unique = len(unique_companies)
    unprocessed = [
        uc for uc in unique_companies
        if not _is_checkpointed(uc["company_ids"], scan_started_at, scanned_map)
    ]
    already_done = companies_total_unique - len(unprocessed)
    on_total_known(companies_total_unique)

    # 5. Process companies in batches — fetch postings/news per batch so peak
    #    memory stays bounded regardless of project size.
    _COMPANY_BATCH = 1

    result_companies = []
    done_count = already_done
    for batch_start in range(0, len(unprocessed), _COMPANY_BATCH):
        # Yield GIL between batches so the asyncio event loop can process
        # status-poll requests and avoid proxy 504 timeouts.
        time.sleep(0)

        batch = unprocessed[batch_start:batch_start + _COMPANY_BATCH]
        batch_cids = [cid for uc in batch for cid in uc["company_ids"]]
        # Use effective IDs (source_company_id for ghost companies) when querying postings/news
        effective_fetch_ids = list({uc["effective_ids"][cid] for uc in batch for cid in uc["company_ids"]})

        batch_postings = _fetch_all_in(
            "postings", "company_id", effective_fetch_ids,
            select="company_id, title, snippet_requirement, snippet_responsibility"
        )
        postings_by_company: dict[str, list] = {}
        for p in batch_postings:
            postings_by_company.setdefault(p["company_id"], []).append(p)

        batch_news_meta = _fetch_all_in(
            "news_articles", "company_id", effective_fetch_ids,
            select="id, company_id, title, snippet"
        )
        # Fetch full_text in small batches of 10 to avoid response-size timeouts
        news_ids = [a["id"] for a in batch_news_meta]
        full_text_rows = _fetch_all_in(
            "news_articles", "id", news_ids,
            select="id, full_text", batch_size=10
        )
        full_text_map = {r["id"]: r.get("full_text") for r in full_text_rows}
        batch_news = [{**a, "full_text": full_text_map.get(a["id"])} for a in batch_news_meta]
        news_by_company: dict[str, list] = {}
        for a in batch_news:
            news_by_company.setdefault(a["company_id"], []).append(a)

        # 6. For each company x keyword, search postings and news
        for uc in batch:
            time.sleep(0)
            if is_cancelled():
                raise ScanCancelledError()
            company_postings = []
            company_news = []
            for cid in uc["company_ids"]:
                effective_id = uc["effective_ids"][cid]
                company_postings.extend(postings_by_company.get(effective_id, []))
                company_news.extend(news_by_company.get(effective_id, []))

            # Pre-filter publications that contain a stop word (do this once, not per keyword)
            if stop_patterns:
                company_postings = [p for p in company_postings if not _is_stopped(p, POSTING_TEXT_FIELDS)]
                company_news = [a for a in company_news if not _is_stopped(a, NEWS_TEXT_FIELDS)]

            keyword_results = {}
            for kw, _group_name in all_keywords:
                pattern = keyword_patterns[kw]
                matches = []

                for posting in company_postings:
                    for field in POSTING_TEXT_FIELDS:
                        raw_text = posting.get(field) or ""
                        if not raw_text:
                            continue
                        clean_text = _strip_html(raw_text)
                        for sentence in _extract_sentences(clean_text, pattern):
                            matches.append({
                                "source": "posting",
                                "field": field,
                                "title": posting.get("title") or "",
                                "sentence": sentence,
                            })

                for article in company_news:
                    for field in NEWS_TEXT_FIELDS:
                        raw_text = article.get(field) or ""
                        if not raw_text:
                            continue
                        clean_text = _strip_html(raw_text)
                        for sentence in _extract_sentences(clean_text, pattern):
                            matches.append({
                                "source": "news",
                                "field": field,
                                "title": article.get("title") or "",
                                "sentence": sentence,
                            })

                keyword_results[kw] = {"count": len(matches), "sentences": matches[:_MAX_SENTENCES_PER_KW]}

            hit_count, hit_groups = _compute_company_hits(groups, keyword_results)
            for cid in uc["company_ids"]:
                try:
                    supabase.table("companies").update({
                        "keyword_hit_count": hit_count,
                        "keyword_group_count": hit_groups,
                        "keyword_scanned_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", cid).execute()
                except Exception as e:
                    logger.warning("Failed to update keyword hits for company %s: %s", cid, e)

            result_companies.append({
                "name": uc["name"],
                "inn": uc["inn"],
                "results": keyword_results,
            })
            done_count += 1
            on_company_done(done_count)

    return {"groups": groups, "companies": result_companies}


def generate_keyword_xlsx(scan_result: dict) -> io.BytesIO:
    """Generate a five-sheet XLSX from scan results."""
    groups = scan_result["groups"]
    anti_groups = scan_result.get("anti_groups", [])
    companies = scan_result["companies"]

    # ── Sheet 1: Quick_Summary ──
    qs_rows = []
    for company in companies:
        total_kw = 0
        total_groups = 0
        found_kw_names: list[str] = []
        row: dict = {"Company": company["name"], "INN": company["inn"]}
        for group in groups:
            group_kw_count = 0
            for kw in group["keywords"]:
                count = company["results"].get(kw, {}).get("count", 0)
                if count > 0:
                    group_kw_count += 1
                    total_kw += 1
                    found_kw_names.append(kw)
            row[group["name"]] = group_kw_count
            if group_kw_count > 0:
                total_groups += 1
        total_matches = sum(
            company["results"].get(kw, {}).get("count", 0)
            for g in groups for kw in g["keywords"]
        )
        row["Unique Keywords Found"] = total_kw
        row["Total Keyword Matches"] = total_matches
        row["Groups With Hits"] = total_groups
        row["Keywords Found"] = ", ".join(found_kw_names)

        anti_total_kw = 0
        anti_total_groups = 0
        anti_found_kw_names: list[str] = []
        for anti_group in anti_groups:
            anti_group_kw_count = 0
            for kw in anti_group["keywords"]:
                count = company.get("anti_results", {}).get(kw, {}).get("count", 0)
                if count > 0:
                    anti_group_kw_count += 1
                    anti_total_kw += 1
                    anti_found_kw_names.append(kw)
            row[f"Anti: {anti_group['name']}"] = anti_group_kw_count
            if anti_group_kw_count > 0:
                anti_total_groups += 1
        anti_total_matches = sum(
            company.get("anti_results", {}).get(kw, {}).get("count", 0)
            for g in anti_groups for kw in g["keywords"]
        )
        row["Anti Unique Keywords Found"] = anti_total_kw
        row["Anti Total Keyword Matches"] = anti_total_matches
        row["Anti Groups With Hits"] = anti_total_groups
        row["Anti Keywords Found"] = ", ".join(anti_found_kw_names)
        qs_rows.append(row)

    qs_df = pd.DataFrame(qs_rows)
    qs_cols = (
        ["Company", "INN",
         "Unique Keywords Found", "Total Keyword Matches", "Groups With Hits", "Keywords Found",
         "Anti Unique Keywords Found", "Anti Total Keyword Matches", "Anti Groups With Hits", "Anti Keywords Found"]
        + [g["name"] for g in groups]
        + [f"Anti: {g['name']}" for g in anti_groups]
    )
    qs_cols = [c for c in qs_cols if c in qs_df.columns]
    qs_df = qs_df[qs_cols]

    # ── Sheet 2: Summary (unchanged) ──
    summary_rows = []
    for company in companies:
        row = {"Company": company["name"], "INN": company["inn"]}
        for group in groups:
            group_found = 0
            for kw in group["keywords"]:
                count = company["results"].get(kw, {}).get("count", 0)
                row[kw] = count
                if count > 0:
                    group_found += 1
            row[f"{group['name']} (total)"] = group_found
        summary_rows.append(row)

    summary_df = pd.DataFrame(summary_rows)
    ordered_cols = ["Company", "INN"]
    for group in groups:
        for kw in group["keywords"]:
            ordered_cols.append(kw)
        ordered_cols.append(f"{group['name']} (total)")
    ordered_cols = [c for c in ordered_cols if c in summary_df.columns]
    summary_df = summary_df[ordered_cols]

    # ── Sheet 3: Details (unchanged) ──
    detail_rows = []
    for company in companies:
        for group in groups:
            for kw in group["keywords"]:
                matches = company["results"].get(kw, {}).get("sentences", [])
                if not matches:
                    continue
                combined = "\n\n".join(
                    f"[{'Job Posting' if m['source'] == 'posting' else 'News'} / {m['title']} / {m['field']}] {m['sentence']}"
                    for m in matches
                )
                posting_count = sum(1 for m in matches if m["source"] == "posting")
                news_count = sum(1 for m in matches if m["source"] == "news")
                detail_rows.append({
                    "Company": company["name"],
                    "INN": company["inn"],
                    "Keyword Group": group["name"],
                    "Keyword": kw,
                    "Total Matches": len(matches),
                    "From Postings": posting_count,
                    "From News": news_count,
                    "Sentences": combined,
                })

    details_df = pd.DataFrame(detail_rows) if detail_rows else pd.DataFrame(
        columns=["Company", "INN", "Keyword Group", "Keyword", "Total Matches", "From Postings", "From News", "Sentences"]
    )

    # ── Sheet 4: Anti_Summary ──
    anti_summary_rows = []
    for company in companies:
        row = {"Company": company["name"], "INN": company["inn"]}
        for anti_group in anti_groups:
            group_found = 0
            for kw in anti_group["keywords"]:
                count = company.get("anti_results", {}).get(kw, {}).get("count", 0)
                row[kw] = count
                if count > 0:
                    group_found += 1
            row[f"{anti_group['name']} (total)"] = group_found
        anti_summary_rows.append(row)

    if anti_groups:
        anti_summary_df = pd.DataFrame(anti_summary_rows)
        anti_ordered_cols = ["Company", "INN"]
        for anti_group in anti_groups:
            for kw in anti_group["keywords"]:
                anti_ordered_cols.append(kw)
            anti_ordered_cols.append(f"{anti_group['name']} (total)")
        anti_ordered_cols = [c for c in anti_ordered_cols if c in anti_summary_df.columns]
        anti_summary_df = anti_summary_df[anti_ordered_cols]
    else:
        anti_summary_df = pd.DataFrame(columns=["Company", "INN"])

    # ── Sheet 5: Anti_Details ──
    anti_detail_rows = []
    for company in companies:
        for anti_group in anti_groups:
            for kw in anti_group["keywords"]:
                matches = company.get("anti_results", {}).get(kw, {}).get("sentences", [])
                if not matches:
                    continue
                combined = "\n\n".join(
                    f"[{'Job Posting' if m['source'] == 'posting' else 'News'} / {m['title']} / {m['field']}] {m['sentence']}"
                    for m in matches
                )
                posting_count = sum(1 for m in matches if m["source"] == "posting")
                news_count = sum(1 for m in matches if m["source"] == "news")
                anti_detail_rows.append({
                    "Company": company["name"],
                    "INN": company["inn"],
                    "Keyword Group": anti_group["name"],
                    "Keyword": kw,
                    "Total Matches": len(matches),
                    "From Postings": posting_count,
                    "From News": news_count,
                    "Sentences": combined,
                })

    anti_details_df = pd.DataFrame(anti_detail_rows) if anti_detail_rows else pd.DataFrame(
        columns=["Company", "INN", "Keyword Group", "Keyword", "Total Matches", "From Postings", "From News", "Sentences"]
    )

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        qs_df.to_excel(writer, sheet_name="Quick_Summary", index=False)
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        details_df.to_excel(writer, sheet_name="Details", index=False)
        anti_summary_df.to_excel(writer, sheet_name="Anti_Summary", index=False)
        anti_details_df.to_excel(writer, sheet_name="Anti_Details", index=False)
    buffer.seek(0)
    return buffer


def derive_quick_summary_df(
    summary_df: pd.DataFrame,
    anti_summary_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Reconstruct Quick_Summary from Summary (and optionally Anti_Summary) sheets.

    Used by the download-with-contacts endpoint to rebuild Quick_Summary from the
    stored XLSX without re-running the scan.
    """
    total_cols = [c for c in summary_df.columns if c.endswith(" (total)")]
    kw_cols = [
        c for c in summary_df.columns
        if c not in ("Company", "INN") and not c.endswith(" (total)")
    ]

    anti_total_cols: list[str] = []
    anti_kw_cols: list[str] = []
    anti_lookup: dict = {}
    if anti_summary_df is not None:
        anti_total_cols = [c for c in anti_summary_df.columns if c.endswith(" (total)")]
        anti_kw_cols = [
            c for c in anti_summary_df.columns
            if c not in ("Company", "INN") and not c.endswith(" (total)")
        ]
        for _, r in anti_summary_df.iterrows():
            anti_lookup[(r["Company"], str(r["INN"]))] = r

    rows = []
    for _, r in summary_df.iterrows():
        total_kw = int(sum(1 for c in kw_cols if r[c] > 0))
        total_matches = int(sum(r[c] for c in kw_cols))
        total_groups = int(sum(1 for c in total_cols if r[c] > 0))
        found_kws = [c for c in kw_cols if r[c] > 0]
        row: dict = {
            "Company": r["Company"],
            "INN": str(r["INN"]),
            "Unique Keywords Found": total_kw,
            "Total Keyword Matches": total_matches,
            "Groups With Hits": total_groups,
            "Keywords Found": ", ".join(found_kws),
        }
        for tc in total_cols:
            row[tc[: -len(" (total)")]] = int(r[tc])

        if anti_summary_df is not None:
            ar = anti_lookup.get((r["Company"], str(r["INN"])))
            anti_total_kw = int(sum(1 for c in anti_kw_cols if ar[c] > 0)) if ar is not None else 0
            anti_total_matches = int(sum(ar[c] for c in anti_kw_cols)) if ar is not None else 0
            anti_total_groups = int(sum(1 for c in anti_total_cols if ar[c] > 0)) if ar is not None else 0
            anti_found_kws = [c for c in anti_kw_cols if ar is not None and ar[c] > 0]
            row["Anti Unique Keywords Found"] = anti_total_kw
            row["Anti Total Keyword Matches"] = anti_total_matches
            row["Anti Groups With Hits"] = anti_total_groups
            row["Anti Keywords Found"] = ", ".join(anti_found_kws)
            for tc in anti_total_cols:
                row[f"Anti: {tc[: -len(' (total)')]}"] = int(ar[tc]) if ar is not None else 0

        rows.append(row)

    group_names = [tc[: -len(" (total)")] for tc in total_cols]
    anti_group_names = [tc[: -len(" (total)")] for tc in anti_total_cols]
    base_cols = [
        "Company", "INN", "Unique Keywords Found", "Total Keyword Matches",
        "Groups With Hits", "Keywords Found",
    ]
    if anti_summary_df is not None:
        anti_stat_cols = [
            "Anti Unique Keywords Found", "Anti Total Keyword Matches",
            "Anti Groups With Hits", "Anti Keywords Found",
        ]
        cols = base_cols + anti_stat_cols + group_names + [f"Anti: {g}" for g in anti_group_names]
    else:
        cols = base_cols + group_names

    df = pd.DataFrame(rows)
    return df[[c for c in cols if c in df.columns]]
