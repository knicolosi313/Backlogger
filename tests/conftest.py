"""Shared synthetic data and opt-in database access."""

import os
from types import SimpleNamespace

import pytest

from services.db import get_engine
from services.worker.objects import ClockSegment, ClockTranscript, CritiqueResult, ExtractionResult, Transcript
from services.worker.timecode import format_clock


@pytest.fixture(autouse=True)
def fresh_trello_rate_limit():
    """Every test starts with an unused Trello request budget, so none waits on another's calls."""
    from adapters.trello import client

    client._sent.clear()


@pytest.fixture(autouse=True)
def disable_real_embeddings(monkeypatch):
    """Tests opt into embeddings explicitly and supply a fake embedding client."""
    monkeypatch.setenv("STORY_EMBEDDINGS_ENABLED", "false")


@pytest.fixture
def pipeline_results():
    transcript = Transcript.model_validate({
        "language": "en",
        "segments": [{
            "speaker": "Alex", "start_s": 2.0, "end_s": 5.0,
            "text": "Members should be able to reset their password by email.",
        }],
    })
    extraction = ExtractionResult.model_validate({
        "stories": [{
            "title": "Reset a password",
            "as_a": "member",
            "i_want": "to reset my password by email",
            "so_that": "I can access my account",
            "acceptance_criteria": [
                "Given a member, when they request a reset, then the request is accepted",
                "Given a reset request, when it is processed, then a reset email is sent",
            ],
            "solution_tasks": ["Add the password reset request flow", "Implement reset email delivery"],
            "priority": "high",
            "needs_clarification": False,
            "source": {
                "quote": transcript.segments[0].text,
                "speaker": "Alex", "start_s": 2.0, "end_s": 5.0,
            },
        }],
        "open_questions": [{"question": "How long should the reset link last?"}],
    })
    critique = CritiqueResult.model_validate({
        "reviews": [{
            "index": 0, "invest_score": 5, "confidence": 0.9,
            "flags": [{"criterion": "Testable", "issue": "Link expiration is unspecified"}],
            "recommend_split": False, "recommend_reject": False,
        }],
    })
    return transcript, extraction, critique


@pytest.fixture
def database_connection():
    if os.getenv("RUN_DATABASE_TESTS") != "1":
        pytest.skip("Set RUN_DATABASE_TESTS=1 to test the migrated PostgreSQL database")

    with get_engine().connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


@pytest.fixture
def gemini_responses(pipeline_results):
    """What Gemini returns for each pass. The transcript pass answers in MM:SS, not seconds."""
    transcript, *later_passes = pipeline_results
    clock = ClockTranscript(language=transcript.language, segments=[
        ClockSegment(
            speaker=segment.speaker, start=format_clock(segment.start_s),
            end=format_clock(segment.end_s), text=segment.text,
        )
        for segment in transcript.segments
    ])
    return [SimpleNamespace(parsed=result) for result in (clock, *later_passes)]
