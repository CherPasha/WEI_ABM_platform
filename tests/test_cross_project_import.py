from unittest.mock import patch, MagicMock, call
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def _mock_sb():
    """Return a fresh MagicMock that chains .table().select().eq()... naturally."""
    m = MagicMock()
    # Make every method return the same mock so chains work
    m.table.return_value = m
    m.select.return_value = m
    m.eq.return_value = m
    m.neq.return_value = m
    m.in_.return_value = m
    m.order.return_value = m
    m.range.return_value = m
    m.insert.return_value = m
    m.update.return_value = m
    m.delete.return_value = m
    m.not_ = m
    m.execute.return_value = MagicMock(data=[])
    return m


# ──────────────── import endpoint ────────────────

def test_import_session_success():
    sb = _mock_sb()
    results = iter([
        MagicMock(data=[{"id": "src-sess", "project_id": "proj-1", "filename": "a.xlsx", "status": "completed", "total_companies": 2}]),  # source session lookup
        MagicMock(data=[]),                                                               # duplicate guard
        MagicMock(data=[{"name": "Project One"}]),                                        # source project name
        MagicMock(data=[{"id": "new-sess", "type": "imported", "project_id": "proj-2"}]), # session insert
        MagicMock(data=[{"id": "c1", "legal_name": "A", "inn": "1", "kpp": None, "ogrn": None, "website_url": None, "ceo_name": None, "revenue": None, "known_names": []},
                        {"id": "c2", "legal_name": "B", "inn": "2", "kpp": None, "ogrn": None, "website_url": None, "ceo_name": None, "revenue": None, "known_names": []}]),  # companies page 1
        MagicMock(data=[]),                                                               # companies insert
    ])
    sb.execute.side_effect = lambda: next(results)

    with patch("app.main.supabase", sb):
        resp = client.post("/api/projects/proj-2/sessions/import",
                           json={"source_session_id": "src-sess"})
    assert resp.status_code == 200
    assert resp.json()["type"] == "imported"


def test_import_session_same_project_rejected():
    sb = _mock_sb()
    sb.execute.return_value = MagicMock(data=[{"id": "src-sess", "project_id": "proj-1", "filename": "a.xlsx", "status": "completed", "total_companies": 2}])

    with patch("app.main.supabase", sb):
        resp = client.post("/api/projects/proj-1/sessions/import",
                           json={"source_session_id": "src-sess"})
    assert resp.status_code == 400
    assert "same project" in resp.json()["detail"].lower()


def test_import_session_not_completed_rejected():
    sb = _mock_sb()
    sb.execute.return_value = MagicMock(data=[{"id": "src-sess", "project_id": "proj-1", "filename": "a.xlsx", "status": "processing", "total_companies": 0}])

    with patch("app.main.supabase", sb):
        resp = client.post("/api/projects/proj-2/sessions/import",
                           json={"source_session_id": "src-sess"})
    assert resp.status_code == 400
    assert "completed" in resp.json()["detail"].lower()


def test_import_session_duplicate_rejected():
    sb = _mock_sb()
    results = iter([
        MagicMock(data=[{"id": "src-sess", "project_id": "proj-1", "filename": "a.xlsx", "status": "completed", "total_companies": 1}]),  # source session
        MagicMock(data=[{"id": "existing"}]),  # duplicate guard — already imported
    ])
    sb.execute.side_effect = lambda: next(results)

    with patch("app.main.supabase", sb):
        resp = client.post("/api/projects/proj-2/sessions/import",
                           json={"source_session_id": "src-sess"})
    assert resp.status_code == 409


def test_import_session_source_not_found():
    sb = _mock_sb()
    sb.execute.return_value = MagicMock(data=[])

    with patch("app.main.supabase", sb):
        resp = client.post("/api/projects/proj-2/sessions/import",
                           json={"source_session_id": "nonexistent"})
    assert resp.status_code == 404


# ──────────────── completed sessions endpoint ────────────────

def test_completed_sessions_lists_only_completed():
    sb = _mock_sb()
    sb.execute.return_value = MagicMock(data=[
        {"id": "s1", "filename": "f.xlsx", "created_at": "2026-01-01", "total_companies": 5}
    ])

    with patch("app.main.supabase", sb):
        resp = client.get("/api/projects/proj-1/sessions/completed")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "s1"


def test_completed_sessions_excludes_already_imported():
    sb = _mock_sb()
    results = iter([
        MagicMock(data=[
            {"id": "s1", "filename": "f.xlsx", "created_at": "2026-01-01", "total_companies": 5},
            {"id": "s2", "filename": "g.xlsx", "created_at": "2026-01-02", "total_companies": 3},
        ]),  # completed sessions in source project
        MagicMock(data=[{"source_session_id": "s1"}]),  # already imported in target project
    ])
    sb.execute.side_effect = lambda: next(results)

    with patch("app.main.supabase", sb):
        resp = client.get("/api/projects/proj-1/sessions/completed?importing_into=proj-2")
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert "s1" not in ids
    assert "s2" in ids


# ──────────────── dependents endpoints ────────────────

def test_session_dependents_returns_list():
    sb = _mock_sb()
    results = iter([
        MagicMock(data=[{"id": "imp-sess", "project_id": "proj-2", "source_project_name": "P2", "source_session_filename": "a.xlsx"}]),  # dependents query
        MagicMock(data=[{"name": "Project Two"}]),  # project name lookup
    ])
    sb.execute.side_effect = lambda: next(results)

    with patch("app.main.supabase", sb):
        resp = client.get("/api/sessions/src-sess/dependents")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["project_name"] == "Project Two"


