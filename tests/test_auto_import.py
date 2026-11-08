"""Eligibility and opted-in exports; provider calls are fake and DB writes roll back."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
import requests
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from adapters.jira.client import JiraClient
from adapters.trello.client import TrelloClient
from services.api import auto_import, jira_auth, jobs, trello_auth
from services.api.server import app
from services.db import get_session
from services.models import AnalyticsEvent, AutoImportRun, Board, Import, JiraConnection, Meeting, Story, TrelloConnection, utc_now
from services.worker import pipeline


@pytest.fixture
def eligible(pipeline_results):
    review = pipeline_results[2].reviews[0].model_copy(update={"flags": [], "invest_score": 6, "confidence": 0.95})
    return Story(meeting_id=uuid4(), draft=pipeline_results[1].stories[0].model_dump(mode="json"), critique=review.model_dump(mode="json"), confidence=0.95, invest_score=6)


def test_eligibility_requires_threshold_and_complete_review(eligible):
    assert auto_import.eligible_story(eligible, 0.95)
    assert not auto_import.eligible_story(eligible, 0.96)
    eligible.status = "accepted"
    assert not auto_import.eligible_story(eligible, 0.9)
    assert auto_import.eligible_story(eligible, 0.9, retry=True)


@pytest.mark.parametrize("problem", [
    "proposed", "inferred", "assumptions", "clarification", "quote", "timestamp", "criteria",
    "critique", "flag", "scope", "split", "reject", "confidence", "score", "rejected", "review_score",
])
def test_uncertain_or_invalid_stories_are_left_for_review(eligible, problem):
    if problem in {"proposed", "inferred"}:
        eligible.draft = eligible.draft | {"origin": problem, "needs_clarification": False}
    elif problem == "assumptions":
        eligible.draft = eligible.draft | {"assumptions": ["Unconfirmed behavior"]}
    elif problem == "clarification":
        eligible.draft = eligible.draft | {"needs_clarification": True}
    elif problem == "quote":
        eligible.draft["source"]["quote"] = " "
    elif problem == "timestamp":
        eligible.draft["source"]["end_s"] = 0
    elif problem == "criteria":
        eligible.draft["acceptance_criteria"] = []
    elif problem == "critique":
        eligible.critique = None
    elif problem == "flag":
        eligible.critique["flags"] = [{"criterion": "Independent", "issue": "Overlap"}]
    elif problem == "scope":
        eligible.critique["relationships"] = [{"other_story_index": 1, "relationship": "contains", "reason": "Overlaps other work"}]
    elif problem in {"split", "reject"}:
        eligible.critique[f"recommend_{problem}"] = True
    elif problem == "confidence":
        eligible.confidence = float("nan")
    elif problem == "score":
        eligible.invest_score = 5
    elif problem == "review_score":
        eligible.critique["invest_score"] = 5
    else:
        eligible.status = "rejected"
    assert not auto_import.eligible_story(eligible, 0.5, retry=True)


@pytest.fixture(params=["trello", "jira"])
def auto_case(request, database_connection, pipeline_results, monkeypatch, tmp_path):
    provider = request.param
    def session_factory(*args):
        return Session(bind=database_connection, join_transaction_mode="create_savepoint")
    def dependency():
        with session_factory() as session:
            yield session
    key = Fernet.generate_key()
    cookie = "synthetic-browser-session"
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key.decode())
    monkeypatch.setenv("TRELLO_API_KEY", "synthetic-key")
    monkeypatch.setenv("JIRA_CLIENT_ID", "synthetic-client")
    monkeypatch.setenv("JIRA_CLIENT_SECRET", "synthetic-secret")
    monkeypatch.setenv("MEDIA_STORAGE", "local")
    monkeypatch.setenv("MEDIA_LOCAL_PATH", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-gemini-key")
    monkeypatch.setattr(trello_auth, "load_dotenv", lambda *args, **kwargs: None)
    cloud_id = str(uuid4())
    cipher = Fernet(key)
    account_type = TrelloConnection if provider == "trello" else JiraConnection
    with session_factory() as session:
        board = Board(provider=provider, external_id=uuid4().hex if provider == "trello" else f"{cloud_id}:10001", name="Test destination")
        session.add(board)
        session.flush()
        meeting = Meeting(board_id=board.id, title="Auto-import test")
        account = account_type(session_hash=trello_auth.hash_secret(cookie), encrypted_token=cipher.encrypt(b"synthetic-token").decode(), expires_at=utc_now() + timedelta(days=1))
        if provider == "trello":
            account.member_id = "original-account"
        else:
            account.account_id = "original-account"
        session.add_all([meeting, account])
        session.commit()
        meeting_id, board_id, account_id = meeting.id, board.id, account.id
    review = pipeline_results[2].reviews[0]
    review.flags, review.invest_score, review.confidence = [], 6, 0.95
    def fake_pipeline(audio_path, model, save_stage, **kwargs):
        for stage, value in zip(["transcribe", "extract", "critique"], pipeline_results):
            save_stage(stage, value)
        return pipeline_results
    monkeypatch.setattr(pipeline, "Session", session_factory)
    monkeypatch.setattr(pipeline, "_run_pipeline", fake_pipeline)
    monkeypatch.setattr(jobs, "Session", session_factory)
    monkeypatch.setattr(auto_import, "Session", session_factory)
    client = Mock(spec=TrelloClient if provider == "trello" else JiraClient)
    if provider == "trello":
        client.list_lists.return_value = [{"id": "list1", "name": "Triage"}]
        client.find_existing_card.return_value = None
        client.list_labels.return_value = [{"id": "label1", "name": "AI Draft"}]
        client.create_card.return_value = {"id": "card1"}
        client.list_checklists.return_value = []
        client.create_checklist.side_effect = lambda card_id, name: {"id": name, "name": name}
        client.format_story.side_effect = TrelloClient.format_story
        monkeypatch.setattr(trello_auth, "TrelloClient", Mock(return_value=client))
        destination = {"list_id": "list1"}
    else:
        client.list_sites.return_value = [{"id": cloud_id}]
        client.get_project.return_value = {"id": "10001"}
        client.get_create_fields.return_value = {"fields": [], "total": 0}
        client.find_import_by_key.return_value = None
        client.create_issue.return_value = {"key": "TEST-1", "id": "10020"}
        client.format_story.side_effect = JiraClient.format_story
        monkeypatch.setattr(jira_auth, "JiraClient", Mock(return_value=client))
        destination = {"issue_type_id": "10002", "additional_fields": {}}
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = dependency
    try:
        with TestClient(app) as browser:
            browser.cookies.set(trello_auth.COOKIE_NAME if provider == "trello" else jira_auth.COOKIE_NAME, cookie)
            url = f"/api/meetings/{meeting_id}"
            assert browser.post(f"{url}/media", files={"file": ("audio.webm", b"synthetic audio", "audio/webm")}).status_code == 200
            yield SimpleNamespace(
                api=browser, client=client, url=url, provider=provider, board_id=board_id, meeting_id=meeting_id,
                account_id=account_id, session=session_factory, results=pipeline_results,
                body={"creativity": "balanced", "auto_import": {"confidence_threshold": 0.85, "evidence_base_url": "https://api.example.test", **destination}},
            )
    finally:
        app.dependency_overrides = previous


def test_auto_import_runs_after_processing_and_records_status(auto_case):
    case = auto_case
    response = case.api.post(f"{case.url}/process", json=case.body)
    assert response.status_code == 202, response.text
    result = case.api.get(f"{case.url}/auto-import")
    assert result.status_code == 200, result.text
    assert {key: result.json()[key] for key in ("status", "imported", "skipped", "failed")} == {
        "status": "completed", "imported": 1, "skipped": 0, "failed": 0,
    }
    assert "connection_id" not in result.json() and "options" not in result.json()
    with case.session() as session:
        story = session.exec(select(Story).where(Story.meeting_id == case.meeting_id)).one()
        assert story.status == "imported"
        assert session.exec(select(Import).where(Import.story_id == story.id)).one().status == "completed"
        events = session.exec(select(AnalyticsEvent).where(AnalyticsEvent.story_id == story.id)).all()
        assert sum(event.event_type == "story_auto_accepted" for event in events) == 1
    # Repeated completed retry cannot create another card/issue.
    assert case.api.post(f"{case.url}/auto-import/retry").json()["status"] == "completed"
    create = case.client.create_card if case.provider == "trello" else case.client.create_issue
    assert create.call_count == 1


def test_auto_import_stays_off_when_not_requested(auto_case):
    case = auto_case
    assert case.api.post(f"{case.url}/process", json={"creativity": "balanced"}).status_code == 202
    assert case.api.get(f"{case.url}/auto-import").status_code == 404
    create = case.client.create_card if case.provider == "trello" else case.client.create_issue
    create.assert_not_called()


def test_proposals_stay_in_drafts_even_with_high_model_confidence(auto_case):
    case = auto_case
    case.results[1].stories[0].origin = "proposed"
    assert case.api.post(f"{case.url}/process", json=case.body).status_code == 202
    result = case.api.get(f"{case.url}/auto-import").json()
    assert result["imported"] == 0 and result["skipped"] == 1
    with case.session() as session:
        assert session.exec(select(Story).where(Story.meeting_id == case.meeting_id)).one().status == "draft"


def test_processing_failure_does_not_start_imports(auto_case, monkeypatch):
    case = auto_case
    monkeypatch.setattr(jobs, "run_pipeline", Mock(side_effect=RuntimeError("Synthetic failure")))
    assert case.api.post(f"{case.url}/process", json=case.body).status_code == 202
    assert case.api.get(f"{case.url}/auto-import").json()["status"] == "failed"
    assert case.api.post(f"{case.url}/auto-import/retry").status_code == 409
    create = case.client.create_card if case.provider == "trello" else case.client.create_issue
    create.assert_not_called()


def test_another_browser_cannot_read_or_retry_the_run(auto_case):
    case = auto_case
    assert case.api.post(f"{case.url}/process", json=case.body).status_code == 202
    case.api.cookies.clear()
    assert case.api.get(f"{case.url}/auto-import").status_code == 403
    assert case.api.post(f"{case.url}/auto-import/retry").status_code == 403


def test_destination_mismatch_is_rejected_before_processing(auto_case):
    case = auto_case
    options = case.body["auto_import"]
    if case.provider == "trello":
        del options["list_id"]
        options["issue_type_id"] = "10002"
    else:
        del options["issue_type_id"]
        options["list_id"] = "list1"
    assert case.api.post(f"{case.url}/process", json=case.body).status_code == 409
    assert case.api.get(case.url).json()["status"] == "uploaded"


def test_failed_export_can_resume_without_duplicate_creation(auto_case):
    case = auto_case
    if case.provider == "trello":
        case.client.add_label.side_effect = [requests.Timeout("secret=NEVER_ECHO"), None]
    else:
        # Preflight succeeds; the first export's project check fails before issue creation.
        case.client.get_project.side_effect = [{"id": "10001"}, requests.Timeout("secret=NEVER_ECHO"), {"id": "10001"}]
    assert case.api.post(f"{case.url}/process", json=case.body).status_code == 202
    result = case.api.get(f"{case.url}/auto-import")
    assert result.json()["status"] == "failed" and result.json()["failed"] == 1
    assert "NEVER_ECHO" not in result.text
    assert case.api.get(case.url).json()["status"] == "completed"
    response = case.api.post(f"{case.url}/auto-import/retry")
    assert response.status_code == 202, response.text
    result = case.api.get(f"{case.url}/auto-import").json()
    assert result["status"] == "completed" and result["imported"] == 1 and result["failed"] == 0
    create = case.client.create_card if case.provider == "trello" else case.client.create_issue
    assert create.call_count == 1


def test_partial_failure_keeps_exporting_and_retry_reuses_completed_work(auto_case):
    case = auto_case
    extra = case.results[1].stories[0].model_copy(update={"title": "Another fully reviewed outcome"}, deep=True)
    case.results[1].stories.append(extra)
    case.results[2].reviews.append(case.results[2].reviews[0].model_copy(update={"index": 1}, deep=True))
    if case.provider == "trello":
        case.client.create_card.side_effect = [{"id": "card1"}, {"id": "card2"}]
        case.client.add_label.side_effect = [requests.Timeout("Synthetic failure"), None, None]
    else:
        case.client.create_issue.side_effect = [{"key": "TEST-1", "id": "10020"}, {"key": "TEST-2", "id": "10021"}]
        case.client.get_project.side_effect = [{"id": "10001"}, requests.Timeout("Synthetic failure"), {"id": "10001"}, {"id": "10001"}]
    assert case.api.post(f"{case.url}/process", json=case.body).status_code == 202
    result = case.api.get(f"{case.url}/auto-import").json()
    assert result["imported"] == 1 and result["failed"] == 1
    assert case.api.post(f"{case.url}/auto-import/retry").status_code == 202
    result = case.api.get(f"{case.url}/auto-import").json()
    assert result["status"] == "completed" and result["imported"] == 2 and result["failed"] == 0
    create = case.client.create_card if case.provider == "trello" else case.client.create_issue
    assert create.call_count == 2


def test_invalid_provider_destination_is_caught_before_pipeline_work(auto_case):
    case = auto_case
    if case.provider == "trello":
        case.client.list_lists.return_value = []
    else:
        case.client.get_create_fields.return_value = {"fields": [{"fieldId": "customfield_999", "required": True, "hasDefaultValue": False}], "total": 1}
    response = case.api.post(f"{case.url}/process", json=case.body)
    assert response.status_code == 409, response.text
    assert case.api.get(case.url).json()["status"] == "uploaded"
    assert case.api.get(f"{case.url}/auto-import").status_code == 404


def test_retry_waits_for_active_worker_but_can_resume_an_interrupted_run(auto_case):
    case = auto_case
    assert case.api.post(f"{case.url}/process", json=case.body).status_code == 202
    with case.session() as session:
        run = session.exec(select(AutoImportRun).where(AutoImportRun.meeting_id == case.meeting_id)).one()
        run.status, run.updated_at = "running", utc_now()
        session.commit()
    assert case.api.post(f"{case.url}/auto-import/retry").status_code == 409
    with case.session() as session:
        run = session.exec(select(AutoImportRun).where(AutoImportRun.meeting_id == case.meeting_id)).one()
        run.updated_at = utc_now() - timedelta(minutes=16)
        session.commit()
    assert case.api.post(f"{case.url}/auto-import/retry").status_code == 202
    assert case.api.get(f"{case.url}/auto-import").json()["status"] == "completed"
    create = case.client.create_card if case.provider == "trello" else case.client.create_issue
    assert create.call_count == 1


def test_reconnecting_as_another_account_cannot_redirect_pending_exports(auto_case, monkeypatch):
    case = auto_case
    original_pipeline = pipeline._run_pipeline
    def switch_account(*args, **kwargs):
        result = original_pipeline(*args, **kwargs)
        with case.session() as session:
            account = session.get(TrelloConnection if case.provider == "trello" else JiraConnection, case.account_id)
            if case.provider == "trello":
                account.member_id = "different-account"
            else:
                account.account_id = "different-account"
            session.commit()
        return result
    monkeypatch.setattr(pipeline, "_run_pipeline", switch_account)
    assert case.api.post(f"{case.url}/process", json=case.body).status_code == 202
    assert case.api.get(f"{case.url}/auto-import").status_code == 403
    with case.session() as session:
        run = session.exec(select(AutoImportRun).where(AutoImportRun.meeting_id == case.meeting_id)).one()
        assert run.status == "failed"
        assert session.exec(select(Story).where(Story.meeting_id == case.meeting_id)).one().status == "draft"
    create = case.client.create_card if case.provider == "trello" else case.client.create_issue
    create.assert_not_called()


def test_processing_retry_with_auto_import_off_removes_previous_opt_in(auto_case, monkeypatch):
    case = auto_case
    original = jobs.run_pipeline
    monkeypatch.setattr(jobs, "run_pipeline", Mock(side_effect=RuntimeError("Synthetic failure")))
    assert case.api.post(f"{case.url}/process", json=case.body).status_code == 202
    monkeypatch.setattr(jobs, "run_pipeline", original)
    assert case.api.post(f"{case.url}/process", json={"creativity": "balanced"}).status_code == 202
    assert case.api.get(f"{case.url}/auto-import").status_code == 404
    create = case.client.create_card if case.provider == "trello" else case.client.create_issue
    create.assert_not_called()


@pytest.mark.parametrize("options", [
    {"confidence_threshold": 0.4, "list_id": "list1"},
    {"confidence_threshold": 1.1, "list_id": "list1"},
    {"list_id": "list1", "issue_type_id": "10002"}, {},
    {"issue_type_id": "10002", "additional_fields": {"summary": "Override"}},
    {"list_id": "list1", "evidence_base_url": "https://user:password@example.test"},
])
def test_invalid_auto_import_settings_are_rejected(options):
    from pydantic import ValidationError
    from services.api.schemas import AutoImportOptions
    with pytest.raises(ValidationError):
        AutoImportOptions.model_validate({"evidence_base_url": "https://api.example.test", **options})
