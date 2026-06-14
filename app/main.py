import base64
import io
import logging
import uuid
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from pydantic import BaseModel

from app.database import supabase
from app.models import CreateKeywordGroup, RenameKeywordGroup, CreateKeyword, CreateStopWord, UpdateProject, ContactScanSettings, ImportSession
from app.services.session_processor import process_session, resume_session
from app.services.contact_scanner import run_contact_scan
from app.services.keyword_scanner import scan_project_keywords, generate_keyword_xlsx, derive_quick_summary_df, ScanCancelledError
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


@app.on_event("startup")
async def recover_keyword_scans() -> None:
    """Re-enqueue any scan that was running when the server last stopped."""
    try:
        result = (
            supabase.table("keyword_scans")
            .select("project_id, started_at")
            .eq("status", "running")
            .execute()
        )
        loop = asyncio.get_event_loop()
        for row in (result.data or []):
            started_at = datetime.fromisoformat(row["started_at"])
            loop.run_in_executor(None, _run_scan_task, row["project_id"], started_at)
            logging.getLogger(__name__).info(
                "Recovering interrupted keyword scan for project %s", row["project_id"]
            )
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to recover interrupted keyword scans on startup"
        )

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_KEEP_STATUSES = {"valid", "accept_all", "risky"}


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
async def delete_project(project_id: str, force: bool = False):
    if not force:
        sessions = supabase.table("sessions").select("id").eq("project_id", project_id).execute()
        session_ids = [s["id"] for s in (sessions.data or [])]
        dep_project_ids: set[str] = set()
        for sid in session_ids:
            deps = (
                supabase.table("sessions")
                .select("project_id")
                .eq("source_session_id", sid)
                .execute()
            )
            for row in (deps.data or []):
                if row["project_id"] != project_id:
                    dep_project_ids.add(row["project_id"])
        if dep_project_ids:
            names = []
            for pid in dep_project_ids:
                p = supabase.table("projects").select("name").eq("id", pid).execute()
                if p.data:
                    names.append(p.data[0]["name"])
            raise HTTPException(
                status_code=409,
                detail={"message": "Project sessions are imported by other projects", "projects": names},
            )
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


@app.post("/api/projects/{project_id}/contact-scan/cancel")
async def contact_scan_cancel(project_id: str):
    result = (
        supabase.table("contact_scans")
        .select("id, status")
        .eq("project_id", project_id)
        .eq("status", "running")
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=409, detail="No running contact scan found")
    scan_id = result.data[0]["id"]
    supabase.table("contact_scans").update({"status": "cancelling"}).eq("id", scan_id).execute()
    return {}


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


@app.get("/api/projects/{project_id}/sessions/completed")
async def list_completed_sessions(project_id: str, importing_into: Optional[str] = None):
    result = (
        supabase.table("sessions")
        .select("id, filename, created_at, total_companies")
        .eq("project_id", project_id)
        .eq("status", "completed")
        .order("created_at", desc=True)
        .execute()
    )
    sessions = result.data or []

    if importing_into:
        already = (
            supabase.table("sessions")
            .select("source_session_id")
            .eq("project_id", importing_into)
            .eq("type", "imported")
            .execute()
        )
        imported_ids = {r["source_session_id"] for r in (already.data or []) if r.get("source_session_id")}
        sessions = [s for s in sessions if s["id"] not in imported_ids]

    return sessions


