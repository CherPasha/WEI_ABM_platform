import base64
import io
import logging
import time
import uuid
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from pydantic import BaseModel

from app.database import supabase
from app.models import CreateKeywordGroup, RenameKeywordGroup, CreateKeyword, CreateStopWord, UpdateProject, ContactScanSettings
from app.services.session_processor import process_session, resume_session
from app.services.contact_scanner import run_contact_scan
from app.services.keyword_scanner import scan_project_keywords, generate_keyword_xlsx
from app.services.keyword_parser import parse_keyword_xlsx, parse_stop_word_xlsx, parse_roles_xlsx

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="ABM Platform")


@app.on_event("startup")
async def _log_config():
    from app.config import settings
    def _mask(val: str) -> str:
        return (val[:4] + "…") if len(val) > 4 else ("SET" if val else "NOT SET")
    logging.getLogger(__name__).info(
        "Config check — SUPABASE=%s | OPENAI=%s | HUNTER=%s | YANDEX_KEY=%s | YANDEX_FOLDER=%s",
        _mask(settings.SUPABASE_URL),
        _mask(settings.OPENAI_API_KEY),
        _mask(settings.HUNTER_API_KEY),
        _mask(settings.YANDEX_SEARCH_API_KEY),
        _mask(settings.yandex_folder_id),
    )

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# ──────────────────────── HTML Pages ────────────────────────


@app.get("/", response_class=HTMLResponse)
async def projects_page(request: Request):
    return templates.TemplateResponse(request, "projects.html")


@app.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_detail_page(request: Request, project_id: str):
    return templates.TemplateResponse(request, "project.html", {"project_id": project_id})


# ──────────────────────── Projects API ────────────────────────


class CreateProject(BaseModel):
    name: str
    target_roles: list[str] = []


@app.get("/api/projects")
async def list_projects():
    result = (
        supabase.table("projects")
        .select("id, name, target_roles, created_at")
        .order("created_at", desc=True)
        .execute()
    )
    return result.data


@app.post("/api/projects")
async def create_project(body: CreateProject):
    result = supabase.table("projects").insert({
        "name": body.name,
        "target_roles": body.target_roles,
    }).execute()
    return result.data[0]


@app.get("/api/projects/{project_id}/details")
async def project_details(project_id: str):
    result = (
        supabase.table("projects")
        .select("id, name, target_roles, contact_scan_use_roles, contact_scan_keyword_only, created_at")
        .eq("id", project_id)
        .execute()
    )
    if not result.data:
        return {"error": "Project not found"}
    return result.data[0]


@app.put("/api/projects/{project_id}")
async def update_project(project_id: str, body: UpdateProject):
    result = supabase.table("projects").update({
        "target_roles": body.target_roles,
    }).eq("id", project_id).execute()
    if not result.data:
        return {"error": "Project not found"}
    return result.data[0]

