import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock


def _make_fetch_all():
    def fake(table, column, value, select="*"):
        if table == "keyword_groups":
            return [{"id": "g1", "name": "Tech", "is_anti": False}]
        if table == "keywords":
            return [{"id": "k1", "group_id": "g1", "keyword": "AI"}]
        if table == "stop_words":
            return []
        if table == "sessions":
            return [{"id": "s1"}]
        return []
    return fake


def _make_fetch_in():
    def fake(table, column, values, select="*", **kwargs):
        if table == "keywords":
            return [{"id": "k1", "group_id": "g1", "keyword": "AI"}]
        if table == "companies" and column == "session_id":
            return [
                {"id": "c1", "legal_name": "Acme", "inn": "001", "known_names": []},
                {"id": "c2", "legal_name": "Beta", "inn": "002", "known_names": []},
            ]
        if table == "companies" and column == "id" and "keyword_scanned_at" in select:
            return [{"id": cid, "keyword_scanned_at": None} for cid in values]
        if table in ("postings", "news_articles"):
            return []
        return []
    return fake


@patch("app.services.keyword_scanner.time.sleep", return_value=None)
@patch("app.services.keyword_scanner.supabase")
@patch("app.services.keyword_scanner._fetch_all_in")
@patch("app.services.keyword_scanner._fetch_all")
def test_scan_raises_scan_cancelled_error_when_is_cancelled_true(
    mock_fetch_all, mock_fetch_in, mock_supa, mock_sleep
):
    """scan_project_keywords raises ScanCancelledError when is_cancelled() returns True."""
    from app.services.keyword_scanner import scan_project_keywords, ScanCancelledError

    mock_fetch_all.side_effect = _make_fetch_all()
    mock_fetch_in.side_effect = _make_fetch_in()
    mock_supa.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    started_at = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(ScanCancelledError):
        scan_project_keywords(
            "proj1",
            started_at,
            on_total_known=lambda t: None,
            on_company_done=lambda d: None,
            is_cancelled=lambda: True,
        )


@patch("app.services.keyword_scanner.time.sleep", return_value=None)
@patch("app.services.keyword_scanner.supabase")
@patch("app.services.keyword_scanner._fetch_all_in")
@patch("app.services.keyword_scanner._fetch_all")
def test_scan_completes_when_not_cancelled(
    mock_fetch_all, mock_fetch_in, mock_supa, mock_sleep
):
    """scan_project_keywords completes normally when is_cancelled() always returns False."""
    from app.services.keyword_scanner import scan_project_keywords, ScanCancelledError

    mock_fetch_all.side_effect = _make_fetch_all()
    mock_fetch_in.side_effect = _make_fetch_in()
    mock_supa.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    started_at = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)

    result = scan_project_keywords(
        "proj1",
        started_at,
        on_total_known=lambda t: None,
        on_company_done=lambda d: None,
        is_cancelled=lambda: False,
    )
    assert "companies" in result
    assert len(result["companies"]) == 2
