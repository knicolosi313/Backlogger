"""Trello discovery routes; fake clients keep credentials and network calls out."""

from unittest.mock import Mock

import pytest
import requests
from fastapi.testclient import TestClient

from adapters.trello.client import TrelloClient
from services.api import exports
from services.api.exports import get_trello_client
from services.api.server import app


@pytest.fixture
def discovery():
    trello = Mock(spec=TrelloClient)
    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_trello_client] = lambda: trello
    try:
        with TestClient(app) as api:
            yield api, trello
    finally:
        app.dependency_overrides = previous_overrides


def test_board_discovery_returns_external_ids_and_filters_closed_boards(discovery):
    api, trello = discovery
    trello.list_boards.return_value = [
        {"id": "external1", "name": "Active", "closed": False,
         "url": "https://trello.com/b/example", "private_metadata": "omit"},
        {"id": "external2", "name": "Archived", "closed": True},
    ]
    response = api.get("/api/integrations/trello/boards")
    assert response.status_code == 200
    assert response.json() == [{
        "id": "external1", "name": "Active", "closed": False,
        "url": "https://trello.com/b/example",
    }]
    trello.list_boards.assert_called_once_with()


def test_list_discovery_uses_trello_id_and_returns_only_open_lists(discovery):
    api, trello = discovery
    trello.list_lists.return_value = [
        {"id": "list1", "name": "Triage", "closed": False, "idBoard": "external1"},
        {"id": "list2", "name": "Archived", "closed": True},
    ]
    response = api.get("/api/integrations/trello/boards/external1/lists")
    assert response.status_code == 200
    assert response.json() == [{"id": "list1", "name": "Triage"}]
    trello.list_lists.assert_called_once_with("external1")


@pytest.mark.parametrize("route,method", [
    ("/api/integrations/trello/boards", "list_boards"),
    ("/api/integrations/trello/boards/external1/lists", "list_lists"),
])
def test_discovery_can_return_an_empty_list(discovery, route, method):
    api, trello = discovery
    getattr(trello, method).return_value = []
    assert api.get(route).json() == []


@pytest.mark.parametrize("trello_status,api_status", [(401, 502), (403, 502), (404, 404), (429, 503), (500, 502)])
def test_trello_errors_are_sanitized(discovery, trello_status, api_status):
    api, trello = discovery
    response = requests.Response()
    response.status_code = trello_status
    trello.list_boards.side_effect = requests.HTTPError(
        "https://api.trello.com/?token=SECRET", response=response,
    )
    result = api.get("/api/integrations/trello/boards")
    assert result.status_code == api_status
    assert "SECRET" not in result.text


def test_trello_timeout_returns_504(discovery):
    api, trello = discovery
    trello.list_lists.side_effect = requests.Timeout("token=SECRET")
    result = api.get("/api/integrations/trello/boards/external1/lists")
    assert result.status_code == 504
    assert "SECRET" not in result.text


def test_invalid_external_id_is_rejected_before_calling_trello(discovery):
    api, trello = discovery
    assert api.get("/api/integrations/trello/boards/not-an-id/lists").status_code == 422
    trello.list_lists.assert_not_called()


def test_missing_configuration_returns_503(monkeypatch):
    monkeypatch.setattr(exports, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("TRELLO_API_KEY", raising=False)
    monkeypatch.delenv("TRELLO_TOKEN", raising=False)
    with TestClient(app) as api:
        response = api.get("/api/integrations/trello/boards")
    assert response.status_code == 503
