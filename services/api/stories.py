"""Human review actions and the revision history of each story."""

from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import func
from sqlmodel import select

from services.models import AnalyticsEvent, Import, Meeting, Story, StoryRevision

from .dependencies import SessionDep, get_record
from .schemas import StoryDecision, StoryEdit, StoryRead

router = APIRouter(prefix="/api/stories", tags=["Stories"])


def check_pending_import(session, story: Story) -> None:
    pending = session.exec(
        select(Import.id).where(Import.story_id == story.id, Import.status == "pending")
    ).first()
    if pending is not None:
        raise HTTPException(status_code=409, detail="Finish or reconcile the pending export first")


def record_event(session, story: Story, event_type: str) -> None:
    meeting = get_record(session, Meeting, story.meeting_id)
    session.add(AnalyticsEvent(
        board_id=meeting.board_id, story_id=story.id, event_type=event_type,
        confidence=story.confidence, invest_score=story.invest_score,
    ))


@router.get("/{story_id}", response_model=StoryRead)
def get_story(story_id: UUID, session: SessionDep):
    return get_record(session, Story, story_id)


@router.get("/{story_id}/revisions", response_model=list[StoryRevision])
def get_revisions(story_id: UUID, session: SessionDep):
    get_record(session, Story, story_id)
    return session.exec(
        select(StoryRevision).where(StoryRevision.story_id == story_id)
        .order_by(StoryRevision.revision)
    ).all()


@router.patch("/{story_id}", response_model=StoryRead)
def edit_story(story_id: UUID, body: StoryEdit, session: SessionDep):
    story = get_record(session, Story, story_id, lock=True)
    check_pending_import(session, story)
    if story.status == "imported":
        raise HTTPException(status_code=409, detail="Imported stories cannot be edited here")
    latest_revision = session.exec(
        select(func.max(StoryRevision.revision)).where(StoryRevision.story_id == story_id)
    ).one() or 0
    if latest_revision == 0:
        # Preserve the original even for records created outside the processing job.
        session.add(StoryRevision(story_id=story.id, revision=1, draft=story.draft, editor="ai"))
        latest_revision = 1
    story.draft = body.draft.model_dump(mode="json")
    story.status = "draft"
    # Scores describe the previous draft and must not be presented as current.
    story.confidence = None
    story.invest_score = None
    story.critique = None
    session.add(StoryRevision(
        story_id=story.id, revision=latest_revision + 1, draft=story.draft, editor="human",
    ))
    record_event(session, story, "story_edited")
    session.commit()
    session.refresh(story)
    return story


@router.post("/{story_id}/decision", response_model=StoryRead)
def decide_story(story_id: UUID, body: StoryDecision, session: SessionDep):
    story = get_record(session, Story, story_id, lock=True)
    check_pending_import(session, story)
    if story.status == "imported":
        raise HTTPException(status_code=409, detail="Imported stories cannot be reviewed here")
    new_status = "accepted" if body.decision == "accept" else "rejected"
    if story.status != new_status:
        story.status = new_status
        record_event(session, story, f"story_{new_status}")
        session.commit()
        session.refresh(story)
    return story