def test_session_dependents_returns_empty():
    sb = _mock_sb()
    sb.execute.return_value = MagicMock(data=[])

    with patch("app.main.supabase", sb):
        resp = client.get("/api/sessions/src-sess/dependents")
    assert resp.status_code == 200
    assert resp.json() == []


def test_delete_session_blocked_when_dependents():
    sb = _mock_sb()
    results = iter([
        MagicMock(data=[{"project_id": "proj-2"}]),  # dependents exist
        MagicMock(data=[{"name": "Project Two"}]),   # project name
    ])
    sb.execute.side_effect = lambda: next(results)

    with patch("app.main.supabase", sb):
        resp = client.delete("/api/sessions/src-sess")
    assert resp.status_code == 409


def test_delete_session_force_bypasses_guard():
    sb = _mock_sb()
    results = iter([
        MagicMock(data=[{"id": "src-sess"}]),  # delete result
    ])
    sb.execute.side_effect = lambda: next(results)

    with patch("app.main.supabase", sb):
        resp = client.delete("/api/sessions/src-sess?force=true")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_delete_project_blocked_when_dependents():
    sb = _mock_sb()
    results = iter([
        MagicMock(data=[{"id": "s1"}]),              # project sessions
        MagicMock(data=[{"project_id": "proj-2"}]),  # dependents for s1
        MagicMock(data=[{"name": "Project Two"}]),   # dependent project name
    ])
    sb.execute.side_effect = lambda: next(results)

    with patch("app.main.supabase", sb):
        resp = client.delete("/api/projects/proj-1")
    assert resp.status_code == 409


# ──────────────── download proxy ────────────────

def test_download_postings_proxies_for_imported_session():
    sb = _mock_sb()
    results = iter([
        MagicMock(data=[{"type": "imported", "source_session_id": "src-sess"}]),  # session type check
        # _query_all_rows calls: page 1 returns data, page 2 returns empty
        MagicMock(data=[{"id": "p1", "company_id": "c1", "title": "Dev", "session_id": "src-sess"}]),
        MagicMock(data=[]),
    ])
    sb.execute.side_effect = lambda: next(results)

    with patch("app.main.supabase", sb):
        resp = client.get("/api/sessions/imp-sess/postings/download")
    assert resp.status_code == 200
    assert "spreadsheet" in resp.headers["content-type"]


def test_download_postings_returns_410_for_broken_reference():
    sb = _mock_sb()
    sb.execute.return_value = MagicMock(data=[{"type": "imported", "source_session_id": None}])

    with patch("app.main.supabase", sb):
        resp = client.get("/api/sessions/imp-sess/postings/download")
    assert resp.status_code == 410


def test_download_news_proxies_for_imported_session():
    sb = _mock_sb()
    results = iter([
        MagicMock(data=[{"type": "imported", "source_session_id": "src-sess"}]),  # session type check
        MagicMock(data=[{"id": "n1", "company_id": "c1", "title": "News", "session_id": "src-sess", "url": "http://x.com", "snippet": "x", "full_text": "x", "published_at": None}]),
        MagicMock(data=[]),  # pagination done
        MagicMock(data=[{"id": "c1", "legal_name": "A"}]),  # companies for news
    ])
    sb.execute.side_effect = lambda: next(results)

    with patch("app.main.supabase", sb):
        resp = client.get("/api/sessions/imp-sess/news/download")
    assert resp.status_code == 200
    assert "spreadsheet" in resp.headers["content-type"]


# ──────────────── keyword scanner ────────────────

def test_keyword_scanner_uses_source_company_id_for_ghost():
    """Ghost company (source_company_id set) fetches postings by source ID, not its own."""
    from app.services.keyword_scanner import scan_project_keywords
    from datetime import datetime, timezone

    sb = _mock_sb()

    ghost_id = "ghost-c1"
    source_id = "source-c1"

    call_seq = iter([
        # keyword_groups
        MagicMock(data=[{"id": "g1", "name": "Tech", "is_anti": False}]),
        # keywords
        MagicMock(data=[{"id": "k1", "group_id": "g1", "keyword": "Python"}]),
        # stop_words
        MagicMock(data=[]),
        # sessions for project
        MagicMock(data=[{"id": "imported-sess"}]),
        # companies for session — ghost company with source_company_id set
        MagicMock(data=[{"id": ghost_id, "legal_name": "Acme", "inn": "123", "known_names": ["Acme"], "source_company_id": source_id}]),
        # keyword_scanned_at checkpoint
        MagicMock(data=[{"id": ghost_id, "keyword_scanned_at": None}]),
        # postings fetched by source_id (effective) — has a Python match
        MagicMock(data=[{"company_id": source_id, "title": "Python Developer", "snippet_requirement": "Python", "snippet_responsibility": ""}]),
        # news fetched by source_id (effective)
        MagicMock(data=[]),
        # update company (checkpoint write) — we don't care about result
        MagicMock(data=[{"id": ghost_id}]),
    ])
    sb.execute.side_effect = lambda: next(call_seq)

    with patch("app.services.keyword_scanner.supabase", sb):
        result = scan_project_keywords(
            "proj-2",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            lambda n: None,
            lambda n: None,
        )

    companies = result["companies"]
    assert len(companies) == 1
    assert companies[0]["inn"] == "123"
    assert companies[0]["results"]["Python"]["count"] > 0