@app.post("/api/projects/{project_id}/roles/import")
async def import_roles(project_id: str, file: UploadFile = File(...)):
    if not (file.filename or "").endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="File must be .xlsx")

    file_bytes = await file.read()
    try:
        parsed = parse_roles_xlsx(file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = supabase.table("projects").select("target_roles").eq("id", project_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Project not found")

    existing = result.data[0].get("target_roles") or []
    existing_lower = {r.lower() for r in existing}

    roles_added = 0
    roles_skipped = 0
    merged = list(existing)
    for role in parsed:
        if role.lower() in existing_lower:
            roles_skipped += 1
        else:
            merged.append(role)
            existing_lower.add(role.lower())
            roles_added += 1

    update_result = supabase.table("projects").update({"target_roles": merged}).eq("id", project_id).execute()
    if not update_result.data:
        raise HTTPException(status_code=500, detail="Failed to save roles")

    return {"added": roles_added, "skipped": roles_skipped}



@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    result = supabase.table("projects").delete().eq("id", project_id).execute()
    if not result.data:
        return {"error": "Project not found"}
    return {"status": "ok"}


# ──────────────────────── Contact Scan ────────────────────────


@app.post("/api/projects/{project_id}/contact-scan/start")
async def contact_scan_start(project_id: str, background_tasks: BackgroundTasks):
    # Check if a scan is already running
    running = (
        supabase.table("contact_scans")
        .select("id")
        .eq("project_id", project_id)
        .eq("status", "running")
        .execute()
    )
    if running.data:
        raise HTTPException(status_code=409, detail="A contact scan is already running for this project")

    # Snapshot current project settings
    project = supabase.table("projects").select("contact_scan_use_roles, contact_scan_keyword_only").eq("id", project_id).execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="Project not found")
    proj_settings = project.data[0]

    scan = supabase.table("contact_scans").insert({
        "project_id": project_id,
        "status": "running",
        "use_roles": proj_settings["contact_scan_use_roles"],
        "keyword_only": proj_settings["contact_scan_keyword_only"],
    }).execute()
    scan_id = scan.data[0]["id"]

    background_tasks.add_task(run_contact_scan, scan_id)
    return {"scan_id": scan_id}


@app.get("/api/projects/{project_id}/contact-scan/latest/status")
async def contact_scan_latest_status(project_id: str):
    result = (
        supabase.table("contact_scans")
        .select("status, use_roles, keyword_only, total_companies, hunter_done, enrichment_done, total_verification, verification_done, contacts_added, error_message, created_at")
        .eq("project_id", project_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return {"status": "none"}
    return result.data[0]


@app.put("/api/projects/{project_id}/contact-scan/settings")
async def update_contact_scan_settings(project_id: str, body: ContactScanSettings):
    result = supabase.table("projects").update({
        "contact_scan_use_roles": body.use_roles,
        "contact_scan_keyword_only": body.keyword_only,
    }).eq("id", project_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Project not found")
    return result.data[0]


# ──────────────────────── Upload (scoped to project) ────────────────────────


@app.post("/api/projects/{project_id}/sessions/upload")
async def upload_file(
    project_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    run_postings: bool = Form(True),
    run_news: bool = Form(True),
):
    if not file.filename.endswith((".xlsx", ".xls", ".csv")):
        return {"error": "Only .xlsx/.xls/.csv files are accepted"}

    file_bytes = await file.read()

    result = supabase.table("sessions").insert({
        "project_id": project_id,
        "filename": file.filename,
        "status": "uploading",
        "total_companies": 0,
        "run_postings": run_postings,
        "run_news": run_news,
    }).execute()

    session_id = result.data[0]["id"]

    background_tasks.add_task(process_session, session_id, file_bytes, file.filename)

    return {"session_id": session_id, "status": "uploading"}


# ──────────────────────── Sessions (scoped to project) ────────────────────────


@app.get("/api/projects/{project_id}/sessions")
async def list_sessions(project_id: str):
    result = (
        supabase.table("sessions")
        .select("id, filename, status, total_companies, names_done, postings_done, news_done, created_at")
        .eq("project_id", project_id)
        .order("created_at", desc=True)
        .limit(50)
        .execute()
    )
    return result.data


# ──────────────────────── Session Status ────────────────────────


@app.get("/api/sessions/{session_id}/status")
async def session_status(session_id: str):
    result = (
        supabase.table("sessions")
        .select("id, filename, status, error_message, total_companies, names_done, postings_done, news_done")
        .eq("id", session_id)
        .execute()
    )
    if not result.data:
        return {"error": "Session not found"}
    return result.data[0]


# ──────────────────────── Resume ────────────────────────


@app.post("/api/sessions/{session_id}/resume")
async def resume_session_endpoint(session_id: str, background_tasks: BackgroundTasks):
    result = (
        supabase.table("sessions")
        .select("id, status, total_companies")
        .eq("id", session_id)
        .execute()
    )
    if not result.data:
        return {"error": "Session not found"}
    session = result.data[0]
    if session["status"] == "completed":
        return {"error": "Session is already completed"}
    if session["total_companies"] == 0:
        return {"error": "Session has no companies to resume"}

    supabase.table("sessions").update({"status": "resuming", "error_message": None}).eq("id", session_id).execute()
    background_tasks.add_task(resume_session, session_id)
    return {"session_id": session_id, "status": "resuming"}


# ──────────────────────── Delete ────────────────────────


@app.delete("/api/projects/{project_id}/sessions/all")
async def delete_all_project_sessions(project_id: str):
    """Delete all sessions in a project (cascades to companies, postings, contacts)."""
    supabase.table("sessions").delete().eq("project_id", project_id).execute()
    return {"status": "ok"}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    result = supabase.table("sessions").delete().eq("id", session_id).execute()
    if not result.data:
        return {"error": "Session not found"}
    return {"status": "ok"}


# ──────────────────────── Downloads ────────────────────────


def _query_all_rows(table: str, session_id: str) -> list[dict]:
    """Fetch all rows for a session, paginating through Supabase's 1000-row limit."""
    all_rows = []
    offset = 0
    page_size = 1000
    while True:
        result = (
            supabase.table(table)
            .select("*")
            .eq("session_id", session_id)
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


@app.get("/api/sessions/{session_id}/postings/download")
async def download_postings(session_id: str):
    rows = _query_all_rows("postings", session_id)
    if not rows:
        return {"error": "No postings found for this session"}

    df = pd.DataFrame(rows)
    for col in ("raw_data", "id", "session_id", "company_id"):
        if col in df.columns:
            df = df.drop(columns=[col])

    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, engine="openpyxl")
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=postings_{session_id[:8]}.xlsx"},
    )


@app.get("/api/sessions/{session_id}/news/download")
async def download_news(session_id: str):
    rows = _query_all_rows("news_articles", session_id)
    if not rows:
        return {"error": "No news articles found for this session"}

    companies_result = (
        supabase.table("companies")
        .select("id, legal_name")
        .eq("session_id", session_id)
        .execute()
    )
    company_name_by_id = {c["id"]: c["legal_name"] for c in companies_result.data}

    df = pd.DataFrame(rows)
    df.insert(0, "company_name", df["company_id"].map(company_name_by_id))

    for col in ("id", "session_id", "company_id", "raw_data"):
        if col in df.columns:
            df = df.drop(columns=[col])

    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, engine="openpyxl")
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=news_{session_id[:8]}.xlsx"},
    )


@app.get("/api/projects/{project_id}/contacts/download")
async def download_project_contacts(project_id: str):
    # 1. Get all sessions for this project
    sessions_result = (
        supabase.table("sessions")
        .select("id, filename")
        .eq("project_id", project_id)
        .execute()
    )
    sessions = sessions_result.data
    if not sessions:
        raise HTTPException(status_code=404, detail="No sessions found for this project")

    session_ids = [s["id"] for s in sessions]
    session_filename: dict[str, str] = {s["id"]: s["filename"] for s in sessions}

    # 2. Fetch all companies across those sessions
    all_companies: list[dict] = []
    for i in range(0, len(session_ids), 200):
        batch = session_ids[i:i + 200]
        offset = 0
        while True:
            rows = (
                supabase.table("companies")
                .select("id, legal_name, inn, session_id, keyword_hit_count, keyword_group_count")
                .in_("session_id", batch)
                .range(offset, offset + 999)
                .execute()
            ).data
            all_companies.extend(rows)
            if len(rows) < 1000:
                break
            offset += 1000

    if not all_companies:
        raise HTTPException(status_code=404, detail="No companies found in this project")

    company_ids = [c["id"] for c in all_companies]
    company_meta: dict[str, dict] = {
        c["id"]: {
            "Company": c.get("legal_name", ""),
            "INN": c.get("inn", ""),
            "Session": session_filename.get(c.get("session_id", ""), ""),
            "Keywords Found": c.get("keyword_hit_count", 0),
            "Keyword Groups Found": c.get("keyword_group_count", 0),
            "Contacts Found": 0,
        }
        for c in all_companies
    }

    # 3. Fetch all contacts with contact_scan_id set, filtered by verification status
    _KEEP_STATUSES = {"valid", "accept_all"}
    all_contacts: list[dict] = []
    for i in range(0, len(company_ids), 200):
        batch = company_ids[i:i + 200]
        offset = 0
        while True:
            rows = (
                supabase.table("contacts")
                .select("*")
                .in_("company_id", batch)
                .not_.is_("contact_scan_id", "null")
                .range(offset, offset + 999)
                .execute()
            ).data
            all_contacts.extend(rows)
            if len(rows) < 1000:
                break
            offset += 1000

    filtered = [
        c for c in all_contacts
        if c.get("email_status") is None or c.get("email_status") in _KEEP_STATUSES
    ]

    if not filtered:
        raise HTTPException(status_code=404, detail="No contacts found. Run a contact scan first.")

    # 4. Build Sheet 1 — Companies
    for c in filtered:
        cid = c.get("company_id")
        if cid in company_meta:
            company_meta[cid]["Contacts Found"] += 1

    sheet1_rows = [v for v in company_meta.values() if v["Contacts Found"] > 0]
    sheet1_rows.sort(key=lambda x: x["Contacts Found"], reverse=True)
    sheet1_df = pd.DataFrame(sheet1_rows, columns=[
        "Company", "INN", "Session", "Contacts Found", "Keywords Found", "Keyword Groups Found"
    ])

    # 5. Build Sheet 2 — Contacts
    for c in filtered:
        cid = c.get("company_id")
        c["company_name"] = company_meta.get(cid, {}).get("Company", "")

    sheet2_df = pd.DataFrame(filtered)
    for col in ("id", "session_id", "company_id", "contact_scan_id"):
        if col in sheet2_df.columns:
            sheet2_df = sheet2_df.drop(columns=[col])

    # company_name first
    cols = ["company_name"] + [c for c in sheet2_df.columns if c != "company_name"]
    sheet2_df = sheet2_df[[c for c in cols if c in sheet2_df.columns]]
    if "company_name" in sheet2_df.columns and "last_name" in sheet2_df.columns:
        sheet2_df = sheet2_df.sort_values(["company_name", "last_name"], na_position="last")

    # 6. Write two-sheet xlsx
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        sheet1_df.to_excel(writer, sheet_name="Companies", index=False)
        sheet2_df.to_excel(writer, sheet_name="Contacts", index=False)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=contacts_{project_id[:8]}.xlsx"},
    )


