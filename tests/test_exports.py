"""Export workflow tests with real rolled-back database writes and fake Trello."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from adapters.trello.client import TrelloClient
from services.api import exports
from services.api.export_clients import get_export_client
from services.api import trello_auth
from services.api.server import app
from services.db import get_session
from services.models import AnalyticsEvent, Board, Import, Meeting, Story
from starlette.requests import Request


@pytest.fixture
def export_case(database_connection, pipeline_results):
    def new_session():
        return Session(bind=database_connection, join_transaction_mode="create_savepoint")

    with new_session() as session:
        board = Board(provider="trello", external_id=f"synthetic-{uuid4()}", name="Export test")
        session.add(board)
        session.flush()
        meeting = Meeting(board_id=board.id, title="Synthetic meeting")
        session.add(meeting)
        session.flush()
        story = Story(
            meeting_id=meeting.id, draft=pipeline_results[1].stories[0].model_dump(mode="json"),
            status="accepted", confidence=0.9, invest_score=5,
        )
        session.add(story)
        session.commit()
        board_id, story_id = board.id, story.id

    trello = Mock(spec=TrelloClient)
    trello.find_import_by_key.return_value = None
    trello.list_lists.return_value = [{"id": "list-1", "name": "Triage"}]
    trello.list_labels.return_value = [{"id": "label-1", "name": "AI Draft"}]
    trello.create_label.return_value = {"id": "new-label", "name": "AI Draft"}
    trello.create_card.return_value = {"id": "card-1"}
    trello.format_story.side_effect = TrelloClient.format_story
    trello.list_attachments.return_value = []
    trello.list_checklists.return_value = []
    trello.create_checklist.side_effect = lambda card_id, name: {"id": name, "name": name, "checkItems": []}

    def session_dependency():
        with new_session() as session:
            yield session

    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[exports.get_trello_client] = lambda: trello
    app.dependency_overrides[get_export_client] = lambda: trello
    try:
        with TestClient(app) as api:
            yield SimpleNamespace(
                api=api, trello=trello, board_id=board_id, story_id=story_id,
                session=new_session,
                url=f"/api/boards/{board_id}/imports",
                body={
                    "story_id": str(story_id), "list_id": "list-1",
                    "evidence_url": "https://example.com/meetings/synthetic",
                },
            )
    finally:
        app.dependency_overrides = previous_overrides


def import_record(case):
    with case.session() as session:
        return session.exec(select(Import).where(Import.story_id == case.story_id)).first()


def test_export_updates_database_and_repeat_returns_same_card(export_case):
    case = export_case
    response = case.api.post(case.url, json=case.body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "completed"
    assert result["external_card_id"] == "card-1"
    assert result["idempotency_key"] == f"trello:{case.story_id}"
    case.trello.create_card.assert_called_once()
    case.trello.add_label.assert_called_once_with("card-1", "label-1")
    case.trello.attach_evidence_link.assert_called_once_with("card-1", case.body["evidence_url"])
    call_count = len(case.trello.mock_calls)

    repeated = case.api.post(case.url, json=case.body)
    assert repeated.json() == result
    assert len(case.trello.mock_calls) == call_count
    with case.session() as session:
        assert session.get(Story, case.story_id).status == "imported"
        events = session.exec(select(AnalyticsEvent).where(
            AnalyticsEvent.story_id == case.story_id,
            AnalyticsEvent.event_type == "story_imported",
        )).all()
        assert len(events) == 1


@pytest.mark.parametrize("status", ["draft", "needs_changes", "rejected"])
def test_export_requires_human_acceptance(export_case, status):
    case = export_case
    with case.session() as session:
        session.get(Story, case.story_id).status = status
        session.commit()
    assert case.api.post(case.url, json=case.body).status_code == 409
    assert case.trello.mock_calls == []
    assert import_record(case) is None


def test_export_rejects_story_from_another_board(export_case):
    case = export_case
    with case.session() as session:
        board = Board(provider="trello", external_id=str(uuid4()), name="Other board")
        session.add(board)
        session.commit()
        other_board_id = board.id
    assert case.api.post(f"/api/boards/{other_board_id}/imports", json=case.body).status_code == 404
    case.trello.create_card.assert_not_called()


def test_export_requires_an_open_list_on_the_target_board(export_case):
    case = export_case
    case.trello.list_lists.return_value = [{"id": "different-list", "name": "Triage"}]
    assert case.api.post(case.url, json=case.body).status_code == 409
    case.trello.create_card.assert_not_called()
    assert import_record(case) is None


def test_export_creates_missing_ai_draft_label(export_case):
    case = export_case
    case.trello.list_labels.return_value = []
    assert case.api.post(case.url, json=case.body).status_code == 200
    case.trello.create_label.assert_called_once()
    case.trello.add_label.assert_called_once_with("card-1", "new-label")


def test_existing_marker_card_is_reused(export_case):
    case = export_case
    case.trello.find_import_by_key.return_value = {"id": "recovered-card"}
    response = case.api.post(case.url, json=case.body)
    assert response.status_code == 200, response.text
    assert response.json()["external_card_id"] == "recovered-card"
    case.trello.create_card.assert_not_called()


def test_timeout_keeps_pending_and_retry_reconciles_without_recreating(export_case):
    case = export_case
    case.trello.create_card.side_effect = requests.Timeout("URL containing a secret token")
    first = case.api.post(case.url, json=case.body)
    assert first.status_code == 502
    assert "secret" not in first.text
    assert import_record(case).status == "pending"
    assert import_record(case).external_card_id is None

    # A missing marker is not proof that the original POST did nothing.
    assert case.api.post(case.url, json=case.body).status_code == 409
    assert case.trello.create_card.call_count == 1

    case.trello.find_import_by_key.return_value = {"id": "late-card"}
    retry = case.api.post(case.url, json=case.body)
    assert retry.status_code == 200, retry.text
    assert retry.json()["external_card_id"] == "late-card"
    assert case.trello.create_card.call_count == 1


def test_preflight_failure_does_not_leave_a_creation_reservation(export_case):
    case = export_case
    case.trello.list_labels.side_effect = requests.HTTPError("Synthetic 401 token=secret")
    response = case.api.post(case.url, json=case.body)
    assert response.status_code == 502
    assert "secret" not in response.text
    assert import_record(case) is None
    case.trello.create_card.assert_not_called()


def test_attachment_failure_preserves_card_id_and_retry_avoids_duplicate_link(export_case):
    case = export_case
    case.trello.attach_evidence_link.side_effect = requests.Timeout("Synthetic timeout")
    assert case.api.post(case.url, json=case.body).status_code == 502
    saved = import_record(case)
    assert saved.external_card_id == "card-1"
    assert saved.status == "pending"
    with case.session() as session:
        assert session.get(Story, case.story_id).status == "accepted"

    # The attachment succeeded remotely even though its response timed out.
    case.trello.list_attachments.return_value = [{"url": case.body["evidence_url"]}]
    response = case.api.post(case.url, json=case.body)
    assert response.status_code == 200, response.text
    assert case.trello.create_card.call_count == 1
    assert case.trello.attach_evidence_link.call_count == 1


def test_pending_export_blocks_editing_and_review_changes(export_case, pipeline_results):
    case = export_case
    case.trello.create_card.side_effect = requests.Timeout()
    assert case.api.post(case.url, json=case.body).status_code == 502
    story_url = f"/api/stories/{case.story_id}"
    assert case.api.patch(story_url, json={"draft": pipeline_results[1].stories[0].model_dump()}).status_code == 409
    assert case.api.post(f"{story_url}/decision", json={"decision": "reject"}).status_code == 409


def test_export_rejects_non_trello_board(export_case):
    case = export_case
    with case.session() as session:
        session.get(Board, case.board_id).provider = "github"
        session.commit()
    assert case.api.post(case.url, json=case.body).status_code == 409
    case.trello.create_card.assert_not_called()


def test_missing_trello_credentials_are_reported_without_network(monkeypatch):
    monkeypatch.setattr(trello_auth, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("TRELLO_API_KEY", raising=False)
    with pytest.raises(HTTPException) as error:
        exports.get_trello_client(Request({"type": "http", "headers": []}), Mock())
    assert error.value.status_code == 503


def test_import_status_is_available_without_retrying_trello(export_case):
    case = export_case
    story_import_url = f"/api/stories/{case.story_id}/import"
    history_url = f"/api/boards/{case.board_id}/imports"
    assert case.api.get(story_import_url).status_code == 404
    assert case.api.get(history_url).json() == []

    case.trello.create_card.side_effect = requests.Timeout()
    assert case.api.post(case.url, json=case.body).status_code == 502
    previous_calls = len(case.trello.mock_calls)
    response = case.api.get(story_import_url)
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert case.api.get(history_url, params={"status": "pending"}).json() == [response.json()]
    assert case.api.get(history_url, params={"status": "completed"}).json() == []
    assert case.api.get(history_url, params={"offset": 1}).json() == []
    assert len(case.trello.mock_calls) == previous_calls


def test_import_history_is_scoped_to_board_and_includes_completed_export(export_case):
    case = export_case
    result = case.api.post(case.url, json=case.body)
    assert result.status_code == 200
    assert case.api.get(f"/api/stories/{case.story_id}/import").json() == result.json()
    assert case.api.get(f"/api/boards/{case.board_id}/imports").json() == [result.json()]
    with case.session() as session:
        other_board = Board(provider="trello", external_id=str(uuid4()), name="Other")
        session.add(other_board)
        session.commit()
        other_id = other_board.id
    assert case.api.get(f"/api/boards/{other_id}/imports").json() == []
    assert case.api.get(f"/api/boards/{uuid4()}/imports").status_code == 404
    assert case.api.get(f"/api/stories/{uuid4()}/import").status_code == 404


def test_export_creates_separate_criteria_and_solution_checklists(export_case, pipeline_results):
    case = export_case
    assert case.api.post(case.url, json=case.body).status_code == 200
    names = [call.args[1] for call in case.trello.create_checklist.call_args_list]
    assert names == ["Acceptance Criteria", "Solution Tasks (proposed)"]
    draft = pipeline_results[1].stories[0]
    added = [(call.args[0], call.args[1]) for call in case.trello.add_checklist_item.call_args_list]
    assert added == [
        *[("Acceptance Criteria", criterion) for criterion in draft.acceptance_criteria],
        *[("Solution Tasks (proposed)", task) for task in draft.solution_tasks],
    ]


def test_checklist_failure_keeps_card_id_and_retry_skips_existing_checks(export_case, pipeline_results):
    case = export_case
    draft = pipeline_results[1].stories[0]
    case.trello.add_checklist_item.side_effect = [None, requests.Timeout("Synthetic timeout")]
    assert case.api.post(case.url, json=case.body).status_code == 502
    assert import_record(case).external_card_id == "card-1"
    assert import_record(case).status == "pending"

    # The timed-out addition succeeded in Trello, and the first item is checked.
    case.trello.list_checklists.return_value = [{
        "id": "existing-criteria", "name": "Acceptance Criteria", "checkItems": [
            {"name": draft.acceptance_criteria[0], "state": "complete"},
            {"name": draft.acceptance_criteria[1], "state": "incomplete"},
        ],
    }]
    case.trello.add_checklist_item.side_effect = None
    case.trello.add_checklist_item.reset_mock()
    assert case.api.post(case.url, json=case.body).status_code == 200
    case.trello.create_card.assert_called_once()
    assert [call.args[1] for call in case.trello.add_checklist_item.call_args_list] == draft.solution_tasks
