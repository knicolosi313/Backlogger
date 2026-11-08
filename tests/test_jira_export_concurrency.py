"""Jira parallel exports use separate sessions and retain token-refresh locking."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from services.api import auto_import, jira_auth
from services.models import Board, Meeting, Story


def test_account_lock_is_released_before_manual_export(monkeypatch):
    session = Mock()
    account, client = object(), object()
    monkeypatch.setattr(jira_auth, "browser_connection", Mock(return_value=account))
    monkeypatch.setattr(jira_auth, "client_for_connection", Mock(return_value=client))
    assert jira_auth.get_jira_client("request", session) is client
    jira_auth.browser_connection.assert_called_once_with("request", session, lock=True)
    jira_auth.client_for_connection.assert_called_once_with(account, session)
    session.commit.assert_called_once()


def test_jira_auto_exports_overlap_in_distinct_sessions(monkeypatch, pipeline_results):
    meeting_id, board_id, connection_id = uuid4(), uuid4(), uuid4()
    draft = pipeline_results[1].stories[0].model_dump(mode="json")
    review = pipeline_results[2].reviews[0].model_copy(update={"flags": [], "invest_score": 6, "confidence": 0.95}).model_dump(mode="json")
    stories = [Story(
        id=uuid4(), meeting_id=meeting_id, status="accepted", draft=draft,
        critique=review, confidence=0.95, invest_score=6,
    ) for _ in range(4)]
    run = SimpleNamespace(status="pending", story_ids=[str(story.id) for story in stories], connection_id=connection_id, options={
        "account_identity": "original-account",
        "settings": {"confidence_threshold": 0.85, "issue_type_id": "10002", "additional_fields": {}, "evidence_base_url": "https://example.test"},
    })
    meeting = SimpleNamespace(id=meeting_id, board_id=board_id, status="completed")
    board = SimpleNamespace(id=board_id, provider="jira")
    sessions = []
    def new_session(*args):
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        if not sessions:
            session.exec.side_effect = [Mock(first=Mock(return_value=run)), Mock(all=Mock(return_value=stories))]
        else:
            session.exec.return_value.first.return_value = "account"
        sessions.append(session)
        return session
    def get_record(session, model, record_id, **kwargs):
        if model is Meeting:
            return meeting
        if model is Board:
            return board
        return next(story for story in stories if story.id == record_id)
    barrier = threading.Barrier(3, timeout=5)
    exported_sessions = []
    def export(session, client, destination, body):
        assert session.commit.called  # account lock released before the issue API
        exported_sessions.append(id(session))
        if body.story_id in {story.id for story in stories[:3]}:
            barrier.wait()  # Sequential exports fail here instead of silently passing.
    monkeypatch.setattr(auto_import, "Session", new_session)
    monkeypatch.setattr(auto_import, "get_engine", lambda: None)
    monkeypatch.setattr(auto_import, "get_record", get_record)
    monkeypatch.setattr(auto_import, "account_identity", lambda *args: "original-account")
    monkeypatch.setattr(jira_auth, "client_for_connection", lambda *args: "client")
    monkeypatch.setattr(auto_import, "export_jira_story", export)
    progress = Mock()
    monkeypatch.setattr(auto_import, "save_progress", progress)
    auto_import.run_auto_import(meeting_id)
    assert len(set(exported_sessions)) == 4
    assert progress.call_args.kwargs == {"status": "completed", "imported": 4, "skipped": 0, "failed": 0, "error": None}