# ──────────────────────── Keyword Groups ────────────────────────


@app.get("/api/projects/{project_id}/keyword-groups")
async def list_keyword_groups(project_id: str):
    groups = (
        supabase.table("keyword_groups")
        .select("id, name, created_at")
        .eq("project_id", project_id)
        .order("created_at")
        .execute()
    ).data

    group_ids = [g["id"] for g in groups]
    keywords = []
    # Fetch in chunks of 50 IDs to stay under URL length limits,
    # and paginate each chunk to bypass the 1000-row default cap.
    for i in range(0, len(group_ids), 50):
        chunk = group_ids[i:i + 50]
        offset = 0
        while True:
            batch = (
                supabase.table("keywords")
                .select("id, group_id, keyword")
                .in_("group_id", chunk)
                .range(offset, offset + 999)
                .execute()
            ).data
            keywords.extend(batch)
            if len(batch) < 1000:
                break
            offset += 1000

    for g in groups:
        g["keywords"] = [k for k in keywords if k["group_id"] == g["id"]]

    return groups


@app.post("/api/projects/{project_id}/keyword-groups")
async def create_keyword_group(project_id: str, body: CreateKeywordGroup):
    result = supabase.table("keyword_groups").insert({
        "project_id": project_id,
        "name": body.name,
    }).execute()
    return result.data[0]


