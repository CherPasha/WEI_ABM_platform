# tests/test_email_verification.py
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

import pytest

from app.services.hunter_service import verify_email


def _mock_response(status_code: int, json_body: dict) -> MagicMock:
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_body
    m.text = str(json_body)
    return m


VALID_DATA = {
    "data": {
        "email": "john@example.com",
        "status": "valid",
        "score": 92,
        "regexp": True,
        "gibberish": False,
        "disposable": False,
        "webmail": False,
        "mx_records": True,
        "smtp_server": True,
        "smtp_check": True,
        "accept_all": False,
        "block": False,
        "sources": [],
    }
}


def test_verify_email_returns_all_fields_on_200():
    with patch("app.services.hunter_service.requests.get") as mock_get:
        mock_get.return_value = _mock_response(200, VALID_DATA)
        result = verify_email("john@example.com")

    assert result is not None
    assert result["email_status"] == "valid"
    assert result["email_score"] == 92
    assert result["email_regexp"] is True
    assert result["email_gibberish"] is False
    assert result["email_disposable"] is False
    assert result["email_webmail"] is False
    assert result["email_mx_records"] is True
    assert result["email_smtp_server"] is True
    assert result["email_smtp_check"] is True
    assert result["email_accept_all"] is False
    assert result["email_block"] is False
    assert result["email_verified_at"] is not None


def test_verify_email_polls_on_202_then_succeeds():
    """If the first attempt returns 202 (in progress), retry and succeed on the second."""
    in_progress = _mock_response(202, {})
    ok = _mock_response(200, VALID_DATA)

    with patch("app.services.hunter_service.requests.get") as mock_get:
        with patch("app.services.hunter_service.time.sleep"):
            mock_get.side_effect = [in_progress, ok]
            result = verify_email("john@example.com")

    assert result is not None
    assert result["email_status"] == "valid"
    assert mock_get.call_count == 2


def test_verify_email_returns_none_after_three_202s():
    """If all 3 polling attempts return 202, return None."""
    in_progress = _mock_response(202, {})

    with patch("app.services.hunter_service.requests.get") as mock_get:
        with patch("app.services.hunter_service.time.sleep"):
            mock_get.return_value = in_progress
            result = verify_email("john@example.com")

    assert result is None
    assert mock_get.call_count == 3


def test_verify_email_returns_none_on_4xx():
    """Non-200/202 responses return None."""
    with patch("app.services.hunter_service.requests.get") as mock_get:
        mock_get.return_value = _mock_response(401, {"error": "Unauthorized"})
        result = verify_email("john@example.com")

    assert result is None


def test_verify_email_returns_none_on_exception():
    """Network/timeout errors are caught and None is returned."""
    with patch("app.services.hunter_service.requests.get") as mock_get:
        mock_get.side_effect = Exception("connection refused")
        result = verify_email("john@example.com")

    assert result is None


def test_verify_email_uses_correct_endpoint_and_params():
    """Verify the right URL and required params are passed."""
    with patch("app.services.hunter_service.requests.get") as mock_get:
        mock_get.return_value = _mock_response(200, VALID_DATA)
        verify_email("test@example.com")

    call_kwargs = mock_get.call_args
    url = call_kwargs[0][0]
    params = call_kwargs[1]["params"]

    assert url == "https://api.hunter.io/v2/email-verifier"
    assert params["email"] == "test@example.com"
    assert "api_key" in params


def _make_status_data(status: str) -> dict:
    d = {**VALID_DATA["data"], "status": status}
    return {"data": d}


@pytest.mark.parametrize("status", ["valid", "invalid", "webmail", "disposable", "unknown", "accept_all"])
def test_verify_email_returns_status_field_for_all_statuses(status):
    """verify_email() always returns the status as-is — filtering is the caller's job."""
    with patch("app.services.hunter_service.requests.get") as mock_get:
        mock_get.return_value = _mock_response(200, _make_status_data(status))
        result = verify_email("any@example.com")

    assert result is not None
    assert result["email_status"] == status