@app.post("/api/projects/{project_id}/sessions/import")
async def import_session(project_id: str, body: ImportSession):
    # 1. Validate source session
    src_result = (
        supabase.table("sessions")
        .select("id, project_id, filename, status, total_companies")
        .eq("id", body.source_session_id)
        .execute()
    )
    if not src_result.data:
        raise HTTPException(status_code=404, detail="Source session not found")
    src = src_result.data[0]

    if src["project_id"] == project_id:
        raise HTTPException(status_code=400, detail="Cannot import from the same project")

    if src["status"] != "completed":
        raise HTTPException(status_code=400, detail="Source session must be completed")

    # 2. Duplicate guard
    dup = (
        supabase.table("sessions")
        .select("id")
        .eq("project_id", project_id)
        .eq("source_session_id", body.source_session_id)
        .execute()
    )
    if dup.data:
        raise HTTPException(status_code=409, detail="This session is already imported into this project")

    # 3. Snapshot source project name
    proj_result = (
        supabase.table("projects")
        .select("name")
        .eq("id", src["project_id"])
        .execute()
    )
    source_project_name = proj_result.data[0]["name"] if proj_result.data else "Unknown"

    # 4. Create imported session
    imported = supabase.table("sessions").insert({
        "project_id": project_id,
        "filename": src["filename"],
        "status": "completed",
        "type": "imported",
        "source_session_id": body.source_session_id,
        "source_project_name": source_project_name,
        "source_session_filename": src["filename"],
        "total_companies": src["total_companies"],
    }).execute()
    imported_session_id = imported.data[0]["id"]

    # 5. Batch-copy company metadata (no postings/news)
    all_source_companies: list[dict] = []
    offset = 0
    while True:
        rows = (
            supabase.table("companies")
            .select("id, legal_name, inn, kpp, ogrn, website_url, ceo_name, revenue, known_names")
            .eq("session_id", body.source_session_id)
            .range(offset, offset + 999)
            .execute()
        ).data
        all_source_companies.extend(rows)
        if len(rows) < 1000:
            break
        offset += 1000

    batch_size = 200
    for i in range(0, len(all_source_companies), batch_size):
        batch = all_source_companies[i:i + batch_size]
        supabase.table("companies").insert([
            {
                "session_id": imported_session_id,
                "source_company_id": c["id"],
                "legal_name": c.get("legal_name"),
                "inn": c.get("inn"),
                "kpp": c.get("kpp"),
                "ogrn": c.get("ogrn"),
                "website_url": c.get("website_url"),
                "ceo_name": c.get("ceo_name"),
                "revenue": c.get("revenue"),
                "known_names": c.get("known_names"),
            }
            for c in batch
        ]).execute()

    return imported.data[0]


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
        .select("id, filename, status, total_companies, names_done, postings_done, news_done, created_at, type, source_session_id, source_project_name, source_session_filename")
        .eq("project_id", project_id)
        .order("created_at", desc=True)
        .limit(50)
        .execute()
    )
    return result.data


@app.get("/api/sessions/{session_id}/dependents")
async def session_dependents(session_id: str):
    result = (
        supabase.table("sessions")
        .select("id, project_id, source_project_name, source_session_filename")
        .eq("source_session_id", session_id)
        .execute()
    )
    dependents = []
    for row in (result.data or []):
        proj = supabase.table("projects").select("name").eq("id", row["project_id"]).execute()
        project_name = proj.data[0]["name"] if proj.data else row.get("source_project_name", "Unknown")
        dependents.append({
            "project_name": project_name,
            "session_filename": row.get("source_session_filename", ""),
        })
    return dependents


@app.get("/api/projects/{project_id}/dependents")
async def project_dependents(project_id: str):
    sessions_result = (
        supabase.table("sessions")
        .select("id, filename")
        .eq("project_id", project_id)
        .execute()
    )
    session_ids = [s["id"] for s in (sessions_result.data or [])]
    if not session_ids:
        return []

    all_dependents = []
    for sid in session_ids:
        src_filename = next((s["filename"] for s in (sessions_result.data or []) if s["id"] == sid), "")
        deps = (
            supabase.table("sessions")
            .select("project_id, source_session_filename")
            .eq("source_session_id", sid)
            .execute()
        )
        for row in (deps.data or []):
            proj = supabase.table("projects").select("name").eq("id", row["project_id"]).execute()
            project_name = proj.data[0]["name"] if proj.data else "Unknown"
            all_dependents.append({
                "project_name": project_name,
                "source_session_filename": src_filename,
                "session_filename": row.get("source_session_filename", ""),
            })
    return all_dependents


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


