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
