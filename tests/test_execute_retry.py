"""Tests for _execute() — the retry wrapper around PostgREST .execute() calls.

postgrest's send_with_retry only retries on HTTP 503/520 (Cloudflare errors) and
does not catch httpx.RemoteProtocolError at all. _execute() fills that gap.
"""
from unittest.mock import MagicMock, call, patch

import httpx
import pytest

from app.services.keyword_scanner import _RETRY_DELAYS, _execute


def _builder(*side_effects):
    """Return a mock PostgREST builder whose .execute() has the given side effects."""
    b = MagicMock()
    b.execute.side_effect = list(side_effects)
    return b


# ── happy path ─────────────────────────────────────────────────────────────────

def test_execute_returns_result_immediately_when_no_error():
    expected = MagicMock(data=[{"id": "1"}])
    result = _execute(_builder(expected))
    assert result is expected


# ── retry on RemoteProtocolError ───────────────────────────────────────────────

def test_execute_retries_once_after_connection_drop():
    expected = MagicMock(data=[{"id": "1"}])
    builder = _builder(httpx.RemoteProtocolError("Server disconnected"), expected)

    with patch("app.services.keyword_scanner.time.sleep") as mock_sleep:
        result = _execute(builder)

    assert result is expected
    assert builder.execute.call_count == 2
    mock_sleep.assert_called_once_with(_RETRY_DELAYS[0])


def test_execute_retries_twice_after_two_consecutive_drops():
    expected = MagicMock(data=[])
    err = httpx.RemoteProtocolError("Server disconnected")
    builder = _builder(err, err, expected)

    with patch("app.services.keyword_scanner.time.sleep") as mock_sleep:
        result = _execute(builder)

    assert result is expected
    assert builder.execute.call_count == 3
    assert mock_sleep.call_args_list == [call(_RETRY_DELAYS[0]), call(_RETRY_DELAYS[1])]


def test_execute_uses_increasing_delays_between_retries():
    """First retry waits 2 s, second retry waits 5 s."""
    err = httpx.RemoteProtocolError("Server disconnected")
    builder = _builder(err, err, MagicMock(data=[]))

    with patch("app.services.keyword_scanner.time.sleep") as mock_sleep:
        _execute(builder)

    delays = [c.args[0] for c in mock_sleep.call_args_list]
    assert delays == [2, 5]


def test_execute_raises_after_all_retries_exhausted():
    err = httpx.RemoteProtocolError("Server disconnected")
    builder = _builder(err, err, err)

    with patch("app.services.keyword_scanner.time.sleep"):
        with pytest.raises(httpx.RemoteProtocolError):
            _execute(builder)

    assert builder.execute.call_count == 3


def test_execute_does_not_swallow_error_on_final_attempt():
    """The original exception (not a generic one) must propagate."""
    original = httpx.RemoteProtocolError("very specific disconnect")
    builder = _builder(original, original, original)

    with patch("app.services.keyword_scanner.time.sleep"):
        with pytest.raises(httpx.RemoteProtocolError, match="very specific disconnect"):
            _execute(builder)


# ── must NOT retry on other exception types ────────────────────────────────────

def test_execute_does_not_retry_on_value_error():
    builder = _builder(ValueError("bad query"))
    with pytest.raises(ValueError):
        _execute(builder)
    assert builder.execute.call_count == 1


def test_execute_does_not_retry_on_http_status_error():
    """A 500 from Supabase is a server error, not a connection drop — no retry."""
    err = httpx.HTTPStatusError("500 Internal Server Error",
                                request=MagicMock(), response=MagicMock())
    builder = _builder(err)
    with pytest.raises(httpx.HTTPStatusError):
        _execute(builder)
    assert builder.execute.call_count == 1


def test_execute_does_not_retry_on_generic_exception():
    builder = _builder(RuntimeError("unexpected"))
    with pytest.raises(RuntimeError):
        _execute(builder)
    assert builder.execute.call_count == 1


# ── retry count boundary ───────────────────────────────────────────────────────

def test_execute_does_not_make_a_fourth_attempt():
    """max_retries=2 means exactly 3 total attempts, never 4."""
    err = httpx.RemoteProtocolError("Server disconnected")
    # Provide 4 errors to confirm a 4th call never happens
    builder = _builder(err, err, err, MagicMock(data=[]))

    with patch("app.services.keyword_scanner.time.sleep"):
        with pytest.raises(httpx.RemoteProtocolError):
            _execute(builder)

    assert builder.execute.call_count == 3