@app.post("/api/sessions/{session_id}/cancel")
async def cancel_session(session_id: str):
    result = (
        supabase.table("sessions")
        .select("status")
        .eq("id", session_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Session not found")
    active = {"resolving_names", "finding_postings", "finding_news", "resuming", "parsing"}
    if result.data[0].get("status") not in active:
        raise HTTPException(status_code=409, detail="Session is not running")
    supabase.table("sessions").update({"status": "cancelling"}).eq("id", session_id).execute()
    return {}


# ──────────────────────── Delete ────────────────────────


@app.delete("/api/projects/{project_id}/sessions/all")
async def delete_all_project_sessions(project_id: str):
    """Delete all sessions in a project (cascades to companies, postings, contacts)."""
    supabase.table("sessions").delete().eq("project_id", project_id).execute()
    return {"status": "ok"}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, force: bool = False):
    if not force:
        deps = (
            supabase.table("sessions")
            .select("project_id")
            .eq("source_session_id", session_id)
            .execute()
        )
        if deps.data:
            project_ids = list({r["project_id"] for r in deps.data})
            names = []
            for pid in project_ids:
                p = supabase.table("projects").select("name").eq("id", pid).execute()
                if p.data:
                    names.append(p.data[0]["name"])
            raise HTTPException(
                status_code=409,
                detail={"message": "Session is imported by other projects", "projects": names},
            )
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
    # Proxy imported sessions to their source
    sess = supabase.table("sessions").select("type, source_session_id").eq("id", session_id).execute()
    if sess.data and sess.data[0].get("type") == "imported":
        src_id = sess.data[0].get("source_session_id")
        if not src_id:
            raise HTTPException(status_code=410, detail="Source session has been deleted")
        session_id = src_id

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
    # Proxy imported sessions to their source
    sess = supabase.table("sessions").select("type, source_session_id").eq("id", session_id).execute()
    if sess.data and sess.data[0].get("type") == "imported":
        src_id = sess.data[0].get("source_session_id")
        if not src_id:
            raise HTTPException(status_code=410, detail="Source session has been deleted")
        session_id = src_id

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
        .eq("is_anti", False)
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
        "is_anti": False,
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
        .eq("is_anti", False)
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
            {"project_id": project_id, "name": name, "is_anti": False} for name in new_names
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


# ──────────────────────── Anti-Keyword Groups ────────────────────────


@app.get("/api/projects/{project_id}/anti-keyword-groups")
async def list_anti_keyword_groups(project_id: str):
    groups = (
        supabase.table("keyword_groups")
        .select("id, name, created_at")
        .eq("project_id", project_id)
        .eq("is_anti", True)
        .order("created_at")
        .execute()
    ).data

    group_ids = [g["id"] for g in groups]
    keywords = []
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


@app.post("/api/projects/{project_id}/anti-keyword-groups")
async def create_anti_keyword_group(project_id: str, body: CreateKeywordGroup):
    result = supabase.table("keyword_groups").insert({
        "project_id": project_id,
        "name": body.name,
        "is_anti": True,
    }).execute()
    return result.data[0]


@app.post("/api/projects/{project_id}/anti-keyword-groups/import")
async def import_anti_keyword_groups(project_id: str, file: UploadFile = File(...)):
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
        .eq("is_anti", True)
        .execute()
    ).data
    group_by_name = {g["name"]: g["id"] for g in existing_groups}

    # Pass 1 — resolve all groups (create missing ones in a single batch insert)
    seen: set[str] = set()
    ordered_group_names: list[str] = []
    for row in parsed:
        if row["group"] not in seen:
            seen.add(row["group"])
            ordered_group_names.append(row["group"])

    existing_names = [n for n in ordered_group_names if n in group_by_name]
    new_names = [n for n in ordered_group_names if n not in group_by_name]

    group_id_by_name: dict[str, str] = {}
    for name in existing_names:
        group_id_by_name[name] = group_by_name[name]

    if new_names:
        result = supabase.table("keyword_groups").insert([
            {"project_id": project_id, "name": name, "is_anti": True} for name in new_names
        ]).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create anti-keyword groups")
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

def _upsert_keyword_scan(project_id: str, fields: dict) -> None:
    """Insert or update the keyword_scans row for this project."""
    existing = (
        supabase.table("keyword_scans")
        .select("id")
        .eq("project_id", project_id)
        .execute()
    )
    if existing.data:
        supabase.table("keyword_scans").update(fields).eq("project_id", project_id).execute()
    else:
        supabase.table("keyword_scans").insert({"project_id": project_id, **fields}).execute()


