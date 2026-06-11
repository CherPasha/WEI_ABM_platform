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
