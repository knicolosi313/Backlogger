"""Real PostgreSQL constraints; every synthetic write is rolled back."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from services.models import Board, Import, Meeting, PipelineOutput, Story
from services.worker import pipeline


@pytest.fixture
def session(database_connection):
    with Session(bind=database_connection, join_transaction_mode="create_savepoint") as session:
        yield session


@pytest.fixture
def story(session):
    board = Board(provider="trello", external_id=f"test-{uuid4()}", name="Synthetic board")
    session.add(board)
    session.flush()
    meeting = Meeting(board_id=board.id, title="Synthetic meeting")
    session.add(meeting)
    session.flush()
    story = Story(meeting_id=meeting.id, draft={"title": "Synthetic story"})
    session.add(story)
    session.flush()
    return story


def test_import_key_cannot_be_reused_for_another_story(session, story):
    other_story = Story(meeting_id=story.meeting_id, draft={"title": "Other story"})
    session.add(other_story)
    session.flush()
    key = f"synthetic-{uuid4()}"
    session.add(Import(story_id=story.id, idempotency_key=key))
    session.flush()

    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(Import(story_id=other_story.id, idempotency_key=key))
            session.flush()


def test_story_cannot_have_two_imports_even_with_different_keys(session, story):
    session.add(Import(story_id=story.id, idempotency_key=str(uuid4())))
    session.flush()

    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(Import(story_id=story.id, idempotency_key=str(uuid4())))
            session.flush()


def test_completed_import_requires_card_id(session, story):
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(Import(
                story_id=story.id, idempotency_key=str(uuid4()), status="completed",
            ))
            session.flush()

    session.add(Import(
        story_id=story.id, idempotency_key=str(uuid4()),
        status="completed", external_card_id="synthetic-card",
    ))
    session.flush()


def test_meeting_requires_an_existing_board(session):
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(Meeting(board_id=uuid4(), title="Missing board"))
            session.flush()


def test_pipeline_cannot_save_the_same_stage_twice_in_one_run(session, story):
    run_id = uuid4()
    for attempt in range(2):
        output = PipelineOutput(
            meeting_id=story.meeting_id, run_id=run_id,
            stage="transcribe", model="test", output={"language": "en", "segments": []},
        )
        if attempt == 0:
            session.add(output)
            session.flush()
        else:
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(output)
                    session.flush()


def test_story_rejects_unknown_status(session, story):
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            story.status = "published"
            session.flush()


def test_analytics_table_is_a_hypertable(session):
    name = session.execute(text("""
        SELECT hypertable_name
        FROM timescaledb_information.hypertables
        WHERE hypertable_schema = 'backlogger' AND hypertable_name = 'analytics_events'
    """)).scalar_one()
    assert name == "analytics_events"


def test_unknown_meeting_is_rejected_before_gemini(database_connection, monkeypatch):
    def new_session(*args):
        return Session(bind=database_connection, join_transaction_mode="create_savepoint")

    def unexpected_gemini_call(*args):
        pytest.fail("Gemini should not run for a missing meeting")

    monkeypatch.setattr(pipeline, "get_engine", lambda: None)
    monkeypatch.setattr(pipeline, "Session", new_session)
    monkeypatch.setattr(pipeline, "_run_pipeline", unexpected_gemini_call)

    with pytest.raises(ValueError, match="Meeting does not exist"):
        pipeline.run_pipeline("synthetic.wav", meeting_id=uuid4())