def _merge_resume_results(project_id: str, new_scan_result: dict) -> dict:
    """
    On a resume run, new_scan_result only contains companies processed in this run.
    Load the previously-stored XLSX, reconstruct the old companies, and merge.
    Returns a complete scan_result with all companies.
    """
    _logger = logging.getLogger(__name__)
    try:
        row = (
            supabase.table("projects")
            .select("keyword_scan_result")
            .eq("id", project_id)
            .execute()
        )
        if not row.data or not row.data[0].get("keyword_scan_result"):
            return new_scan_result  # no previous result; return as-is

        xlsx_bytes = base64.b64decode(row.data[0]["keyword_scan_result"])
        xf = pd.ExcelFile(io.BytesIO(xlsx_bytes))
        if "Summary" not in xf.sheet_names:
            return new_scan_result

        summary_df = xf.parse("Summary")
        details_df = xf.parse("Details") if "Details" in xf.sheet_names else pd.DataFrame()

        groups = new_scan_result["groups"]

        def _dedup_key(inn: str, name: str) -> str:
            inn = (inn or "").strip()
            return f"inn:{inn}" if inn else f"name:{(name or '').strip().lower()}"

        new_keys = {
            _dedup_key(c["inn"], c["name"])
            for c in new_scan_result["companies"]
        }

        # Reconstruct old companies from Summary sheet
        old_companies = []
        kw_cols = [
            c for c in summary_df.columns
            if c not in ("Company", "INN") and not str(c).endswith(" (total)")
        ]
        for _, row_s in summary_df.iterrows():
            inn = str(row_s.get("INN", "") or "")
            company_name = str(row_s.get("Company", "") or "")
            if _dedup_key(inn, company_name) in new_keys:
                continue  # already in new results; skip
            results = {}
            for kw in kw_cols:
                count = int(row_s.get(kw, 0) or 0)
                # Reconstruct sentences from Details sheet
                sentences = []
                if not details_df.empty and "Keyword" in details_df.columns:
                    detail_rows = details_df[
                        (details_df["INN"].astype(str) == inn) &
                        (details_df["Keyword"].astype(str) == str(kw))
                    ]
                    if not detail_rows.empty:
                        raw = str(detail_rows.iloc[0].get("Sentences", "") or "")
                        for part in raw.split("\n\n"):
                            part = part.strip()
                            if part:
                                sentences.append({"source": "unknown", "field": "", "title": "", "sentence": part})
                results[str(kw)] = {"count": count, "sentences": sentences}
            old_companies.append({
                "name": str(row_s.get("Company", "") or ""),
                "inn": inn,
                "results": results,
            })

        merged_companies = old_companies + new_scan_result["companies"]
        return {"groups": groups, "companies": merged_companies}

    except Exception:
        _logger.exception("Failed to merge resume results for project %s; using partial results", project_id)
        return new_scan_result


