import app.config
import pytest
from unittest.mock import patch, MagicMock


def test_settings_has_hh_fields():
    assert hasattr(app.config.Settings, "HH_CLIENT_ID")
    assert hasattr(app.config.Settings, "HH_CLIENT_SECRET")
    assert hasattr(app.config.Settings, "HH_USER_AGENT")
    assert "WEI-Group-Vacancy-Analysis" in app.config.Settings.HH_USER_AGENT


def test_get_app_token_returns_token():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"access_token": "test_token_123"}
    mock_resp.raise_for_status.return_value = None

    with patch("app.services.postings_finder.requests.post", return_value=mock_resp) as mock_post:
        from app.services.postings_finder import get_app_token
        token = get_app_token()

    assert token == "test_token_123"
    call_args = mock_post.call_args
    assert call_args[0][0] == "https://api.hh.ru/token"
    assert call_args[1]["data"]["grant_type"] == "client_credentials"


def test_get_app_token_raises_if_no_token_in_response():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {}
    mock_resp.raise_for_status.return_value = None

    with patch("app.services.postings_finder.requests.post", return_value=mock_resp):
        from app.services.postings_finder import get_app_token
        with pytest.raises(RuntimeError, match="access_token"):
            get_app_token()
