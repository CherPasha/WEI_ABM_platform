import io
import logging
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from pydantic import BaseModel

from app.database import supabase
from app.models import CreateKeywordGroup, RenameKeywordGroup, CreateKeyword, CreateStopWord, UpdateProject
from app.services.session_processor import process_session, resume_session
from app.services.keyword_scanner import scan_project_keywords, generate_keyword_xlsx
from app.services.keyword_parser import parse_keyword_xlsx, parse_stop_word_xlsx

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="ABM Platform")

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
        .select("id, name, target_roles, created_at")
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


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    result = supabase.table("projects").delete().eq("id", project_id).execute()
    if not result.data:
        return {"error": "Project not found"}
    return {"status": "ok"}


# ──────────────────────── Upload (scoped to project) ────────────────────────


@app.post("/api/projects/{project_id}/sessions/upload")
async def upload_file(
    project_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    run_postings: bool = Form(True),
    run_news: bool = Form(True),
    run_contacts: bool = Form(True),
    run_enrichment: bool = Form(True),
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
        "run_contacts": run_contacts,
        "run_enrichment": run_enrichment,
    }).execute()

    session_id = result.data[0]["id"]

    background_tasks.add_task(process_session, session_id, file_bytes, file.filename)

    return {"session_id": session_id, "status": "uploading"}


# ──────────────────────── Sessions (scoped to project) ────────────────────────


@app.get("/api/projects/{project_id}/sessions")
async def list_sessions(project_id: str):
    result = (
        supabase.table("sessions")
        .select("id, filename, status, total_companies, names_done, postings_done, news_done, contacts_done, enrichment_done, created_at")
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
        .select("id, filename, status, error_message, total_companies, names_done, postings_done, news_done, contacts_done, enrichment_done")
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


@app.get("/api/sessions/{session_id}/contacts/download")
async def download_contacts(session_id: str):
    rows = _query_all_rows("contacts", session_id)
    if not rows:
        return {"error": "No contacts found for this session"}

    # Fetch company names for this session to join in
    companies_result = (
        supabase.table("companies")
        .select("id, legal_name")
        .eq("session_id", session_id)
        .execute()
    )
    company_name_by_id = {c["id"]: c["legal_name"] for c in companies_result.data}

    df = pd.DataFrame(rows)
    df.insert(0, "company_name", df["company_id"].map(company_name_by_id))

    for col in ("id", "session_id", "company_id"):
        if col in df.columns:
            df = df.drop(columns=[col])

    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, engine="openpyxl")
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=contacts_{session_id[:8]}.xlsx"},
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
    if group_ids:
        keywords = (
            supabase.table("keywords")
            .select("id, group_id, keyword")
            .in_("group_id", group_ids)
            .execute()
        ).data

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

    # Pass 1 — resolve all groups (create if missing), collect group IDs
    group_id_by_name: dict[str, str] = {}  # name → id
    groups_created = 0
    groups_updated = 0

    for row in parsed:
        group_name = row["group"]
        if group_name in group_id_by_name:
            # already resolved in a previous row with the same group name
            continue
        if group_name in group_by_name:
            group_id_by_name[group_name] = group_by_name[group_name]
            groups_updated += 1
        else:
            result = supabase.table("keyword_groups").insert({
                "project_id": project_id,
                "name": group_name,
            }).execute()
            if not result.data:
                raise HTTPException(status_code=500, detail=f"Failed to create group '{group_name}'")
            group_id = result.data[0]["id"]
            group_id_by_name[group_name] = group_id
            groups_created += 1

    # Between passes — batch-fetch all existing keywords for all resolved groups
    all_group_ids = list(group_id_by_name.values())
    all_existing_kws = []
    if all_group_ids:
        all_existing_kws = (
            supabase.table("keywords")
            .select("group_id, keyword")
            .in_("group_id", all_group_ids)
            .execute()
        ).data

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
    existing = (
        supabase.table("stop_words")
        .select("id")
        .eq("project_id", project_id)
        .ilike("word", word)
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

    if to_insert:
        supabase.table("stop_words").insert(to_insert).execute()

    return {"words_added": words_added, "words_skipped": words_skipped}


# ──────────────────────── Keyword Scan ────────────────────────


@app.get("/api/projects/{project_id}/keyword-scan/download")
async def keyword_scan_download(project_id: str):
    try:
        scan_result = scan_project_keywords(project_id)
    except ValueError as e:
        return {"error": str(e)}

    buffer = generate_keyword_xlsx(scan_result)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=keyword_analysis_{project_id[:8]}.xlsx"},
    )
