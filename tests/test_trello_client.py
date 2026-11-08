"""Trello adapter tests with mocked HTTP responses; no real boards are changed."""

from unittest.mock import Mock

import pytest
import requests

from adapters.trello.client import TrelloClient


@pytest.fixture
def http(monkeypatch):
    response = Mock()
    response.content = b"{}"
    response.json.return_value = {"id": "synthetic-id"}
    request = Mock(return_value=response)
    monkeypatch.setattr(requests, "request", request)
    return request, response


@pytest.fixture
def client():
    return TrelloClient("synthetic-key", "synthetic-token")


@pytest.mark.parametrize("method,args,verb,path,parameters", [
    ("list_boards", (), "GET", "members/me/boards", {}),
    ("get_current_member", (), "GET", "members/me", {"fields": "id,username"}),
    ("list_lists", ("board-1",), "GET", "boards/board-1/lists", {"filter": "open"}),
    ("list_labels", ("board-1",), "GET", "boards/board-1/labels", {"fields": "id,name,color", "limit": 1000}),
    ("create_label", ("board-1", "AI Draft", "yellow"), "POST", "labels",
     {"idBoard": "board-1", "name": "AI Draft", "color": "yellow"}),
    ("add_label", ("card-1", "label-1"), "POST", "cards/card-1/idLabels", {"value": "label-1"}),
    ("list_attachments", ("card-1",), "GET", "cards/card-1/attachments", {}),
    ("list_checklists", ("card-1",), "GET", "cards/card-1/checklists", {"checkItems": "all"}),
    ("create_checklist", ("card-1", "Acceptance Criteria"), "POST", "cards/card-1/checklists", {"name": "Acceptance Criteria"}),
    ("add_checklist_item", ("checklist-1", "Verify result"), "POST", "checklists/checklist-1/checkItems", {"name": "Verify result", "checked": "false"}),
    ("attach_evidence_link", ("card-1", "https://example.com/meeting/1", "Source"),
     "POST", "cards/card-1/attachments", {"url": "https://example.com/meeting/1", "name": "Source"}),
])
def test_methods_send_the_right_request(client, http, method, args, verb, path, parameters):
    request, response = http
    assert getattr(client, method)(*args) == response.json.return_value
    request.assert_called_once_with(
        verb, f"https://api.trello.com/1/{path}",
        params={**parameters, "key": "synthetic-key", "token": "synthetic-token"},
        json=None, timeout=10,
    )
    response.raise_for_status.assert_called_once()


def test_card_payload_contains_exact_import_marker(client, http):
    request, _ = http
    client.create_card("list-1", "Reset password", "Description\n", idempotency_key="story-1")
    assert request.call_args.kwargs["json"] == {
        "idList": "list-1", "name": "Reset password",
        "desc": "Description\n\nBacklogger-Import-Key: story-1",
    }
    assert "desc" not in request.call_args.kwargs["params"]


def test_card_without_import_key_keeps_description(client, http):
    client.create_card("list-1", "Title", "Description\n")
    assert http[0].call_args.kwargs["json"]["desc"] == "Description\n"


@pytest.mark.parametrize("key", ["", " ", "first\nsecond", "first\rsecond"])
def test_invalid_import_key_never_creates_a_card(client, http, key):
    with pytest.raises(ValueError, match="single line"):
        client.create_card("list-1", "Title", "Description", idempotency_key=key)
    http[0].assert_not_called()


def test_import_lookup_matches_whole_line_and_includes_archived_cards(client, http):
    request, response = http
    match = {"id": "archived-card", "closed": True, "desc": "Text\nBacklogger-Import-Key: story-1\n"}
    response.json.return_value = [
        {"id": "wrong", "desc": "Backlogger-Import-Key: story-10"},
        {"id": "inline", "desc": "Text Backlogger-Import-Key: story-1"},
        match,
    ]
    assert client.find_import_by_key("board-1", "story-1") == match
    assert request.call_args.kwargs["params"]["filter"] == "all"


def test_import_lookup_returns_none_when_missing(client, http):
    http[1].json.return_value = [{"id": "card-without-description"}]
    assert client.find_import_by_key("board-1", "story-1") is None


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_http_errors_are_raised_without_retrying_card_creation(client, http, status):
    error = requests.HTTPError(f"Synthetic HTTP {status}")
    http[1].raise_for_status.side_effect = error
    with pytest.raises(requests.HTTPError) as caught:
        client.create_card("list-1", "Title", "Description")
    assert caught.value is error
    http[0].assert_called_once()
    http[1].json.assert_not_called()


def test_timeout_is_propagated_without_retrying(client, http):
    http[0].side_effect = requests.Timeout("Synthetic timeout")
    with pytest.raises(requests.Timeout):
        client.create_card("list-1", "Title", "Description")
    http[0].assert_called_once()


def test_empty_success_response_is_supported(client, http):
    http[1].content = b""
    assert client.add_label("card-1", "label-1") is None
    http[1].json.assert_not_called()


def test_story_format_includes_criteria_evidence_and_zero_estimate(client, pipeline_results):
    story = pipeline_results[1].stories[0].model_copy(update={"estimate": 0, "epic": "Accounts"})
    description = client.format_story(story)
    assert "Acceptance Criteria checklist" in description
    assert "Solution Tasks (proposed) checklist" in description
    assert story.source.quote in description
    assert "Speaker: Alex" in description
    assert "Start Time: 2.00s" in description
    assert "Estimate: 0 points" in description
    assert "Epic: Accounts" in description
