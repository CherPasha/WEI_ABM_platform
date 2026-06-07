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


def test_find_postings_by_search_term_sends_auth_header():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"items": [{"id": "1", "name": "Dev"}], "pages": 1}

    with patch("app.services.postings_finder.requests.get", return_value=mock_resp) as mock_get:
        from app.services.postings_finder import find_postings_by_search_term
        result = find_postings_by_search_term("python developer", token="my_bearer_token")

    assert len(result) == 1
    call_headers = mock_get.call_args[1]["headers"]
    assert call_headers["Authorization"] == "Bearer my_bearer_token"
    assert "HH-User-Agent" in call_headers


def test_find_postings_by_search_term_no_auth_header_without_token():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"items": [], "pages": 0}

    with patch("app.services.postings_finder.requests.get", return_value=mock_resp) as mock_get:
        from app.services.postings_finder import find_postings_by_search_term
        find_postings_by_search_term("python developer")

    call_headers = mock_get.call_args[1]["headers"]
    assert "Authorization" not in call_headers


def test_find_all_postings_for_company_fetches_token_once_and_passes_it():
    with patch("app.services.postings_finder.get_app_token", return_value="shared_tok") as mock_token, \
         patch("app.services.postings_finder.find_postings_by_search_term", return_value=[]) as mock_find:
        from app.services.postings_finder import find_all_postings_for_company
        find_all_postings_for_company(["Acme Ltd", "Acme"])

    mock_token.assert_called_once()
    assert mock_find.call_count == 2
    for call in mock_find.call_args_list:
        assert call[1].get("token") == "shared_tok"
