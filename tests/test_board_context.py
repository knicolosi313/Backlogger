"""Board scoping, semantic cache invalidation, and provider snapshots."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from adapters.jira.client import JiraClient
from services.api import board_context as api_context
from services.api.export_clients import get_export_client
from services.api.server import app
from services.db import get_session
from services.models import Board, Meeting, Story, StoryContext
from services.worker import board_context as context


@pytest.fixture
def board_data(database_connection, pipeline_results):
    with Session(bind=database_connection, join_transaction_mode="create_savepoint") as session:
        board = Board(provider="trello", external_id=uuid4().hex, name="Product board")
        other = Board(provider="trello", external_id=uuid4().hex, name="Another board")
        session.add_all([board, other])
        session.flush()
        old = Meeting(board_id=board.id, title="Prior meeting")
        current = Meeting(board_id=board.id, title="Current meeting")
        unrelated = Meeting(board_id=other.id, title="Private meeting")
        session.add_all([old, current, unrelated])
        session.flush()
        draft = pipeline_results[1].stories[0].model_dump(mode="json") | {"epic": "Account access"}
        story = Story(meeting_id=old.id, draft=draft)
        session.add_all([
            story, Story(meeting_id=old.id, draft=draft | {"title": "Rejected"}, status="rejected"),
            Story(meeting_id=current.id, draft=draft | {"title": "Same meeting"}),
            Story(meeting_id=unrelated.id, draft=draft | {"title": "Other board secret"}),
            StoryContext(board_id=other.id, source="trello", external_id="secret", payload={"title": "External secret"}),
        ])
        session.commit()
        yield session, board, current, story


def test_context_excludes_other_boards_rejected_and_current_meeting(board_data, pipeline_results):
    session, board, meeting, story = board_data
    result = context.build_board_context(session, meeting.id, pipeline_results[0])
    assert result["board_name"] == board.name
    assert result["epic_names"] == ["Account access"]
    assert result["retrieval_method"] == "keyword"
    assert [item["id"] for item in result["existing_stories"]] == [str(story.id)]


def test_saved_meeting_loads_board_context_after_transcription(
    board_data, database_connection, pipeline_results, monkeypatch,
):
    from google import genai
    from services.worker import pipeline

    _, _, meeting, story = board_data
    client = Mock()
    client.files.upload.return_value = SimpleNamespace(state=SimpleNamespace(name="ACTIVE"))
    client.models.generate_content.side_effect = [SimpleNamespace(parsed=result) for result in pipeline_results]
    monkeypatch.setattr(genai, "Client", Mock(return_value=client))
    monkeypatch.setattr(pipeline, "Session", lambda *args: Session(
        bind=database_connection, join_transaction_mode="create_savepoint",
    ))
    pipeline.run_pipeline("synthetic.wav", meeting_id=meeting.id)
    calls = client.models.generate_content.call_args_list
    for call in calls[1:]:
        assert str(story.id) in call.kwargs["contents"]
        assert "Account access" in call.kwargs["contents"]


def test_semantic_retrieval_caches_and_refreshes_edited_stories(board_data, pipeline_results, monkeypatch):
    session, board, meeting, story = board_data
    monkeypatch.setenv("STORY_EMBEDDINGS_ENABLED", "true")
    vector = [1.0] + [0.0] * (context.DIMENSIONS - 1)
    embed = Mock(side_effect=lambda texts: [vector[:] for _ in texts])
    monkeypatch.setattr(context, "embed_texts", embed)
    first = context.build_board_context(session, meeting.id, pipeline_results[0])
    assert first["retrieval_method"] == "embedding"
    assert first["existing_stories"][0]["similarity"] == 1.0
    assert len(embed.call_args.args[0]) == 2  # Query plus one missing story.
    context.build_board_context(session, meeting.id, pipeline_results[0])
    assert len(embed.call_args.args[0]) == 1  # Only the query is regenerated.
    story.draft = story.draft | {"title": "Reset with email verification"}
    session.commit()
    updated = context.build_board_context(session, meeting.id, pipeline_results[0])
    assert len(embed.call_args.args[0]) == 2
    assert "email verification" in updated["existing_stories"][0]["story"]
    rows = session.exec(select(StoryContext).where(StoryContext.board_id == board.id)).all()
    assert len(rows) == 1
    assert rows[0].payload["title"] == story.draft["title"]


def test_each_draft_searches_board_candidates_beyond_the_transcript_shortlist(board_data, pipeline_results, monkeypatch):
    session, board, meeting, _ = board_data
    for index in range(25):
        session.add(StoryContext(board_id=board.id, source="trello", external_id=f"other{index:02}", payload={"title": f"Unrelated work {index}"}))
    session.add(StoryContext(board_id=board.id, source="trello", external_id="target", payload={"title": "Reset a password"}))
    session.commit()
    monkeypatch.setenv("STORY_EMBEDDINGS_ENABLED", "true")

    def embed(texts):
        return [([1.0, 0.0] if "Reset a password" in text else [0.0, 1.0]) + [0.0] * 766 for text in texts]

    fake = Mock(side_effect=embed)
    monkeypatch.setattr(context, "embed_texts", fake)
    initial = context.build_board_context(session, meeting.id, pipeline_results[0])
    assert not any(item["id"] == "target" for item in initial["existing_stories"])
    review = context.build_review_context(pipeline_results[1].stories, session=session, meeting_id=meeting.id)
    assert any(item["reference"] == "trello:target" for item in review["board_matches"])
    assert len(fake.call_args.args[0]) == 1  # Existing board vectors were reused.
    assert not any("secret" in item["reference"] for item in review["board_matches"])


def test_embedding_failure_keeps_context_available(board_data, pipeline_results, monkeypatch):
    session, _, meeting, story = board_data
    monkeypatch.setenv("STORY_EMBEDDINGS_ENABLED", "true")
    monkeypatch.setattr(context, "embed_texts", Mock(side_effect=RuntimeError("Provider unavailable")))
    result = context.build_board_context(session, meeting.id, pipeline_results[0])
    assert result["retrieval_method"] == "keyword"
    assert result["existing_stories"][0]["id"] == str(story.id)


def test_new_story_index_is_saved_without_generating_a_meeting(board_data, monkeypatch):
    session, board, _, story = board_data
    monkeypatch.setenv("STORY_EMBEDDINGS_ENABLED", "true")
    vector = [1.0] + [0.0] * (context.DIMENSIONS - 1)
    monkeypatch.setattr(context, "embed_texts", Mock(return_value=[vector]))
    context.index_stories(session, board.id, [story])
    row = session.exec(select(StoryContext).where(StoryContext.board_id == board.id)).one()
    assert row.external_id == str(story.id)
    assert row.embedding == vector


def test_sync_endpoint_refreshes_external_work_and_prunes_removed_cards(board_data, pipeline_results):
    session, board, meeting, _ = board_data
    client = Mock()
    client.list_context_checklists.return_value = [{
        "idCard": "card1", "name": "Acceptance Criteria", "checkItems": [{"name": "Receive an email"}],
    }]
    client.list_context_cards.return_value = [{
        "id": "card1", "name": "Password recovery", "desc": "Email reset flow", "closed": False,
        "labels": [{"name": "Epic: Account access"}],
    }]
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_export_client] = lambda: client
    try:
        with TestClient(app) as api:
            response = api.post(f"/api/boards/{board.id}/context/sync")
            assert response.status_code == 200, response.text
            assert response.json()["synced_items"] == 1
            result = context.build_board_context(session, meeting.id, pipeline_results[0])
            assert result["external_context_synced"] is True
            assert any(item["id"] == "card1" for item in result["existing_stories"])
            row = session.exec(select(StoryContext).where(StoryContext.board_id == board.id)).one()
            assert row.payload["acceptance_criteria"] == ["Receive an email"]
            row.embedding = [1.0] + [0.0] * 767
            row.embedding_model = context.EMBEDDING_MODEL
            row.content_hash = "old-hash"
            session.commit()
            assert api.post(f"/api/boards/{board.id}/context/sync").status_code == 200
            session.refresh(row)
            assert row.embedding is not None  # Unchanged sync preserves the cache.
            client.list_context_cards.return_value[0]["name"] = "Recover an account"
            assert api.post(f"/api/boards/{board.id}/context/sync").status_code == 200
            session.refresh(row)
            assert row.embedding is None
            # On failure the previous snapshot remains intact.
            client.list_context_cards.side_effect = requests.Timeout("secret token")
            response = api.post(f"/api/boards/{board.id}/context/sync")
            assert response.status_code == 502
            assert "secret" not in response.text
            assert session.exec(select(StoryContext).where(StoryContext.board_id == board.id)).one().external_id == "card1"
            client.list_context_cards.side_effect = None
            client.list_context_cards.return_value = []
            assert api.post(f"/api/boards/{board.id}/context/sync").json()["synced_items"] == 0
            assert not session.exec(select(StoryContext).where(StoryContext.board_id == board.id)).all()
    finally:
        app.dependency_overrides = previous


def test_jira_snapshot_follows_pages_and_preserves_epic_names():
    board = SimpleNamespace(provider="jira", external_id=f"{uuid4()}:10001")
    client = Mock()
    client.list_context_issues.side_effect = [
        {"issues": [{"key": "APP-1", "fields": {"summary": "Account access", "issuetype": {"hierarchyLevel": 1}}}], "nextPageToken": "page2"},
        {"issues": [{"key": "APP-2", "fields": {
            "summary": "Reset password", "parent": {"key": "APP-1"},
            "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Email flow"}]}]},
        }}]},
    ]
    result = api_context.fetch_context(board, client)
    assert result["APP-2"]["epic"] == "Account access"
    assert result["APP-2"]["description"] == "Email flow"
    assert client.list_context_issues.call_args.args[-1] == "page2"


@pytest.mark.parametrize("page", [
    {"issues": [], "nextPageToken": "repeated"},
    {"issues": [], "isLast": False},
])
def test_incomplete_snapshot_is_rejected_instead_of_saved(page):
    board = SimpleNamespace(provider="jira", external_id=f"{uuid4()}:10001")
    client = Mock()
    client.list_context_issues.return_value = page
    with pytest.raises(HTTPException, match="incomplete"):
        api_context.fetch_context(board, client)


def test_jira_context_request_uses_numeric_project_and_enhanced_search():
    client = JiraClient("synthetic-token")
    client._request = Mock(return_value={"issues": []})
    client.list_context_issues(str(uuid4()), "10001", "page2")
    call = client._request.call_args
    assert call.args[1].endswith("/search/jql")
    assert call.kwargs["body"]["nextPageToken"] == "page2"
    with pytest.raises(ValueError):
        client.list_context_issues(str(uuid4()), "10001 OR project = SECRET")


@pytest.mark.parametrize("values", [[0.0] * 768, [float("nan")] * 768, [1.0]])
def test_embedding_provider_output_is_validated(monkeypatch, values):
    from google import genai
    client = Mock()
    client.models.embed_content.return_value = SimpleNamespace(embeddings=[SimpleNamespace(values=values)])
    monkeypatch.setattr(genai, "Client", Mock(return_value=client))
    with pytest.raises(ValueError):
        context.embed_texts(["Password recovery"])


def test_embeddings_use_normalized_semantic_similarity_vectors(monkeypatch):
    from google import genai
    client = Mock()
    vector = [2.0] + [0.0] * 767
    client.models.embed_content.return_value = SimpleNamespace(embeddings=[SimpleNamespace(values=vector)])
    monkeypatch.setattr(genai, "Client", Mock(return_value=client))
    result = context.embed_texts(["Password recovery"])
    assert result[0][0] == 1.0
    config = client.models.embed_content.call_args.kwargs["config"]
    assert config.task_type == "SEMANTIC_SIMILARITY"
    assert config.output_dimensionality == 768
