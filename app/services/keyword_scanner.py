import io
import re
import logging
import time
from datetime import datetime, timezone
from typing import Callable

import pandas as pd

from app.database import supabase

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


def _fetch_all(table: str, column: str, value: str, select: str = "*") -> list[dict]:
    """Fetch all rows matching column=value, paginating through 1000-row limit."""
    all_rows = []
    offset = 0
    page_size = 1000
    while True:
        result = (
            supabase.table(table)
            .select(select)
            .eq(column, value)
            .range(offset, offset + page_size - 1)
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


def _fetch_all_in(table: str, column: str, values: list[str], select: str = "*") -> list[dict]:
    """Fetch all rows where column IN (values), paginating in batches."""
    if not values:
        return []
    all_rows = []
    batch_size = 200
    for i in range(0, len(values), batch_size):
        batch_values = values[i: i + batch_size]
        offset = 0
        page_size = 1000
        while True:
            result = (
                supabase.table(table)
                .select(select)
                .in_(column, batch_values)
                .range(offset, offset + page_size - 1)
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


def _compute_company_hits(groups: list[dict], keyword_results: dict) -> tuple[int, int]:
    """Compute total keyword hit count and number of groups with at least one hit."""
    hit_count = sum(kd.get("count", 0) for kd in keyword_results.values())
    hit_groups = sum(
        1 for g in groups
        if any(keyword_results.get(kw, {}).get("count", 0) > 0 for kw in g["keywords"])
    )
    return hit_count, hit_groups


def scan_project_keywords(project_id: str) -> dict:
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
        select="id, legal_name, inn, known_names"
    )

    # 4. Deduplicate companies by inn (fallback: lower(legal_name))
    dedup: dict[str, dict] = {}
    for c in all_companies:
        inn = (c.get("inn") or "").strip()
        legal_name = c.get("legal_name", "")
        key = f"inn:{inn}" if inn else f"name:{legal_name.strip().lower()}"

        if key not in dedup:
            dedup[key] = {"name": legal_name, "inn": inn, "company_ids": []}
        dedup[key]["company_ids"].append(c["id"])

    unique_companies = list(dedup.values())

    # 5. Process companies in batches — fetch postings/news per batch so peak
    #    memory stays bounded regardless of project size.
    _COMPANY_BATCH = 1

    result_companies = []
    for batch_start in range(0, len(unique_companies), _COMPANY_BATCH):
        # Yield GIL between batches so the asyncio event loop can process
        # status-poll requests and avoid proxy 504 timeouts.
        time.sleep(0)

        batch = unique_companies[batch_start:batch_start + _COMPANY_BATCH]
        batch_cids = [cid for uc in batch for cid in uc["company_ids"]]

        batch_postings = _fetch_all_in(
            "postings", "company_id", batch_cids,
            select="company_id, title, snippet_requirement, snippet_responsibility"
        )
        postings_by_company: dict[str, list] = {}
        for p in batch_postings:
            postings_by_company.setdefault(p["company_id"], []).append(p)

        batch_news = _fetch_all_in(
            "news_articles", "company_id", batch_cids,
            select="company_id, title, snippet, full_text"
        )
        news_by_company: dict[str, list] = {}
        for a in batch_news:
            news_by_company.setdefault(a["company_id"], []).append(a)

        # 6. For each company x keyword, search postings and news
        for uc in batch:
            time.sleep(0)  # yield GIL once per company so the event loop can serve status polls
            company_postings = []
            company_news = []
            for cid in uc["company_ids"]:
                company_postings.extend(postings_by_company.get(cid, []))
                company_news.extend(news_by_company.get(cid, []))

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
                    }).eq("id", cid).execute()
                except Exception as e:
                    logger.warning("Failed to update keyword hits for company %s: %s", cid, e)

            result_companies.append({
                "name": uc["name"],
                "inn": uc["inn"],
                "results": keyword_results,
            })

    return {"groups": groups, "companies": result_companies}


def generate_keyword_xlsx(scan_result: dict) -> io.BytesIO:
    """Generate a three-sheet XLSX from scan results."""
    groups = scan_result["groups"]
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
        row["Total Keywords Found"] = total_kw
        row["Groups With Hits"] = total_groups
        row["Keywords Found"] = ", ".join(found_kw_names)
        qs_rows.append(row)

    qs_df = pd.DataFrame(qs_rows)
    qs_cols = ["Company", "INN", "Total Keywords Found", "Groups With Hits", "Keywords Found"] + [
        g["name"] for g in groups
    ]
    qs_cols = [c for c in qs_cols if c in qs_df.columns]
    qs_df = qs_df[qs_cols]

    # ── Sheet 2: Summary ──
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

    # ── Sheet 3: Details ──
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

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        qs_df.to_excel(writer, sheet_name="Quick_Summary", index=False)
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        details_df.to_excel(writer, sheet_name="Details", index=False)
    buffer.seek(0)
    return buffer


def derive_quick_summary_df(summary_df: pd.DataFrame) -> pd.DataFrame:
    """Reconstruct Quick_Summary rows from a parsed Summary sheet DataFrame.

    Columns ending with ' (total)' are group total columns — strip the suffix
    for the display name, use the value as the per-group keyword count.
    All other non-Company/INN columns are individual keyword columns.
    """
    total_cols = [c for c in summary_df.columns if c.endswith(" (total)")]
    kw_cols = [
        c for c in summary_df.columns
        if c not in ("Company", "INN") and not c.endswith(" (total)")
    ]

    rows = []
    for _, r in summary_df.iterrows():
        total_kw = int(sum(1 for c in kw_cols if r[c] > 0))
        total_groups = int(sum(1 for c in total_cols if r[c] > 0))
        found_kws = [c for c in kw_cols if r[c] > 0]
        row: dict = {
            "Company": r["Company"],
            "INN": str(r["INN"]),
            "Total Keywords Found": total_kw,
            "Groups With Hits": total_groups,
            "Keywords Found": ", ".join(found_kws),
        }
        for tc in total_cols:
            group_name = tc[: -len(" (total)")]
            row[group_name] = int(r[tc])
        rows.append(row)

    group_names = [tc[: -len(" (total)")] for tc in total_cols]
    cols = ["Company", "INN", "Total Keywords Found", "Groups With Hits", "Keywords Found"] + group_names
    df = pd.DataFrame(rows)
    return df[[c for c in cols if c in df.columns]]
