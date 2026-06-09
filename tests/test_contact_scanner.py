import time
from unittest.mock import MagicMock, patch, call


@patch("app.services.contact_scanner.supabase")
@patch("app.services.contact_scanner.find_contacts_for_domain", return_value=[])
@patch("app.services.contact_scanner.time.sleep", return_value=None)
def test_scan_completes_when_no_sessions(mock_sleep, mock_hunter, mock_supa):
    """Scan with no sessions should immediately set status=completed."""
    def table_side_effect(name):
        q = MagicMock()
        q.select.return_value = q
        q.eq.return_value = q
        q.gt.return_value = q
        q.in_.return_value = q
        q.not_ = MagicMock()
        q.not_.is_ = MagicMock(return_value=q)
        q.range.return_value = q
        q.update.return_value = q
        q.insert.return_value = q
        q.order.return_value = q
        q.limit.return_value = q

        if name == "contact_scans":
            q.execute.return_value = MagicMock(data=[{
                "id": "scan-1", "project_id": "proj-1",
                "use_roles": False, "keyword_only": False,
            }])
        elif name == "projects":
            q.execute.return_value = MagicMock(data=[{"target_roles": []}])
        elif name == "sessions":
            q.execute.return_value = MagicMock(data=[])  # no sessions
        else:
            q.execute.return_value = MagicMock(data=[])
        return q

    mock_supa.table.side_effect = table_side_effect

    from app.services.contact_scanner import run_contact_scan
    run_contact_scan("scan-1")

    # Hunter.io should not be called (no companies to scan)
    assert mock_hunter.call_count == 0


@patch("app.services.contact_scanner.supabase")
@patch("app.services.contact_scanner.find_contacts_for_domain")
@patch("app.services.contact_scanner.time.sleep", return_value=None)
def test_scan_deduplicates_hunter_contacts_by_email(mock_sleep, mock_hunter, mock_supa):
    """Contacts whose email already exists for a company are skipped."""
    mock_hunter.return_value = [
        {"email": "new@acme.com", "first_name": "New", "last_name": "Person",
         "confidence": 90, "source": "hunter"},
        {"email": "existing@acme.com", "first_name": "Old", "last_name": "Person",
         "confidence": 85, "source": "hunter"},
    ]

    inserted_batches = []

    def table_side_effect(name):
        q = MagicMock()
        q.select.return_value = q
        q.eq.return_value = q
        q.gt.return_value = q
        q.in_.return_value = q
        q.not_ = MagicMock()
        q.not_.is_ = MagicMock(return_value=q)
        q.range.return_value = q
        q.update.return_value = q
        q.order.return_value = q
        q.limit.return_value = q

        def insert_side(rows):
            inserted_batches.append(rows)
            return q

        q.insert.side_effect = insert_side

        if name == "contact_scans":
            q.execute.return_value = MagicMock(data=[{
                "id": "scan-1", "project_id": "proj-1",
                "use_roles": False, "keyword_only": False,
            }])
        elif name == "projects":
            q.execute.return_value = MagicMock(data=[{"target_roles": []}])
        elif name == "sessions":
            q.execute.return_value = MagicMock(data=[{"id": "sess-1"}])
        elif name == "companies":
            q.execute.return_value = MagicMock(data=[{
                "id": "comp-1", "legal_name": "Acme",
                "known_names": ["Acme"], "website_url": "https://acme.com",
                "keyword_hit_count": 0,
            }])
        elif name == "contacts":
            # Return existing email on first call (get_existing_emails), empty on subsequent
            call_results = [
                MagicMock(data=[{"email": "existing@acme.com"}]),  # existing emails
                MagicMock(data=[]),  # contacts_to_verify (Phase 2)
            ]
            q.execute.side_effect = call_results
        return q

    mock_supa.table.side_effect = table_side_effect

    from importlib import reload
    import app.services.contact_scanner as cs
    reload(cs)
    cs.run_contact_scan("scan-1")

    # Only the NEW contact should have been inserted
    all_inserted = [row for batch in inserted_batches for row in batch]
    inserted_emails = [c.get("email") for c in all_inserted]
    assert "new@acme.com" in inserted_emails
    assert "existing@acme.com" not in inserted_emails
    assert len(all_inserted) == 1
