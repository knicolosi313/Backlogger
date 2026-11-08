"""API workflow tests using PostgreSQL transactions and fake Gemini output."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from services.api import jobs, meetings
from services.api.server import app
from services.db import get_session
from services.models import Meeting, Story, StoryRevision
from services.worker import pipeline


@pytest.fixture
def api(database_connection, monkeypatch, tmp_path, pipeline_results):
    def new_session(*args):
        return Session(bind=database_connection, join_transaction_mode="create_savepoint")

    def session_dependency():
        with new_session() as session:
            yield session

    def fake_gemini(audio_path, model, save_stage):
        for stage, output in zip(["transcribe", "extract", "critique"], pipeline_results):
            save_stage(stage, output)
        return pipeline_results

    monkeypatch.setenv("MEDIA_STORAGE", "local")
    monkeypatch.setenv("MEDIA_LOCAL_PATH", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-not-a-real-key")
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    monkeypatch.setattr(pipeline, "Session", new_session)
    monkeypatch.setattr(pipeline, "_run_pipeline", fake_gemini)
    monkeypatch.setattr(jobs, "Session", new_session)
    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = session_dependency
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides = previous_overrides


def create_board(client):
    response = client.post("/api/boards", json={
        "provider": "trello", "external_id": f"synthetic-{uuid4()}", "name": "Test board",
    })
    assert response.status_code == 201, response.text
    return response.json()


def create_meeting(client, board_id):
    response = client.post("/api/meetings", json={"board_id": board_id, "title": "Test meeting"})
    assert response.status_code == 201, response.text
    return response.json()


def upload_and_process(client, meeting_id):
    response = client.post(
        f"/api/meetings/{meeting_id}/media",
        files={"file": ("synthetic.webm", b"synthetic audio", "audio/webm;codecs=opus")},
    )
    assert response.status_code == 200, response.text
    response = client.post(f"/api/meetings/{meeting_id}/process")
    assert response.status_code == 202, response.text
    return response


def test_complete_upload_process_edit_and_review_workflow(
    api, pipeline_results, database_connection, monkeypatch,
):
    run_pipeline = jobs.run_pipeline

    def check_status_before_materializing(*args, **kwargs):
        results = run_pipeline(*args, **kwargs)
        with Session(bind=database_connection, join_transaction_mode="create_savepoint") as session:
            meeting = session.get(Meeting, kwargs["meeting_id"])
            assert meeting.status == "processing"
        return results

    monkeypatch.setattr(jobs, "run_pipeline", check_status_before_materializing)
    board = create_board(api)
    board_id = board["id"]
    assert api.get(f"/api/boards/{board_id}").json() == board
    assert any(item["id"] == board_id for item in api.get("/api/boards").json())
    empty_analytics = api.get(f"/api/boards/{board_id}/analytics").json()
    assert empty_analytics["acceptance_rate"] is None
    assert empty_analytics["draft_outcome_counts"] == {"accepted": 0, "edited": 0, "rejected": 0}
    assert empty_analytics["draft_outcome_percentages"] == {"accepted": 0.0, "edited": 0.0, "rejected": 0.0}

    meeting = create_meeting(api, board_id)
    meeting_id = meeting["id"]
    assert api.get("/api/meetings", params={"board_id": board_id}).json() == [meeting]
    assert api.get(f"/api/meetings/{meeting_id}/transcript").status_code == 404
    accepted = upload_and_process(api, meeting_id)
    assert accepted.json()["status_url"] == f"/api/meetings/{meeting_id}"
    assert api.get(f"/api/meetings/{meeting_id}").json()["status"] == "completed"
    assert api.get(f"/api/meetings/{meeting_id}/transcript").json() == pipeline_results[0].model_dump()

    stories = api.get(f"/api/boards/{board_id}/stories").json()
    assert len(stories) == 1
    story = stories[0]
    story_url = f"/api/stories/{story['id']}"
    assert story["draft"] == pipeline_results[1].stories[0].model_dump()
    assert story["critique"] == pipeline_results[2].reviews[0].model_dump()
    assert api.get(story_url).json() == story

    # Repeating a decision should not record a duplicate event.
    for _ in range(2):
        response = api.post(f"{story_url}/decision", json={"decision": "accept"})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "accepted"
    analytics = api.get(f"/api/boards/{board_id}/analytics").json()
    assert analytics["acceptance_rate"] == 1.0
    assert analytics["draft_outcome_percentages"] == {"accepted": 100.0, "edited": 0.0, "rejected": 0.0}
    assert analytics["average_invest_score"] == 5.0
    assert sum(event["count"] for event in analytics["daily_events"] if event["event_type"] == "story_accepted") == 1

    edited_draft = {**story["draft"], "title": "Human edited title"}
    response = api.patch(story_url, json={"draft": edited_draft})
    assert response.status_code == 200, response.text
    edited_story = response.json()
    assert edited_story["status"] == "draft"
    assert edited_story["confidence"] is None
    assert edited_story["invest_score"] is None
    assert edited_story["critique"] is None
    analytics = api.get(f"/api/boards/{board_id}/analytics").json()
    assert analytics["draft_outcome_percentages"] == {"accepted": 0.0, "edited": 100.0, "rejected": 0.0}
    revisions = api.get(f"{story_url}/revisions").json()
    assert [revision["revision"] for revision in revisions] == [1, 2]
    assert [revision["editor"] for revision in revisions] == ["ai", "human"]
    assert revisions[0]["draft"] == story["draft"]
    assert revisions[1]["draft"] == edited_draft

    assert api.post(f"{story_url}/decision", json={"decision": "reject"}).json()["status"] == "rejected"
    analytics = api.get(f"/api/boards/{board_id}/analytics").json()
    assert analytics["acceptance_rate"] == 0.0
    assert analytics["story_counts"]["rejected"] == 1
    assert analytics["draft_outcome_percentages"] == {"accepted": 0.0, "edited": 0.0, "rejected": 100.0}
    assert api.get(f"/api/boards/{board_id}/stories", params={"status": "accepted"}).json() == []


def test_draft_outcome_percentages_count_stories_once_and_exclude_other_boards(
    api, database_connection, pipeline_results,
):
    board = create_board(api)
    meeting = create_meeting(api, board["id"])
    other_board = create_board(api)
    other_meeting = create_meeting(api, other_board["id"])
    draft = pipeline_results[1].stories[0].model_dump(mode="json")

    with Session(bind=database_connection, join_transaction_mode="create_savepoint") as session:
        # Four categorized stories: two accepted as-is, one edited, one rejected.
        # Untouched draft/needs_changes stories must not change the denominator.
        for status, human_edits in [
            ("accepted", 0), ("imported", 0), ("accepted", 2),
            ("rejected", 1), ("draft", 0), ("needs_changes", 0),
        ]:
            story = Story(meeting_id=meeting["id"], draft=draft, status=status)
            session.add(story)
            session.flush()
            session.add(StoryRevision(story_id=story.id, revision=1, draft=draft, editor="ai"))
            for revision in range(2, human_edits + 2):
                session.add(StoryRevision(
                    story_id=story.id, revision=revision, draft=draft, editor="human",
                ))
        session.add(Story(meeting_id=other_meeting["id"], draft=draft, status="rejected"))
        session.commit()

    response = api.get(f"/api/boards/{board['id']}/analytics")
    assert response.status_code == 200, response.text
    analytics = response.json()
    assert analytics["draft_outcome_counts"] == {"accepted": 2, "edited": 1, "rejected": 1}
    assert analytics["draft_outcome_percentages"] == {"accepted": 50.0, "edited": 25.0, "rejected": 25.0}
    # The existing acceptance rate still counts accepted stories that were edited.
    assert analytics["acceptance_rate"] == 0.75


def test_duplicate_board_registration_returns_conflict(api):
    board = create_board(api)
    response = api.post("/api/boards", json={
        "provider": board["provider"], "external_id": board["external_id"], "name": "Duplicate",
    })
    assert response.status_code == 409
    assert "SQL" not in response.text


@pytest.mark.parametrize("path", [
    "/api/boards/{id}", "/api/boards/{id}/stories", "/api/boards/{id}/analytics",
    "/api/meetings/{id}", "/api/meetings/{id}/transcript",
    "/api/stories/{id}", "/api/stories/{id}/revisions",
])
def test_missing_resources_return_404(api, path):
    assert api.get(path.format(id=uuid4())).status_code == 404


def test_meeting_cannot_be_created_for_unknown_board(api):
    response = api.post("/api/meetings", json={"board_id": str(uuid4()), "title": "Test"})
    assert response.status_code == 404


def test_processing_requires_uploaded_audio_and_api_key(api, monkeypatch):
    board = create_board(api)
    meeting = create_meeting(api, board["id"])
    url = f"/api/meetings/{meeting['id']}"
    assert api.post(f"{url}/process").status_code == 409
    api.post(f"{url}/media", files={"file": ("test.wav", b"synthetic", "audio/wav")})
    monkeypatch.delenv("GEMINI_API_KEY")
    assert api.post(f"{url}/process").status_code == 503
    assert api.get(url).json()["status"] == "uploaded"


def test_processing_claim_prevents_duplicate_job_and_upload(api, monkeypatch):
    board = create_board(api)
    meeting = create_meeting(api, board["id"])
    # Leave the job pending to simulate an active worker.
    monkeypatch.setattr(meetings, "process_meeting", lambda *args: None)
    upload_and_process(api, meeting["id"])
    url = f"/api/meetings/{meeting['id']}"
    assert api.post(f"{url}/process").status_code == 409
    assert api.post(f"{url}/media", files={"file": ("x.wav", b"x", "audio/wav")}).status_code == 409


def test_model_failure_records_failed_status_and_keeps_transcript(api, monkeypatch, pipeline_results):
    def fail_after_transcribe(audio_path, model, save_stage):
        save_stage("transcribe", pipeline_results[0])
        raise RuntimeError("Synthetic model failure")

    monkeypatch.setattr(pipeline, "_run_pipeline", fail_after_transcribe)
    board = create_board(api)
    meeting = create_meeting(api, board["id"])
    upload_and_process(api, meeting["id"])
    assert api.get(f"/api/meetings/{meeting['id']}").json()["status"] == "failed"
    assert api.get(f"/api/meetings/{meeting['id']}/transcript").status_code == 200
    assert api.get(f"/api/boards/{board['id']}/stories").json() == []


def test_invalid_critique_does_not_create_partial_stories(api, monkeypatch, pipeline_results):
    def invalid_results(*args, **kwargs):
        transcript, extraction, critique = pipeline_results
        return transcript, extraction, critique.model_copy(update={"reviews": []})

    monkeypatch.setattr(jobs, "run_pipeline", invalid_results)
    board = create_board(api)
    meeting = create_meeting(api, board["id"])
    upload_and_process(api, meeting["id"])
    assert api.get(f"/api/meetings/{meeting['id']}").json()["status"] == "failed"
    assert api.get(f"/api/boards/{board['id']}/stories").json() == []


def test_imported_story_cannot_be_edited_or_reviewed(api, database_connection):
    board = create_board(api)
    meeting = create_meeting(api, board["id"])
    upload_and_process(api, meeting["id"])
    story = api.get(f"/api/boards/{board['id']}/stories").json()[0]
    with Session(bind=database_connection, join_transaction_mode="create_savepoint") as session:
        record = session.get(Story, story["id"])
        record.status = "imported"
        session.commit()
    url = f"/api/stories/{story['id']}"
    assert api.patch(url, json={"draft": story["draft"]}).status_code == 409
    assert api.post(f"{url}/decision", json={"decision": "reject"}).status_code == 409


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/boards", {"external_id": " ", "name": "Test"}),
    ("post", "/api/boards", {"provider": "unknown", "external_id": "x", "name": "Test"}),
    ("post", "/api/meetings", {"board_id": "bad-id", "title": "Test"}),
    ("post", "/api/meetings", {"board_id": str(uuid4()), "title": " "}),
    ("post", f"/api/stories/{uuid4()}/decision", {"decision": "import"}),
    ("patch", f"/api/stories/{uuid4()}", {"draft": {"title": "Incomplete"}}),
    ("get", "/api/meetings?limit=101", None),
    ("get", "/api/meetings?status=unknown", None),
])
def test_invalid_requests_return_422_without_database(method, path, body):
    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = lambda: object()
    try:
        with TestClient(app) as client:
            response = client.request(method, path, json=body)
        assert response.status_code == 422
    finally:
        app.dependency_overrides = previous_overrides
