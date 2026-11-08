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
    # The client sends through its own Session, so calls are reused connections
    monkeypatch.setattr(requests.Session, "request", lambda self, *args, **kwargs: request(*args, **kwargs))
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
    ("list_checklists", ("card-1",), "GET", "cards/card-1/checklists", {"checkItems": "all"}),
    ("create_checklist", ("card-1", "Acceptance Criteria"), "POST", "cards/card-1/checklists", {"name": "Acceptance Criteria"}),
    ("add_checklist_item", ("checklist-1", "Verify result"), "POST", "checklists/checklist-1/checkItems", {"name": "Verify result", "checked": "false"}),
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


def test_card_description_is_sent_exactly_as_given(client, http):
    request, _ = http
    client.create_card("list-1", "Reset password", "Description\n")
    assert request.call_args.kwargs["json"] == {
        "idList": "list-1", "name": "Reset password", "desc": "Description\n",
    }
    assert "desc" not in request.call_args.kwargs["params"]


def test_cards_are_never_created_with_an_import_key_in_the_description(client, http):
    with pytest.raises(TypeError):
        client.create_card("list-1", "Title", "Description", idempotency_key="story-1")
    http[0].assert_not_called()


def test_existing_card_is_found_by_title_and_description_including_archived(client, http):
    request, response = http
    description = "As a user, I want X so that Y\n\nSource Quote:\n\"quote\"\n"
    match = {"id": "archived-card", "closed": True, "name": "Reset password", "desc": description}
    response.json.return_value = [
        {"id": "other-title", "name": "Something else", "desc": description},
        {"id": "other-text", "name": "Reset password", "desc": description + "edited"},
        match,
    ]
    assert client.find_existing_card("board-1", "Reset password", description) == match
    assert request.call_args.kwargs["params"]["filter"] == "all"


def test_card_match_ignores_trailing_whitespace_and_windows_line_endings(client, http):
    stored = {"id": "card-1", "name": "Title", "desc": "Line one\r\nLine two  \r\n\r\n"}
    http[1].json.return_value = [stored]
    assert client.find_existing_card("board-1", "Title", "Line one\nLine two") == stored


def test_cards_linked_to_other_imports_are_never_matched(client, http):
    twin = {"id": "taken", "name": "Title", "desc": "Same text"}
    free = {"id": "free", "name": "Title", "desc": "Same text"}
    http[1].json.return_value = [twin, free]
    assert client.find_existing_card("board-1", "Title", "Same text", exclude_ids={"taken"}) == free
    assert client.find_existing_card("board-1", "Title", "Same text", exclude_ids={"taken", "free"}) is None


def test_cards_made_with_the_old_import_key_are_still_recognized(client, http):
    old = {"id": "old-card", "name": "Title", "desc": "Text\n\nBacklogger-Import-Key: story-1"}
    edited = {"id": "edited-card", "name": "Renamed", "desc": "Changed\nBacklogger-Import-Key: story-2\n"}
    wrong = {"id": "wrong", "name": "Title", "desc": "Text Backlogger-Import-Key: story-1"}
    http[1].json.return_value = [wrong, old, edited]
    assert client.find_existing_card("board-1", "Title", "Text\n", legacy_key="story-1") == old
    # the old marker line alone is enough, even if the card was renamed and edited afterwards
    assert client.find_existing_card("board-1", "Title", "Text\n", legacy_key="story-2") == edited
    # without a legacy key, the marker means nothing
    assert client.find_existing_card("board-1", "Nope", "Nope") is None


def test_existing_card_lookup_returns_none_when_missing(client, http):
    http[1].json.return_value = [{"id": "card-without-description", "name": "Title"}]
    assert client.find_existing_card("board-1", "Title", "Description") is None


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


def test_story_format_includes_criteria_quote_with_its_start_time_and_zero_estimate(client, pipeline_results):
    story = pipeline_results[1].stories[0].model_copy(update={"estimate": 0, "epic": "Accounts"})
    description = client.format_story(story)
    assert "Acceptance Criteria checklist" in description
    assert "Solution Tasks (proposed) checklist" in description
    # one line: the quote, then only when it begins
    assert f'Source Quote: "{story.source.quote}" (0:02)' in description
    assert "Start Time" not in description and "End Time" not in description and "Speaker" not in description
    assert "Estimate: 0 points" in description
    assert "Epic: Accounts" in description


def test_one_client_reuses_a_single_connection_for_all_its_calls(monkeypatch):
    sessions = []
    response = Mock(content=b"{}")
    response.json.return_value = {}
    monkeypatch.setattr(
        requests.Session, "request", lambda self, *args, **kwargs: sessions.append(self) or response,
    )
    client = TrelloClient("synthetic-key", "synthetic-token")
    client.list_lists("board-1")
    client.list_labels("board-1")
    client.create_checklist("card-1", "Acceptance Criteria")
    assert len(sessions) == 3 and len({id(session) for session in sessions}) == 1


def test_requests_for_one_token_wait_once_the_trello_limit_is_reached(http, monkeypatch):
    from adapters.trello import client as trello_client

    now, slept = [1000.0], []
    monkeypatch.setattr(trello_client.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(trello_client.time, "sleep", lambda seconds: (slept.append(seconds), now.__setitem__(0, now[0] + seconds)))
    first, second = TrelloClient("synthetic-key", "token-a"), TrelloClient("synthetic-key", "token-a")
    for _ in range(trello_client.RATE_LIMIT_REQUESTS):
        first.list_lists("board-1")
    assert slept == []
    # a different client with the same token shares the budget, so the next call must wait out the window
    second.list_lists("board-1")
    assert slept == [pytest.approx(trello_client.RATE_LIMIT_WINDOW_SECONDS)]
    # and a different token has a budget of its own
    TrelloClient("synthetic-key", "token-b").list_lists("board-1")
    assert len(slept) == 1
