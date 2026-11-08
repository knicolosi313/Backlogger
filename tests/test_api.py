"""API workflow tests using PostgreSQL transactions and fake Gemini output."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from services.api import jobs, meetings
from services.api import stories as story_routes
from services.api.server import app
from services.db import get_session
from services.models import Meeting, Story, StoryRevision
from services.worker import pipeline
from services.worker.objects import RevisionResult


@pytest.fixture
def api(database_connection, monkeypatch, tmp_path, pipeline_results):
    def new_session(*args):
        return Session(bind=database_connection, join_transaction_mode="create_savepoint")

    def session_dependency():
        with new_session() as session:
            yield session

    def fake_gemini(audio_path, model, save_stage, *, creativity="balanced", board_context=None, review_context=None):
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
    by_meeting = api.get(f"/api/boards/{board_id}/stories", params={"meeting_id": meeting_id}).json()
    assert by_meeting == stories
    other_meeting = create_meeting(api, board_id)["id"]
    assert api.get(f"/api/boards/{board_id}/stories", params={"meeting_id": other_meeting}).json() == []
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
    # The critique flags "Testable" once; a good, accepted story is not pending, so nothing needs attention.
    assert analytics["invest_issues"]["testable"] == 1
    assert sum(analytics["invest_issues"].values()) == 1
    assert analytics["needs_attention"] == []

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
    # An edit clears the scores and critique, so the old flag no longer counts.
    assert analytics["invest_issues"]["testable"] == 0
    assert analytics["needs_attention"] == []
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


def test_meeting_audio_is_available_inside_the_extension(api):
    board = create_board(api)
    meeting = create_meeting(api, board["id"])
    url = f"/api/meetings/{meeting['id']}"
    assert api.get(f"{url}/audio").status_code == 404
    assert api.get(f"/api/meetings/{uuid4()}/audio").status_code == 404
    data = b"synthetic audio for evidence playback"
    assert api.post(f"{url}/media", files={"file": ("audio.webm", data, "audio/webm")}).status_code == 200
    response = api.get(f"{url}/audio")
    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == "audio/webm"
    assert response.headers["cache-control"] == "private, no-store"


def test_supabase_upload_replacement_and_processing(api, monkeypatch, pipeline_results, tmp_path):
    from unittest.mock import Mock
    import requests
    from services.api import media

    objects = {}
    storage = Mock(bucket="meeting-audio")
    storage.upload.side_effect = lambda key, file, content_type: objects.update({key: file.read()})
    storage.exists.side_effect = lambda key: key in objects
    storage.delete.side_effect = lambda key: objects.pop(key, None)

    def download(key):
        response = requests.Response()
        response.status_code = 200
        response._content = objects[key]
        response._content_consumed = True
        return response

    storage.download.side_effect = download
    monkeypatch.setattr(media, "SupabaseStorage", lambda *args: storage)
    monkeypatch.setenv("MEDIA_STORAGE", "supabase")
    observed_paths = []
    original_pipeline = jobs.run_pipeline

    def check_download(audio_path, **kwargs):
        from pathlib import Path
        path = Path(audio_path)
        assert path.suffix == ".webm"
        assert path.read_bytes() == b"replacement audio"
        observed_paths.append(path)
        return original_pipeline(audio_path, **kwargs)

    monkeypatch.setattr(jobs, "run_pipeline", check_download)
    board = create_board(api)
    meeting = create_meeting(api, board["id"])
    url = f"/api/meetings/{meeting['id']}"
    for data in (b"original audio", b"replacement audio"):
        response = api.post(f"{url}/media", files={"file": ("audio.webm", data, "audio/webm")})
        assert response.status_code == 200, response.text
        assert response.json()["media_key"].startswith("supabase://meeting-audio/")
    assert len(objects) == 1
    assert storage.delete.call_count == 1
    playback = api.get(f"{url}/audio")
    assert playback.status_code == 200
    assert playback.content == b"replacement audio"
    assert playback.headers["content-type"] == "audio/webm"
    assert "supabase" not in playback.headers.get("location", "")
    assert api.post(f"{url}/process").status_code == 202
    assert api.get(url).json()["status"] == "completed"
    assert observed_paths and not observed_paths[0].exists()
    assert len(objects) == 1  # Keep the original recording for retries/history.
    assert len(api.get(f"/api/boards/{board['id']}/stories").json()) == len(pipeline_results[1].stories)


def test_restore_rejected_story_preserves_content_and_records_one_event(api, database_connection):
    from services.models import AnalyticsEvent

    board = create_board(api)
    meeting = create_meeting(api, board["id"])
    upload_and_process(api, meeting["id"])
    original = api.get(f"/api/boards/{board['id']}/stories").json()[0]
    url = f"/api/stories/{original['id']}/decision"
    assert api.post(url, json={"decision": "reject"}).status_code == 200
    for _ in range(2):
        response = api.post(url, json={"decision": "restore"})
        assert response.status_code == 200, response.text
        restored = response.json()
        assert restored["status"] == "draft"
        for field in ("draft", "confidence", "invest_score", "critique"):
            assert restored[field] == original[field]
    pending = api.get(f"/api/boards/{board['id']}/stories", params={"status": "draft"}).json()
    assert [story["id"] for story in pending] == [original["id"]]
    analytics = api.get(f"/api/boards/{board['id']}/analytics").json()
    assert analytics["story_counts"]["rejected"] == 0
    assert analytics["draft_outcome_counts"]["rejected"] == 0
    with Session(bind=database_connection, join_transaction_mode="create_savepoint") as session:
        events = session.exec(select(AnalyticsEvent).where(
            AnalyticsEvent.story_id == original["id"], AnalyticsEvent.event_type == "story_restored",
        )).all()
        assert len(events) == 1
    assert api.post(url, json={"decision": "accept"}).json()["status"] == "accepted"


@pytest.mark.parametrize("status", ["accepted", "needs_changes", "imported", "pending_export"])
def test_restore_does_not_override_other_states_or_pending_exports(api, database_connection, status):
    from services.models import Import

    board = create_board(api)
    meeting = create_meeting(api, board["id"])
    upload_and_process(api, meeting["id"])
    story = api.get(f"/api/boards/{board['id']}/stories").json()[0]
    with Session(bind=database_connection, join_transaction_mode="create_savepoint") as session:
        record = session.get(Story, story["id"])
        record.status = "rejected" if status == "pending_export" else status
        if status == "pending_export":
            session.add(Import(story_id=record.id, idempotency_key=f"restore-test-{uuid4()}"))
        session.commit()
    response = api.post(f"/api/stories/{story['id']}/decision", json={"decision": "restore"})
    assert response.status_code == 409
    assert api.get(f"/api/stories/{story['id']}").json()["status"] == (
        "rejected" if status == "pending_export" else status
    )


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


def test_analytics_flags_issues_and_pending_stories_needing_attention(api, database_connection, pipeline_results):
    board = create_board(api)
    meeting = create_meeting(api, board["id"])
    other_meeting = create_meeting(api, create_board(api)["id"])
    draft = pipeline_results[1].stories[0].model_dump(mode="json")
    review = pipeline_results[2].reviews[0].model_dump(mode="json")

    def flagged(*criteria):
        return {**review, "flags": [{"criterion": c, "issue": "x"} for c in criteria]}

    with Session(bind=database_connection, join_transaction_mode="create_savepoint") as session:
        rows = [
            # (meeting, status, invest_score, confidence, critique, title)
            (meeting, "draft", 2, 0.9, flagged("Small", "Small", "Grounded"), "Low quality"),
            (meeting, "needs_changes", 6, 0.3, flagged(), "Low confidence"),
            (meeting, "draft", 6, 0.9, flagged("Small"), "Fine"),
            (meeting, "draft", None, None, None, "Edited, unscored"),
            (meeting, "draft", 6, 0.9, flagged(), "No quote"),
            (meeting, "accepted", 1, 0.1, flagged("Testable"), "Accepted but weak"),
            (meeting, "rejected", 0, 0.0, flagged("Small", "Testable"), "Rejected"),
            (other_meeting, "draft", 0, 0.0, flagged("Small"), "Other board"),
        ]
        for target, status, score, confidence, critique, title in rows:
            story_draft = {**draft, "title": title}
            if title == "No quote":
                story_draft["source"] = {**draft["source"], "quote": "  "}
            session.add(Story(
                meeting_id=target["id"], draft=story_draft, status=status,
                invest_score=score, confidence=confidence, critique=critique,
            ))
        session.commit()

    analytics = api.get(f"/api/boards/{board['id']}/analytics").json()
    # A story counts once per criterion; rejected stories and other boards are left out.
    assert analytics["invest_issues"] == {
        "independent": 0, "negotiable": 0, "valuable": 0, "estimable": 0,
        "small": 2, "testable": 1, "grounded": 1,
    }
    # Only pending stories with a low score or confidence, and unscored edits are not "low".
    assert sorted((item["title"], item["reason"]) for item in analytics["needs_attention"]) == [
        ("Low confidence", "Low confidence"), ("Low quality", "Low INVEST quality"),
        ("No quote", "No evidence from the meeting"),
    ]


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
    def fail_after_transcribe(audio_path, model, save_stage, *, creativity="balanced", board_context=None, review_context=None):
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


@pytest.mark.parametrize("selected,configured,expected", [
    ("conservative", "balanced", "conservative"),
    ("balanced", "creative", "balanced"),
    ("creative", "balanced", "creative"),
    ("exploratory", "balanced", "exploratory"),
    (None, "creative", "creative"),
])
def test_processing_passes_creativity_selection_to_job(api, monkeypatch, selected, configured, expected):
    board = create_board(api)
    meeting = create_meeting(api, board["id"])
    url = f"/api/meetings/{meeting['id']}"
    assert api.post(f"{url}/media", files={"file": ("test.wav", b"synthetic", "audio/wav")}).status_code == 200
    received_levels = []
    monkeypatch.setenv("DEFAULT_CREATIVITY", configured)
    monkeypatch.setattr(meetings, "process_meeting", lambda meeting_id, key, model, creativity: received_levels.append(creativity))
    if selected is None:
        response = api.post(f"{url}/process")
    else:
        response = api.post(f"{url}/process", json={"creativity": selected})
    assert response.status_code == 202, response.text
    assert received_levels == [expected]


def test_invalid_creativity_does_not_start_processing(api, monkeypatch):
    board = create_board(api)
    meeting = create_meeting(api, board["id"])
    url = f"/api/meetings/{meeting['id']}"
    api.post(f"{url}/media", files={"file": ("test.wav", b"synthetic", "audio/wav")})
    assert api.post(f"{url}/process", json={"creativity": "unknown"}).status_code == 422
    monkeypatch.setenv("DEFAULT_CREATIVITY", "unknown")
    assert api.post(f"{url}/process").status_code == 503
    assert api.get(url).json()["status"] == "uploaded"


def test_only_the_two_supported_models_are_offered(api, monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert api.get("/api/models").json() == {
        "default": "gemini-3.1-flash-lite",
        "models": ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"],
    }

    # The configured default comes first, and stays usable even if it isn't one of the two.
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    assert api.get("/api/models").json()["models"] == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    monkeypatch.setenv("GEMINI_MODEL", "custom-model")
    body = api.get("/api/models").json()
    assert body["default"] == "custom-model"
    assert body["models"] == ["custom-model", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]


@pytest.mark.parametrize("selected,expected", [
    (None, "gemini-3.1-flash-lite"),
    ("gemini-3.1-flash-lite", "gemini-3.1-flash-lite"),
    ("gemini-3.5-flash-lite", "gemini-3.5-flash-lite"),
])
def test_processing_passes_model_selection_to_job(api, monkeypatch, selected, expected):
    board = create_board(api)
    meeting = create_meeting(api, board["id"])
    url = f"/api/meetings/{meeting['id']}"
    assert api.post(f"{url}/media", files={"file": ("test.wav", b"synthetic", "audio/wav")}).status_code == 200
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    received_models = []
    monkeypatch.setattr(meetings, "process_meeting", lambda meeting_id, key, model, creativity: received_models.append(model))
    response = api.post(f"{url}/process", json={} if selected is None else {"model": selected})
    assert response.status_code == 202, response.text
    assert received_models == [expected]


def test_unsupported_model_does_not_start_processing(api, monkeypatch):
    board = create_board(api)
    meeting = create_meeting(api, board["id"])
    url = f"/api/meetings/{meeting['id']}"
    api.post(f"{url}/media", files={"file": ("test.wav", b"synthetic", "audio/wav")})
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    started = []
    monkeypatch.setattr(meetings, "process_meeting", lambda *args: started.append(args))
    response = api.post(f"{url}/process", json={"model": "gemini-2.5-pro"})
    assert response.status_code == 422
    assert "gemini-2.5-pro" in response.json()["detail"]
    assert api.post(f"{url}/process", json={"model": ""}).status_code == 422
    assert started == []
    assert api.get(url).json()["status"] == "uploaded"


# ---- requesting AI edits on a draft ----


@pytest.fixture
def editable(api, database_connection, pipeline_results, monkeypatch):
    """A processed story, plus a fake AI that records what it was asked and answers as told."""
    board = create_board(api)
    meeting = create_meeting(api, board["id"])
    upload_and_process(api, meeting["id"])
    story = api.get(f"/api/boards/{board['id']}/stories").json()[0]
    asked = []
    answer = {}

    def fake_revise(draft, instructions, transcript, context, model):
        asked.append(SimpleNamespace(
            draft=draft, instructions=instructions, transcript=transcript, context=context, model=model,
        ))
        if "during" in answer:
            answer["during"]()
        if "error" in answer:
            raise answer["error"]
        return answer["result"], answer["reviews"]

    def respond(count=1, **changes):
        base = pipeline_results[1].stories[0]
        stories = [
            base.model_copy(update={"title": f"Revised {n + 1}", **changes}, deep=True) for n in range(count)
        ]
        answer["result"] = RevisionResult(stories=stories, summary="Reworded the story.")
        answer["reviews"] = [
            pipeline_results[2].reviews[0].model_copy(update={"index": n, "confidence": 0.4, "invest_score": 3})
            for n in range(count)
        ]

    respond()
    monkeypatch.setattr(story_routes, "revise_story", fake_revise)
    return SimpleNamespace(
        api=api, board=board, story=story, asked=asked, answer=answer, respond=respond,
        url=f"/api/stories/{story['id']}/request-edits", db=database_connection,
    )


def set_story(case, **changes):
    with Session(bind=case.db, join_transaction_mode="create_savepoint") as session:
        record = session.get(Story, case.story["id"])
        for key, value in changes.items():
            setattr(record, key, value)
        session.commit()


def test_requesting_edits_replaces_the_draft_and_reviews_it_again(editable):
    case = editable
    set_story(case, status="accepted")
    response = case.api.post(case.url, json={"instructions": "Make the title shorter"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["summary"] == "Reworded the story."
    assert result["new_stories"] == []
    story = result["story"]
    assert story["id"] == case.story["id"]
    assert story["draft"]["title"] == "Revised 1"
    # an accepted story that was changed needs a fresh look from a human
    assert story["status"] == "draft"
    # the scores are for the revised draft, from the new review, not left blank like after a manual edit
    assert (story["confidence"], story["invest_score"]) == (0.4, 3)
    assert story["critique"]["invest_score"] == 3
    assert case.api.get(f"/api/stories/{story['id']}").json() == story

    # the AI saw the story as it was, the instructions, the transcript, and which model to use
    (call,) = case.asked
    assert call.draft == case.story["draft"] and call.instructions == "Make the title shorter"
    assert call.transcript.segments and call.model == "gemini-3.1-flash-lite"
    assert call.context is not None  # related board work was looked up

    revisions = case.api.get(f"/api/stories/{story['id']}/revisions").json()
    assert [(item["revision"], item["editor"]) for item in revisions] == [(1, "ai"), (2, "ai")]
    assert revisions[0]["draft"] == case.story["draft"] and revisions[1]["draft"]["title"] == "Revised 1"


def test_requesting_edits_records_one_event(editable):
    from services.models import AnalyticsEvent

    case = editable
    assert case.api.post(case.url, json={"instructions": "Tighten it"}).status_code == 200
    with Session(bind=case.db, join_transaction_mode="create_savepoint") as session:
        events = session.exec(select(AnalyticsEvent).where(
            AnalyticsEvent.story_id == case.story["id"], AnalyticsEvent.event_type == "story_ai_edited",
        )).all()
    assert len(events) == 1


def test_a_split_keeps_the_first_story_and_adds_the_rest_as_new_drafts(editable):
    from services.models import AnalyticsEvent

    case = editable
    case.respond(count=3)
    response = case.api.post(case.url, json={"instructions": "Split into three stories"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["story"]["id"] == case.story["id"]
    assert [item["draft"]["title"] for item in [result["story"], *result["new_stories"]]] == [
        "Revised 1", "Revised 2", "Revised 3",
    ]
    assert all(item["status"] == "draft" and item["meeting_id"] == case.story["meeting_id"]
               for item in result["new_stories"])
    assert all(item["confidence"] == 0.4 for item in result["new_stories"])

    listed = case.api.get(f"/api/boards/{case.board['id']}/stories").json()
    assert {item["id"] for item in listed} == {result["story"]["id"], *(s["id"] for s in result["new_stories"])}
    for item in result["new_stories"]:
        revisions = case.api.get(f"/api/stories/{item['id']}/revisions").json()
        assert [(r["revision"], r["editor"]) for r in revisions] == [(1, "ai")]
    with Session(bind=case.db, join_transaction_mode="create_savepoint") as session:
        created = session.exec(select(AnalyticsEvent).where(
            AnalyticsEvent.board_id == case.board["id"], AnalyticsEvent.event_type == "story_created",
            AnalyticsEvent.story_id.in_([s["id"] for s in result["new_stories"]]),
        )).all()
    assert len(created) == 2


def test_the_selected_model_is_used_and_unknown_models_are_refused_before_the_ai_runs(editable):
    case = editable
    ok = case.api.post(case.url, json={"instructions": "Tighten it", "model": "gemini-3.5-flash-lite"})
    assert ok.status_code == 200, ok.text
    assert case.asked[-1].model == "gemini-3.5-flash-lite"

    before = len(case.asked)
    refused = case.api.post(case.url, json={"instructions": "Tighten it", "model": "gemini-9-ultra"})
    assert refused.status_code == 422 and "gemini-9-ultra" in refused.json()["detail"]
    assert len(case.asked) == before


def test_a_story_changed_while_the_ai_works_is_not_overwritten(editable):
    case = editable
    human = {**case.story["draft"], "title": "A person edited this meanwhile"}
    case.answer["during"] = lambda: case.api.patch(f"/api/stories/{case.story['id']}", json={"draft": human})
    response = case.api.post(case.url, json={"instructions": "Tighten it"})
    assert response.status_code == 409
    assert "changed while the AI was working" in response.json()["detail"]
    current = case.api.get(f"/api/stories/{case.story['id']}").json()
    assert current["draft"]["title"] == "A person edited this meanwhile"
    revisions = case.api.get(f"/api/stories/{case.story['id']}/revisions").json()
    assert [item["editor"] for item in revisions] == ["ai", "human"]


def test_an_ai_failure_changes_nothing_and_never_leaks_details(editable):
    from services.worker.revision import RevisionError

    case = editable
    before = case.api.get(f"/api/stories/{case.story['id']}").json()
    case.answer["error"] = RevisionError("The AI returned an unusable number of stories.")
    refused = case.api.post(case.url, json={"instructions": "Tighten it"})
    assert refused.status_code == 502 and "unusable number" in refused.json()["detail"]

    case.answer["error"] = RuntimeError("quota exceeded for key=SECRET-KEY")
    failed = case.api.post(case.url, json={"instructions": "Tighten it"})
    assert failed.status_code == 502
    assert "SECRET" not in failed.text and "Try again" in failed.json()["detail"]

    assert case.api.get(f"/api/stories/{case.story['id']}").json() == before
    assert len(case.api.get(f"/api/stories/{case.story['id']}/revisions").json()) <= 1


@pytest.mark.parametrize("blocker", ["imported", "pending_export"])
def test_imported_stories_and_pending_exports_cannot_be_revised(editable, blocker):
    from services.models import Import

    case = editable
    if blocker == "imported":
        set_story(case, status="imported")
    else:
        with Session(bind=case.db, join_transaction_mode="create_savepoint") as session:
            session.add(Import(story_id=case.story["id"], idempotency_key=f"revise-test-{uuid4()}"))
            session.commit()
    assert case.api.post(case.url, json={"instructions": "Tighten it"}).status_code == 409
    assert case.asked == []


def test_revising_needs_the_api_key_valid_instructions_and_a_transcript(editable, monkeypatch):
    case = editable
    assert case.api.post(case.url, json={"instructions": "   "}).status_code == 422
    assert case.api.post(case.url, json={"instructions": "x" * 1001}).status_code == 422
    assert case.api.post(case.url, json={}).status_code == 422
    assert case.api.post(case.url, json={"instructions": "Tighten it", "extra": 1}).status_code == 422

    monkeypatch.delenv("GEMINI_API_KEY")
    assert case.api.post(case.url, json={"instructions": "Tighten it"}).status_code == 503
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-not-a-real-key")

    # a story whose meeting never produced a transcript has nothing to revise it from
    with Session(bind=case.db, join_transaction_mode="create_savepoint") as session:
        bare_meeting = Meeting(board_id=case.board["id"], title="No transcript")
        session.add(bare_meeting)
        session.flush()
        bare = Story(meeting_id=bare_meeting.id, draft=case.story["draft"])
        session.add(bare)
        session.commit()
        bare_id = bare.id
    response = case.api.post(f"/api/stories/{bare_id}/request-edits", json={"instructions": "Tighten it"})
    assert response.status_code == 409 and "no transcript" in response.json()["detail"]
    assert case.asked == []
    assert case.api.post(f"/api/stories/{uuid4()}/request-edits", json={"instructions": "x"}).status_code == 404


# ---- deleting rejected drafts, and story titles in export history ----


@pytest.fixture
def rejected_story(api, database_connection):
    board = create_board(api)
    meeting = create_meeting(api, board["id"])
    upload_and_process(api, meeting["id"])
    story = api.get(f"/api/boards/{board['id']}/stories").json()[0]
    assert api.post(f"/api/stories/{story['id']}/decision", json={"decision": "reject"}).status_code == 200
    return SimpleNamespace(api=api, board=board, story=story, db=database_connection)


def stored_story(case):
    with Session(bind=case.db, join_transaction_mode="create_savepoint") as session:
        return session.get(Story, case.story["id"])


def test_deleting_a_rejected_story_hides_it_but_keeps_everything(rejected_story):
    from services.models import AnalyticsEvent

    case = rejected_story
    assert case.api.delete(f"/api/stories/{case.story['id']}").status_code == 204

    # gone from the lists the app shows...
    assert case.api.get(f"/api/boards/{case.board['id']}/stories").json() == []
    assert case.api.get(f"/api/boards/{case.board['id']}/stories", params={"status": "rejected"}).json() == []
    # ...but still stored, readable, and countable
    assert stored_story(case).deleted_at is not None
    assert case.api.get(f"/api/stories/{case.story['id']}").status_code == 200
    kept = case.api.get(f"/api/boards/{case.board['id']}/stories", params={"include_deleted": True}).json()
    assert [item["id"] for item in kept] == [case.story["id"]]
    assert len(case.api.get(f"/api/stories/{case.story['id']}/revisions").json()) >= 1
    # analytics still count it, so deleting can't be used to hide rejections
    assert case.api.get(f"/api/boards/{case.board['id']}/analytics").json()["story_counts"]["rejected"] == 1
    with Session(bind=case.db, join_transaction_mode="create_savepoint") as session:
        events = session.exec(select(AnalyticsEvent).where(
            AnalyticsEvent.story_id == case.story["id"], AnalyticsEvent.event_type == "story_deleted",
        )).all()
    assert len(events) == 1


def test_deleting_twice_is_safe_and_records_one_event(rejected_story):
    from services.models import AnalyticsEvent

    case = rejected_story
    first_time = None
    for _ in range(2):
        assert case.api.delete(f"/api/stories/{case.story['id']}").status_code == 204
        first_time = first_time or stored_story(case).deleted_at
        assert stored_story(case).deleted_at == first_time
    with Session(bind=case.db, join_transaction_mode="create_savepoint") as session:
        events = session.exec(select(AnalyticsEvent).where(
            AnalyticsEvent.story_id == case.story["id"], AnalyticsEvent.event_type == "story_deleted",
        )).all()
    assert len(events) == 1


@pytest.mark.parametrize("status", ["draft", "needs_changes", "accepted", "imported"])
def test_only_rejected_stories_can_be_deleted(rejected_story, status):
    case = rejected_story
    with Session(bind=case.db, join_transaction_mode="create_savepoint") as session:
        session.get(Story, case.story["id"]).status = status
        session.commit()
    assert case.api.delete(f"/api/stories/{case.story['id']}").status_code == 409
    assert stored_story(case).deleted_at is None
    assert [s["id"] for s in case.api.get(f"/api/boards/{case.board['id']}/stories").json()] == [case.story["id"]]


def test_a_story_with_a_pending_export_cannot_be_deleted(rejected_story):
    from services.models import Import

    case = rejected_story
    with Session(bind=case.db, join_transaction_mode="create_savepoint") as session:
        session.add(Import(story_id=case.story["id"], idempotency_key=f"delete-test-{uuid4()}"))
        session.commit()
    assert case.api.delete(f"/api/stories/{case.story['id']}").status_code == 409
    assert stored_story(case).deleted_at is None


def test_a_deleted_story_cannot_be_changed_or_brought_back(rejected_story, monkeypatch):
    case = rejected_story
    assert case.api.delete(f"/api/stories/{case.story['id']}").status_code == 204
    url = f"/api/stories/{case.story['id']}"
    assert case.api.patch(url, json={"draft": case.story["draft"]}).status_code == 409
    assert case.api.post(f"{url}/decision", json={"decision": "restore"}).status_code == 409
    assert case.api.post(f"{url}/decision", json={"decision": "accept"}).status_code == 409
    assert case.api.post(f"{url}/request-edits", json={"instructions": "Tighten it"}).status_code == 409
    assert stored_story(case).status == "rejected"


def test_deleting_an_unknown_story_is_not_found(api):
    assert api.delete(f"/api/stories/{uuid4()}").status_code == 404


def test_export_history_names_the_story(api, database_connection):
    from services.models import Import

    board = create_board(api)
    meeting = create_meeting(api, board["id"])
    upload_and_process(api, meeting["id"])
    story = api.get(f"/api/boards/{board['id']}/stories").json()[0]
    with Session(bind=database_connection, join_transaction_mode="create_savepoint") as session:
        session.add(Import(
            story_id=story["id"], idempotency_key=f"history-test-{uuid4()}",
            status="completed", external_card_id="card-1",
        ))
        session.commit()
    history = api.get(f"/api/boards/{board['id']}/imports").json()
    assert [(item["story_id"], item["story_title"]) for item in history] == [(story["id"], story["draft"]["title"])]
    single = api.get(f"/api/stories/{story['id']}/import").json()
    assert single["story_title"] == story["draft"]["title"]
