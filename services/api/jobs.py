"""In-process meeting processing for the local MVP."""

import logging
from uuid import UUID

from sqlmodel import Session

from services.db import get_engine
from services.models import AnalyticsEvent, Meeting, Story, StoryRevision
from services.worker.pipeline import run_pipeline
from services.worker.prompts.extract import CreativityLevel

from .media import media_path

logger = logging.getLogger(__name__)


def process_meeting(
    meeting_id: UUID, media_key: str, model: str, creativity: CreativityLevel = "balanced",
) -> None:
    """Save raw pipeline outputs, then create reviewable drafts in one transaction."""
    try:
        _, extraction, critique = run_pipeline(
            str(media_path(media_key)), model=model, meeting_id=meeting_id,
            mark_completed=False,
            creativity=creativity,
        )
        reviews = {review.index: review for review in critique.reviews}
        if len(reviews) != len(critique.reviews) or set(reviews) != set(range(len(extraction.stories))):
            raise ValueError("Critique must contain one review per extracted story")

        with Session(get_engine()) as session, session.begin():
            meeting = session.get(Meeting, meeting_id)
            if meeting is None:
                raise ValueError("Meeting no longer exists")
            for index, draft in enumerate(extraction.stories):
                review = reviews[index]
                story = Story(
                    meeting_id=meeting_id,
                    draft=draft.model_dump(mode="json"),
                    confidence=review.confidence,
                    invest_score=review.invest_score,
                    critique=review.model_dump(mode="json"),
                )
                session.add(story)
                session.flush()
                session.add(StoryRevision(
                    story_id=story.id, revision=1, draft=story.draft, editor="ai",
                ))
                session.add(AnalyticsEvent(
                    board_id=meeting.board_id, story_id=story.id,
                    event_type="story_created", confidence=story.confidence,
                    invest_score=story.invest_score,
                ))
            meeting.status = "completed"
    except Exception:
        # Keep exception details out of API responses; raw completed stages remain saved.
        logger.error("Meeting %s processing failed", meeting_id)
        try:
            with Session(get_engine()) as session, session.begin():
                meeting = session.get(Meeting, meeting_id)
                if meeting is not None:
                    meeting.status = "failed"
        except Exception:
            logger.error("Could not save failure status for meeting %s", meeting_id)
