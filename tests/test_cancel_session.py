from unittest.mock import patch, MagicMock


@patch("app.services.session_processor._supabase_call_with_retry")
def test_is_cancelling_true_when_status_cancelling(mock_retry):
    from app.services.session_processor import _is_cancelling
    mock_retry.side_effect = lambda fn: MagicMock(data=[{"status": "cancelling"}])
    assert _is_cancelling("sess-1") is True


@patch("app.services.session_processor._supabase_call_with_retry")
def test_is_cancelling_false_when_status_running(mock_retry):
    from app.services.session_processor import _is_cancelling
    mock_retry.side_effect = lambda fn: MagicMock(data=[{"status": "running"}])
    assert _is_cancelling("sess-1") is False


@patch("app.services.session_processor._supabase_call_with_retry")
def test_is_cancelling_false_when_no_row(mock_retry):
    from app.services.session_processor import _is_cancelling
    mock_retry.side_effect = lambda fn: MagicMock(data=[])
    assert _is_cancelling("sess-1") is False
