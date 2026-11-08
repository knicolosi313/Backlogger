"""Jira exports with fake REST calls and rolled-back PostgreSQL transactions."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
import requests
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from adapters.jira.client import JiraClient
from services.api import export_clients
from services.api.server import app
from services.db import get_session
from services.models import AnalyticsEvent, Board, Import, Meeting, Story


@pytest.fixture
def jira_export(database_connection, pipeline_results):
    def new_session():
        return Session(bind=database_connection, join_transaction_mode="create_savepoint")

    cloud_id = str(uuid4())
    with new_session() as session:
        board = Board(provider="jira", external_id=f"{cloud_id}:10001", name="Jira test")
        session.add(board)
        session.flush()
        meeting = Meeting(board_id=board.id, title="Synthetic meeting")
        session.add(meeting)
        session.flush()
        story = Story(meeting_id=meeting.id, draft=pipeline_results[1].stories[0].model_dump(mode="json"), status="accepted")
        session.add(story)
        session.commit()
        board_id, story_id = board.id, story.id

    client = Mock(spec=JiraClient)
    client.list_sites.return_value = [{"id": cloud_id}]
    client.find_import_by_key.return_value = None
    client.get_create_fields.return_value = {"fields": [], "total": 0}
    client.create_issue.return_value = {"id": "10020", "key": "DEMO-20"}
    client.format_story.side_effect = JiraClient.format_story

    def dependency():
        with new_session() as session:
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = dependency
    app.dependency_overrides[export_clients.get_export_client] = lambda: client
    try:
        with TestClient(app) as api:
            yield SimpleNamespace(
                api=api, client=client, cloud_id=cloud_id, board_id=board_id, story_id=story_id,
                url=f"/api/boards/{board_id}/imports", session=new_session,
                body={"story_id": str(story_id), "issue_type_id": "10002"},
            )
    finally:
        app.dependency_overrides = previous


def test_export_creates_issue_and_repeat_returns_saved_key(jira_export):
    case = jira_export
    first = case.api.post(case.url, json=case.body)
    assert first.status_code == 200, first.text
    assert first.json()["external_card_id"] == "DEMO-20"
    assert first.json()["status"] == "completed"
    fields = case.client.create_issue.call_args.args[1]
    assert fields["project"] == {"id": "10001"}
    assert fields["issuetype"] == {"id": "10002"}
    assert f"backlogger-story-{case.story_id}" in fields["labels"]
    assert fields["description"]["type"] == "doc"
    assert 'Source Quote: "' in str(fields["description"]) and "(0:02)" in str(fields["description"])
    assert "Meeting evidence" not in str(fields["description"])
    assert case.api.post(case.url, json=case.body).json() == first.json()
    case.client.create_issue.assert_called_once()
    with case.session() as session:
        assert session.get(Story, case.story_id).status == "imported"
        assert len(session.exec(select(AnalyticsEvent).where(AnalyticsEvent.story_id == case.story_id)).all()) == 1


@pytest.mark.parametrize("status", ["draft", "rejected", "needs_changes"])
def test_export_requires_acceptance(jira_export, status):
    case = jira_export
    with case.session() as session:
        session.get(Story, case.story_id).status = status
        session.commit()
    assert case.api.post(case.url, json=case.body).status_code == 409
    case.client.create_issue.assert_not_called()


def test_unknown_site_is_rejected(jira_export):
    case = jira_export
    case.client.list_sites.return_value = []
    assert case.api.post(case.url, json=case.body).status_code == 404
    case.client.create_issue.assert_not_called()


def test_existing_issue_marker_is_recovered(jira_export):
    case = jira_export
    case.client.find_import_by_key.return_value = {"key": "DEMO-OLD"}
    response = case.api.post(case.url, json=case.body)
    assert response.status_code == 200, response.text
    assert response.json()["external_card_id"] == "DEMO-OLD"
    case.client.create_issue.assert_not_called()


def test_creation_timeout_is_reconciled_without_duplicate_issue(jira_export):
    case = jira_export
    case.client.create_issue.side_effect = requests.Timeout("secret-token")
    response = case.api.post(case.url, json=case.body)
    assert response.status_code == 502 and "secret-token" not in response.text
    with case.session() as session:
        assert session.exec(select(Import).where(Import.story_id == case.story_id)).one().status == "pending"
    assert case.api.post(case.url, json=case.body).status_code == 409
    case.client.create_issue.assert_called_once()
    case.client.find_import_by_key.return_value = {"key": "DEMO-LATE"}
    assert case.api.post(case.url, json=case.body).json()["external_card_id"] == "DEMO-LATE"
    case.client.create_issue.assert_called_once()


def test_required_fields_are_checked_before_reserving_creation(jira_export):
    case = jira_export
    case.client.get_create_fields.return_value = {
        "fields": [{"fieldId": "customfield_10001", "required": True, "hasDefaultValue": False}], "total": 1,
    }
    assert case.api.post(case.url, json=case.body).status_code == 409
    with case.session() as session:
        assert session.exec(select(Import).where(Import.story_id == case.story_id)).first() is None
    body = {**case.body, "additional_fields": {"customfield_10001": "Required value"}}
    response = case.api.post(case.url, json=body)
    assert response.status_code == 200, response.text
    assert case.client.create_issue.call_args.args[1]["customfield_10001"] == "Required value"


def test_core_fields_cannot_be_overridden(jira_export):
    case = jira_export
    body = {**case.body, "additional_fields": {"project": {"id": "different"}}}
    assert case.api.post(case.url, json=body).status_code == 422
    case.client.create_issue.assert_not_called()


def test_creation_validation_error_can_be_fixed_and_retried(jira_export):
    case = jira_export
    response = requests.Response()
    response.status_code = 400
    case.client.create_issue.side_effect = requests.HTTPError("Synthetic validation error", response=response)
    assert case.api.post(case.url, json=case.body).status_code == 502
    with case.session() as session:
        record = session.exec(select(Import).where(Import.story_id == case.story_id)).one()
        assert record.status == "failed" and record.external_card_id is None
    case.client.create_issue.side_effect = None
    assert case.api.post(case.url, json=case.body).status_code == 200


def test_provider_resolver_uses_jira_connection(jira_export, monkeypatch):
    case = jira_export
    jira = Mock(return_value=case.client)
    trello = Mock(side_effect=AssertionError("Wrong provider"))
    monkeypatch.setattr(export_clients, "get_jira_client", jira)
    monkeypatch.setattr(export_clients, "get_trello_client", trello)
    with case.session() as session:
        assert export_clients.get_export_client(case.board_id, Mock(), session) is case.client
    jira.assert_called_once()
    trello.assert_not_called()


def test_older_clients_may_still_send_an_evidence_link_but_it_is_not_used(jira_export):
    case = jira_export
    body = {**case.body, "evidence_url": "https://example.com/meeting"}
    assert case.api.post(case.url, json=body).status_code == 200
    fields = case.client.create_issue.call_args.args[1]
    assert "example.com" not in str(fields["description"])