@app.put("/api/keyword-groups/{group_id}")
async def rename_keyword_group(group_id: str, body: RenameKeywordGroup):
    result = supabase.table("keyword_groups").update({"name": body.name}).eq("id", group_id).execute()
    if not result.data:
        return {"error": "Group not found"}
    return result.data[0]


@app.delete("/api/keyword-groups/{group_id}")
async def delete_keyword_group(group_id: str):
    result = supabase.table("keyword_groups").delete().eq("id", group_id).execute()
    if not result.data:
        return {"error": "Group not found"}
    return {"status": "ok"}


# ──────────────────────── Keywords ────────────────────────


@app.post("/api/keyword-groups/{group_id}/keywords")
async def add_keyword(group_id: str, body: CreateKeyword):
    result = supabase.table("keywords").insert({
        "group_id": group_id,
        "keyword": body.keyword,
    }).execute()
    return result.data[0]


@app.delete("/api/keywords/{keyword_id}")
async def delete_keyword(keyword_id: str):
    result = supabase.table("keywords").delete().eq("id", keyword_id).execute()
    if not result.data:
        return {"error": "Keyword not found"}
    return {"status": "ok"}


@app.post("/api/projects/{project_id}/keyword-groups/import")
async def import_keyword_groups(project_id: str, file: UploadFile = File(...)):
    if not (file.filename or "").endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="File must be .xlsx")

    file_bytes = await file.read()
    try:
        parsed = parse_keyword_xlsx(file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    existing_groups = (
        supabase.table("keyword_groups")
        .select("id, name")
        .eq("project_id", project_id)
        .execute()
    ).data
    group_by_name = {g["name"]: g["id"] for g in existing_groups}

    # Pass 1 — resolve all groups (create missing ones in a single batch insert)
    group_id_by_name: dict[str, str] = {}  # name → id

    # Preserve order while deduplicating group names
    seen: set[str] = set()
    ordered_group_names: list[str] = []
    for row in parsed:
        if row["group"] not in seen:
            seen.add(row["group"])
            ordered_group_names.append(row["group"])

    existing_names = [n for n in ordered_group_names if n in group_by_name]
    new_names = [n for n in ordered_group_names if n not in group_by_name]

    for name in existing_names:
        group_id_by_name[name] = group_by_name[name]

    if new_names:
        result = supabase.table("keyword_groups").insert([
            {"project_id": project_id, "name": name} for name in new_names
        ]).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create keyword groups")
        for g in result.data:
            group_id_by_name[g["name"]] = g["id"]

    groups_created = len(new_names)
    groups_updated = len(existing_names)

    # Between passes — batch-fetch all existing keywords for all resolved groups
    # Chunk by 50 IDs to avoid URL length limits; paginate to bypass 1000-row cap.
    all_group_ids = list(group_id_by_name.values())
    all_existing_kws = []
    for i in range(0, len(all_group_ids), 50):
        chunk = all_group_ids[i:i + 50]
        offset = 0
        while True:
            batch = (
                supabase.table("keywords")
                .select("group_id, keyword")
                .in_("group_id", chunk)
                .range(offset, offset + 999)
                .execute()
            ).data
            all_existing_kws.extend(batch)
            if len(batch) < 1000:
                break
            offset += 1000

    # Build per-group keyword sets (lowercase)
    existing_kws_by_group: dict[str, set[str]] = {gid: set() for gid in all_group_ids}
    for kw_row in all_existing_kws:
        existing_kws_by_group[kw_row["group_id"]].add(kw_row["keyword"].lower())

    # Pass 2 — insert missing keywords
    keywords_added = 0
    keywords_skipped = 0

    for row in parsed:
        group_name = row["group"]
        group_id = group_id_by_name[group_name]
        existing_set = existing_kws_by_group[group_id]

        to_insert = []
        for kw in row["keywords"]:
            if kw.lower() in existing_set:
                keywords_skipped += 1
            else:
                to_insert.append({"group_id": group_id, "keyword": kw})
                existing_set.add(kw.lower())
                keywords_added += 1
        if to_insert:
            supabase.table("keywords").insert(to_insert).execute()

    return {
        "groups_created": groups_created,
        "groups_updated": groups_updated,
        "keywords_added": keywords_added,
        "keywords_skipped": keywords_skipped,
    }


# ──────────────────────── Stop Words ────────────────────────


@app.get("/api/projects/{project_id}/stop-words")
async def list_stop_words(project_id: str):
    result = (
        supabase.table("stop_words")
        .select("id, word, created_at")
        .eq("project_id", project_id)
        .order("created_at")
        .execute()
    )
    return result.data


@app.post("/api/projects/{project_id}/stop-words")
async def add_stop_word(project_id: str, body: CreateStopWord):
    word = body.word.strip()
    if not word:
        raise HTTPException(status_code=400, detail="Word cannot be empty")
    escaped_word = word.replace("%", r"\%").replace("_", r"\_")
    existing = (
        supabase.table("stop_words")
        .select("id")
        .eq("project_id", project_id)
        .ilike("word", escaped_word)
        .execute()
    )
    if existing.data:
        raise HTTPException(status_code=409, detail="Stop word already exists")
    result = supabase.table("stop_words").insert({
        "project_id": project_id,
        "word": word,
    }).execute()
    return result.data[0]


@app.delete("/api/stop-words/{word_id}")
async def delete_stop_word(word_id: str):
    result = supabase.table("stop_words").delete().eq("id", word_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Stop word not found")
    return {"status": "ok"}


@app.post("/api/projects/{project_id}/stop-words/import")
async def import_stop_words(project_id: str, file: UploadFile = File(...)):
    if not (file.filename or "").endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="File must be .xlsx")

    file_bytes = await file.read()
    try:
        parsed = parse_stop_word_xlsx(file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Paginate past Supabase's 1000-row default limit
    existing_set: set[str] = set()
    offset = 0
    while True:
        page = (
            supabase.table("stop_words")
            .select("word")
            .eq("project_id", project_id)
            .range(offset, offset + 999)
            .execute()
        ).data
        if not page:
            break
        for r in page:
            existing_set.add(r["word"].lower())
        if len(page) < 1000:
            break
        offset += 1000

    to_insert = []
    words_added = 0
    words_skipped = 0
    for word in parsed:
        if word.lower() in existing_set:
            words_skipped += 1
        else:
            to_insert.append({"project_id": project_id, "word": word})
            existing_set.add(word.lower())
            words_added += 1

    batch_size = 500
    for i in range(0, len(to_insert), batch_size):
        supabase.table("stop_words").insert(to_insert[i:i + batch_size]).execute()

    return {"words_added": words_added, "words_skipped": words_skipped}


# ──────────────────────── Keyword Scan ────────────────────────

# In-memory job store: job_id -> {status, result, error, ts}
_scan_jobs: dict[str, dict] = {}
_SCAN_JOB_TTL = 600  # seconds before an unclaimed job is discarded


def _run_scan_task(job_id: str, project_id: str) -> None:
    try:
        scan_result = scan_project_keywords(project_id)
        buffer = generate_keyword_xlsx(scan_result)
        data = buffer.getvalue()
        # Persist to DB so Export tab can fetch it later
        encoded = base64.b64encode(data).decode()
        supabase.table("projects").update(
            {"keyword_scan_result": encoded}
        ).eq("id", project_id).execute()
        _scan_jobs[job_id] = {"status": "done", "result": data, "error": None, "ts": time.time()}
    except ValueError as e:
        _scan_jobs[job_id] = {"status": "error", "result": None, "error": str(e), "ts": time.time()}
    except Exception as e:
        logging.getLogger(__name__).exception("Keyword scan failed for project %s", project_id)
        _scan_jobs[job_id] = {"status": "error", "result": None, "error": "Scan failed unexpectedly", "ts": time.time()}


@app.post("/api/projects/{project_id}/keyword-scan/start")
async def keyword_scan_start(project_id: str, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    _scan_jobs[job_id] = {"status": "running", "result": None, "error": None, "ts": time.time()}
    background_tasks.add_task(_run_scan_task, job_id, project_id)
    return {"job_id": job_id}


@app.get("/api/projects/{project_id}/keyword-scan/{job_id}/status")
async def keyword_scan_job_status(project_id: str, job_id: str):
    now = time.time()
    stale = [jid for jid, j in list(_scan_jobs.items()) if now - j["ts"] > _SCAN_JOB_TTL]
    for jid in stale:
        _scan_jobs.pop(jid, None)

    job = _scan_jobs.get(job_id)
    if not job:
        return {"status": "not_found"}
    return {"status": job["status"], "error": job.get("error")}


@app.get("/api/projects/{project_id}/keyword-scan/{job_id}/download")
async def keyword_scan_job_download(project_id: str, job_id: str):
    job = _scan_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail=f"Job not ready: {job['status']}")
    data = job.pop("result")
    _scan_jobs.pop(job_id, None)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=keyword_analysis_{project_id[:8]}.xlsx"},
    )


@app.get("/api/projects/{project_id}/keyword-scan/status")
async def keyword_scan_db_status(project_id: str):
    """Returns whether a saved keyword scan result exists in the DB."""
    result = (
        supabase.table("projects")
        .select("keyword_scan_result")
        .eq("id", project_id)
        .execute()
    )
    if not result.data:
        return {"has_result": False}
    return {"has_result": result.data[0].get("keyword_scan_result") is not None}


@app.get("/api/projects/{project_id}/keyword-scan/download")
async def keyword_scan_db_download(project_id: str):
    """Streams the saved keyword scan XLSX from the DB."""
    result = (
        supabase.table("projects")
        .select("keyword_scan_result")
        .eq("id", project_id)
        .execute()
    )
    if not result.data or result.data[0].get("keyword_scan_result") is None:
        raise HTTPException(status_code=404, detail="No keyword scan result saved for this project")
    encoded = result.data[0]["keyword_scan_result"]
    data = base64.b64decode(encoded)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=keyword_analysis_{project_id[:8]}.xlsx"
        },
    )
