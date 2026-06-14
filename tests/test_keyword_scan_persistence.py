from datetime import datetime, timezone

import pytest

from app.services.keyword_scanner import _is_checkpointed


def test_is_checkpointed_true_when_one_id_scanned_after_threshold():
    threshold = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
    scanned_map = {
        "id1": "2026-06-11T11:00:00+00:00",  # after threshold
        "id2": None,
    }
    assert _is_checkpointed(["id1", "id2"], threshold, scanned_map) is True


def test_is_checkpointed_false_when_all_ids_null():
    threshold = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
    scanned_map = {"id1": None, "id2": None}
    assert _is_checkpointed(["id1", "id2"], threshold, scanned_map) is False


def test_is_checkpointed_false_when_scanned_before_threshold():
    threshold = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
    scanned_map = {"id1": "2026-06-11T09:00:00+00:00"}  # before threshold
    assert _is_checkpointed(["id1"], threshold, scanned_map) is False


def test_is_checkpointed_false_when_empty_company_ids():
    threshold = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
    assert _is_checkpointed([], threshold, {}) is False


def test_is_checkpointed_true_when_scanned_exactly_at_threshold():
    threshold = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
    scanned_map = {"id1": "2026-06-11T10:00:00+00:00"}  # equal = checkpointed
    assert _is_checkpointed(["id1"], threshold, scanned_map) is True


from unittest.mock import patch, MagicMock


def _make_fetch_all_side_effect():
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


def _make_fetch_in_side_effect(checkpoint_map=None):
    """checkpoint_map: {company_id: keyword_scanned_at_str | None}"""
    if checkpoint_map is None:
        checkpoint_map = {"c1": None, "c2": None}

    def fake(table, column, values, select="*", **kwargs):
        if table == "keywords":
            return [{"id": "k1", "group_id": "g1", "keyword": "AI"}]
        if table == "companies" and column == "session_id":
            return [
                {"id": "c1", "legal_name": "Acme", "inn": "001", "known_names": []},
                {"id": "c2", "legal_name": "Beta", "inn": "002", "known_names": []},
            ]
        if table == "companies" and column == "id" and "keyword_scanned_at" in select:
            return [{"id": cid, "keyword_scanned_at": checkpoint_map.get(cid)} for cid in values]
        if table == "postings":
            return []
        if table == "news_articles":
            return []
        return []
    return fake


@patch("app.services.keyword_scanner.time.sleep", return_value=None)
@patch("app.services.keyword_scanner.supabase")
@patch("app.services.keyword_scanner._fetch_all_in")
@patch("app.services.keyword_scanner._fetch_all")
def test_on_total_known_and_on_company_done_called_for_fresh_scan(
    mock_fetch_all, mock_fetch_in, mock_supa, mock_sleep
):
    """Fresh scan: on_total_known(2) and on_company_done called twice (1, 2)."""
    from app.services.keyword_scanner import scan_project_keywords

    mock_fetch_all.side_effect = _make_fetch_all_side_effect()
    mock_fetch_in.side_effect = _make_fetch_in_side_effect()
    mock_supa.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    started_at = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
    total_known = []
    done_counts = []

    scan_project_keywords(
        "proj1",
        started_at,
        on_total_known=lambda t: total_known.append(t),
        on_company_done=lambda d: done_counts.append(d),
    )

    assert total_known == [2]
    assert done_counts == [1, 2]


@patch("app.services.keyword_scanner.time.sleep", return_value=None)
@patch("app.services.keyword_scanner.supabase")
@patch("app.services.keyword_scanner._fetch_all_in")
@patch("app.services.keyword_scanner._fetch_all")
def test_resume_skips_checkpointed_company(
    mock_fetch_all, mock_fetch_in, mock_supa, mock_sleep
):
    """Resume: c1 already checkpointed, only c2 processed; done_count starts at 1."""
    from app.services.keyword_scanner import scan_project_keywords

    mock_fetch_all.side_effect = _make_fetch_all_side_effect()
    # c1 was processed after scan started (10:00), c2 not yet
    mock_fetch_in.side_effect = _make_fetch_in_side_effect({
        "c1": "2026-06-11T11:00:00+00:00",
        "c2": None,
    })
    mock_supa.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    started_at = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
    total_known = []
    done_counts = []

    scan_project_keywords(
        "proj1",
        started_at,
        on_total_known=lambda t: total_known.append(t),
        on_company_done=lambda d: done_counts.append(d),
    )

    assert total_known == [2]       # always the full deduped total
    assert done_counts == [2]       # only one company processed, already_done=1 → count starts at 2