def _run_scan_task(project_id: str, started_at: datetime | None = None) -> None:
    _logger = logging.getLogger(__name__)
    now = datetime.now(timezone.utc)
    is_resume = started_at is not None

    try:
        if not is_resume:
            started_at = now
            _upsert_keyword_scan(project_id, {
                "status": "running",
                "started_at": started_at.isoformat(),
                "updated_at": now.isoformat(),
                "companies_done": 0,
                "companies_total": 0,
                "error": None,
            })
        else:
            _upsert_keyword_scan(project_id, {
                "status": "running",
                "updated_at": now.isoformat(),
            })
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to write initial running status for project %s; proceeding anyway", project_id
        )
        if not is_resume:
            started_at = now

    def _on_total_known(total: int) -> None:
        try:
            _upsert_keyword_scan(project_id, {
                "companies_total": total,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            _logger.warning("Failed to update companies_total for project %s", project_id)

    def _on_company_done(done: int) -> None:
        try:
            _upsert_keyword_scan(project_id, {
                "companies_done": done,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            _logger.warning("Failed to update companies_done for project %s", project_id)

    try:
        def _is_scan_cancelled() -> bool:
            try:
                row = (
                    supabase.table("keyword_scans")
                    .select("status")
                    .eq("project_id", project_id)
                    .execute()
                )
                return bool(row.data) and row.data[0].get("status") == "cancelling"
            except Exception:
                return False

        scan_result = scan_project_keywords(
            project_id,
            started_at,
            _on_total_known,
            _on_company_done,
            _is_scan_cancelled,
        )
        # On resume, scan_result only has newly-processed companies.
        # Merge with previous XLSX to produce a complete result.
        if is_resume:
            scan_result = _merge_resume_results(project_id, scan_result)

        buffer = generate_keyword_xlsx(scan_result)
        data = buffer.getvalue()
        try:
            encoded = base64.b64encode(data).decode()
            supabase.table("projects").update(
                {"keyword_scan_result": encoded}
            ).eq("id", project_id).execute()
        except Exception:
            _logger.exception(
                "Failed to persist keyword scan to DB for project %s", project_id
            )
        _upsert_keyword_scan(project_id, {
            "status": "done",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    except ScanCancelledError:
        _logger.info("Keyword scan cancelled for project %s, cleaning up", project_id)
        try:
            supabase.table("projects").update(
                {"keyword_scan_result": None}
            ).eq("id", project_id).execute()
        except Exception:
            _logger.warning("Failed to clear keyword_scan_result for project %s", project_id)
        try:
            _upsert_keyword_scan(project_id, {
                "status": "cancelled",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            _logger.warning("Failed to set cancelled status for project %s", project_id)
    except ValueError as e:
        _upsert_keyword_scan(project_id, {
            "status": "error",
            "error": str(e),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        _logger.exception("Keyword scan failed for project %s", project_id)
        try:
            _upsert_keyword_scan(project_id, {
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            _logger.exception("Failed to write error status for project %s", project_id)


@app.post("/api/projects/{project_id}/keyword-scan/start")
async def keyword_scan_start(project_id: str, background_tasks: BackgroundTasks):
    result = (
        supabase.table("keyword_scans")
        .select("status")
        .eq("project_id", project_id)
        .execute()
    )
    if result.data and result.data[0].get("status") == "running":
        raise HTTPException(status_code=409, detail="Scan already running")
    background_tasks.add_task(_run_scan_task, project_id)
    return {}


@app.post("/api/projects/{project_id}/keyword-scan/cancel")
async def keyword_scan_cancel(project_id: str):
    result = (
        supabase.table("keyword_scans")
        .select("status")
        .eq("project_id", project_id)
        .execute()
    )
    if not result.data or result.data[0].get("status") != "running":
        raise HTTPException(status_code=409, detail="No running keyword scan found")
    supabase.table("keyword_scans").update({"status": "cancelling"}).eq("project_id", project_id).execute()
    return {}


@app.get("/api/projects/{project_id}/keyword-scan/status")
async def keyword_scan_db_status(project_id: str):
    """Returns current keyword scan state from keyword_scans table."""
    result = (
        supabase.table("keyword_scans")
        .select("status, started_at, companies_done, companies_total, error")
        .eq("project_id", project_id)
        .execute()
    )
    if not result.data:
        return {
            "status": "none",
            "started_at": None,
            "companies_done": 0,
            "companies_total": 0,
            "error": None,
        }
    row = result.data[0]
    return {
        "status": row["status"],
        "started_at": row.get("started_at"),
        "companies_done": row.get("companies_done", 0),
        "companies_total": row.get("companies_total", 0),
        "error": row.get("error"),
    }


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
    try:
        data = base64.b64decode(encoded)
    except Exception:
        raise HTTPException(status_code=500, detail="Stored keyword scan result is corrupted")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=keyword_analysis_{project_id[:8]}.xlsx"
        },
    )


@app.get("/api/projects/{project_id}/keyword-scan/download-with-contacts")
async def keyword_scan_download_with_contacts(project_id: str):
    """XLSX with Quick_Summary (Contacts Found + anti-keyword cols), Summary, Details, Anti_Summary, Anti_Details, Contacts."""
    # 1. Load stored keyword scan XLSX
    result = (
        supabase.table("projects")
        .select("keyword_scan_result")
        .eq("id", project_id)
        .execute()
    )
    if not result.data or result.data[0].get("keyword_scan_result") is None:
        raise HTTPException(
            status_code=404,
            detail="No keyword scan result found. Run a keyword scan first.",
        )
    try:
        xlsx_bytes = base64.b64decode(result.data[0]["keyword_scan_result"])
    except Exception:
        raise HTTPException(status_code=500, detail="Stored keyword scan result is corrupted")

    # 2. Parse all sheets from stored XLSX (Anti_Summary/Anti_Details absent in pre-migration results)
    stored_buf = io.BytesIO(xlsx_bytes)
    xf = pd.ExcelFile(stored_buf)
    summary_df = xf.parse("Summary")
    details_df = xf.parse("Details") if "Details" in xf.sheet_names else pd.DataFrame(
        columns=["Company", "INN", "Keyword Group", "Keyword", "Total Matches", "From Postings", "From News", "Sentences"]
    )
    anti_summary_df = xf.parse("Anti_Summary") if "Anti_Summary" in xf.sheet_names else None
    anti_details_df = xf.parse("Anti_Details") if "Anti_Details" in xf.sheet_names else pd.DataFrame(
        columns=["Company", "INN", "Keyword Group", "Keyword", "Total Matches", "From Postings", "From News", "Sentences"]
    )

    # 3. Derive Quick_Summary rows (including anti-keyword columns when available)
    qs_df = derive_quick_summary_df(summary_df, anti_summary_df)

    # 4. Fetch all companies for this project
    sessions_result = (
        supabase.table("sessions")
        .select("id")
        .eq("project_id", project_id)
        .execute()
    )
    session_ids = [s["id"] for s in (sessions_result.data or [])]

    all_companies: list[dict] = []
    for i in range(0, len(session_ids), 200):
        batch = session_ids[i:i + 200]
        offset = 0
        while True:
            rows = (
                supabase.table("companies")
                .select("id, legal_name, inn")
                .in_("session_id", batch)
                .range(offset, offset + 999)
                .execute()
            ).data
            all_companies.extend(rows)
            if len(rows) < 1000:
                break
            offset += 1000

    company_ids = [c["id"] for c in all_companies]
    id_to_inn = {c["id"]: str(c.get("inn") or "") for c in all_companies}
    id_to_name = {c["id"]: c.get("legal_name", "") for c in all_companies}

    # 5. Fetch contacts (same filter as /contacts/download)
    raw_contacts: list[dict] = []
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
            raw_contacts.extend(rows)
            if len(rows) < 1000:
                break
            offset += 1000

    contacts = [
        c for c in raw_contacts
        if c.get("email_status") is None or c.get("email_status") in _KEEP_STATUSES
    ]

    # 6. Count contacts per INN; add Contacts Found column to Quick_Summary (position 5 = col 6)
    inn_to_count: dict[str, int] = {}
    for c in contacts:
        inn = id_to_inn.get(c.get("company_id", ""), "")
        if inn:
            inn_to_count[inn] = inn_to_count.get(inn, 0) + 1

    _contacts_found = qs_df["INN"].apply(lambda inn: inn_to_count.get(str(inn), 0))
    if "Contacts Found" not in qs_df.columns:
        qs_df.insert(5, "Contacts Found", _contacts_found)
    else:
        qs_df["Contacts Found"] = _contacts_found

    # 7. Build Contacts sheet
    if contacts:
        for c in contacts:
            c["company_name"] = id_to_name.get(c.get("company_id", ""), "")
        contacts_df = pd.DataFrame(contacts)
        for col in ("id", "session_id", "company_id", "contact_scan_id"):
            if col in contacts_df.columns:
                contacts_df = contacts_df.drop(columns=[col])
        cols = ["company_name"] + [c for c in contacts_df.columns if c != "company_name"]
        contacts_df = contacts_df[[c for c in cols if c in contacts_df.columns]]
        if "company_name" in contacts_df.columns and "last_name" in contacts_df.columns:
            contacts_df = contacts_df.sort_values(["company_name", "last_name"], na_position="last")
    else:
        contacts_df = pd.DataFrame(columns=["company_name"])

    # 8. Write XLSX: Quick_Summary, Summary, Details, Anti_Summary*, Anti_Details*, Contacts
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        qs_df.to_excel(writer, sheet_name="Quick_Summary", index=False)
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        details_df.to_excel(writer, sheet_name="Details", index=False)
        if anti_summary_df is not None:
            anti_summary_df.to_excel(writer, sheet_name="Anti_Summary", index=False)
            anti_details_df.to_excel(writer, sheet_name="Anti_Details", index=False)
        contacts_df.to_excel(writer, sheet_name="Contacts", index=False)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=keyword_scan_with_contacts_{project_id[:8]}.xlsx"
        },
    )
