"""Offline checks plus opt-in integration tests against the migrated database."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, SQLModel, select

from services.api.server import app
from services.db import database_url, get_engine, get_session
from services.models import (
    AnalyticsEvent,
    Board,
    Import,
    Meeting,
    PipelineOutput,
    SchemaMigration,
    Story,
    StoryRevision,
)
from services.worker import pipeline
from services.worker.objects import CritiqueResult, ExtractionResult, Transcript


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+psycopg"])
def test_database_url_selects_driver_and_preserves_ssl(scheme):
    url = database_url(f"{scheme}://user:p%40ss@localhost:5432/tsdb?sslmode=require")
    assert url.drivername == "postgresql+psycopg"
    assert url.password == "p@ss"
    assert url.query["sslmode"] == "require"


def test_invalid_url_error_does_not_expose_credentials():
    with pytest.raises(ValueError) as error:
        database_url("postgresql://user:secret@host:invalid/tsdb")
    assert "secret" not in str(error.value)


def test_health_dependency_and_unavailable_response():
    class FakeSession:
        fail = False

        def exec(self, query):
            if self.fail:
                raise SQLAlchemyError("private connection information")

    fake = FakeSession()
    app.dependency_overrides[get_session] = lambda: fake
    try:
        with TestClient(app) as client:
            assert client.get("/health/db").json() == {"status": "ok"}
            fake.fail = True
            response = client.get("/health/db")
            assert response.status_code == 503
            assert response.json() == {"detail": "Database unavailable"}
    finally:
        app.dependency_overrides.clear()


def test_pipeline_without_meeting_does_not_connect(monkeypatch):
    def fail_if_connected():
        pytest.fail("Standalone pipeline tried to connect to the database")

    expected = ("transcript", "extraction", "critique")
    monkeypatch.setattr(pipeline, "get_engine", fail_if_connected)
    monkeypatch.setattr(pipeline, "_run_pipeline", lambda *args, **kwargs: expected)
    assert pipeline.run_pipeline("sample.wav") == expected


def test_mappings_match_migrated_columns(database_connection):
    inspector = inspect(database_connection)
    dialect = database_connection.dialect

    for table in SQLModel.metadata.sorted_tables:
        database_columns = {
            column["name"]: column
            for column in inspector.get_columns(table.name, schema="backlogger")
        }
        assert set(database_columns) == set(table.columns.keys())

        for column in table.columns:
            database_column = database_columns[column.name]
            assert database_column["nullable"] == column.nullable

            database_type = database_column["type"].compile(dialect=dialect)
            model_type = column.type.compile(dialect=dialect)
            assert str(database_type) == str(model_type)

        primary_key = inspector.get_pk_constraint(table.name, schema="backlogger")
        expected_key_columns = [column.name for column in table.primary_key]
        assert primary_key["constrained_columns"] == expected_key_columns


def test_sqlmodel_json_and_relationship_round_trip(database_connection):
    with Session(bind=database_connection, join_transaction_mode="create_savepoint") as session:
        board = Board(provider="trello", external_id=f"test-{uuid4()}", name="Test board")
        session.add(board)
        session.flush()
        meeting = Meeting(board_id=board.id, title="Synthetic test meeting")
        session.add(meeting)
        session.flush()
        draft = {"title": "Synthetic story", "source": {"start_s": 1.5, "end_s": 3.0}}
        story = Story(meeting_id=meeting.id, draft=draft)
        session.add(story)
        session.flush()
        stage_output = PipelineOutput(
            meeting_id=meeting.id,
            run_id=uuid4(),
            stage="extract",
            model="test",
            output={"stories": [draft]},
        )
        revision = StoryRevision(
            story_id=story.id,
            revision=1,
            draft=draft,
            editor="ai",
        )
        card_import = Import(story_id=story.id, idempotency_key=str(uuid4()))
        event = AnalyticsEvent(
            board_id=board.id,
            story_id=story.id,
            event_type="story_created",
            properties={"synthetic": True},
        )
        session.add_all([stage_output, revision, card_import, event])
        session.commit()
        session.expire_all()
        assert session.get(Story, story.id).draft == draft
        assert session.get(Meeting, meeting.id).status == "uploaded"
        assert session.get(SchemaMigration, "001_initial") is not None


@pytest.mark.parametrize("should_fail", [False, True], ids=["success", "failure"])
def test_pipeline_stage_checkpoints_survive_failure(
    database_connection, monkeypatch, should_fail,
):
    def new_session(*args):
        # Pipeline commits release savepoints; the fixture still rolls everything back.
        return Session(bind=database_connection, join_transaction_mode="create_savepoint")

    with new_session() as session:
        board = Board(provider="trello", external_id=f"test-{uuid4()}", name="Test")
        session.add(board)
        session.flush()
        meeting = Meeting(board_id=board.id, title="Synthetic checkpoint test")
        session.add(meeting)
        session.commit()
        meeting_id = meeting.id

    def fake_gemini(audio_path, model, save_stage, *, creativity="balanced"):
        transcript = Transcript(language="en", segments=[])
        save_stage("transcribe", transcript)
        if should_fail:
            raise RuntimeError("Simulated Gemini failure")
        extraction = ExtractionResult(stories=[], open_questions=[])
        critique = CritiqueResult(reviews=[])
        save_stage("extract", extraction)
        save_stage("critique", critique)
        return transcript, extraction, critique

    monkeypatch.setattr(pipeline, "Session", new_session)
    monkeypatch.setattr(pipeline, "get_engine", lambda: None)
    monkeypatch.setattr(pipeline, "_run_pipeline", fake_gemini)
    if should_fail:
        with pytest.raises(RuntimeError, match="Simulated Gemini failure"):
            pipeline.run_pipeline("synthetic.wav", meeting_id=meeting_id)
    else:
        pipeline.run_pipeline("synthetic.wav", meeting_id=meeting_id)
    with new_session() as session:
        query = select(PipelineOutput).where(
            PipelineOutput.meeting_id == meeting_id
        )
        outputs = session.exec(query).all()
        expected_stages = {"transcribe", "extract", "critique"}
        expected_status = "completed"
        if should_fail:
            expected_stages = {"transcribe"}
            expected_status = "failed"

        assert {output.stage for output in outputs} == expected_stages
        assert len({output.run_id for output in outputs}) == 1
        assert session.get(Meeting, meeting_id).status == expected_status
