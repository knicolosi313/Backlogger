"""Human review actions and the revision history of each story."""

import copy
import logging
import os
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import func
from sqlmodel import select

from services.models import AnalyticsEvent, Import, Meeting, PipelineOutput, Story, StoryRevision, utc_now
from services.worker.board_context import build_board_context
from services.worker.objects import DraftStory, Transcript, TranscriptSegment
from services.worker.revision import RevisionError, revise_story

from .dependencies import SessionDep, get_record
from .gemini_models import available_models, default_model
from .schemas import EditRequest, EditRequestResult, ImportRead, StoryDecision, StoryEdit, StoryRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stories", tags=["Stories"])


def check_pending_import(session, story: Story) -> None:
    pending = session.exec(
        select(Import.id).where(Import.story_id == story.id, Import.status == "pending")
    ).first()
    if pending is not None:
        raise HTTPException(status_code=409, detail="Finish or reconcile the pending export first")


def check_not_deleted(story: Story) -> None:
    if story.deleted_at is not None:
        raise HTTPException(status_code=409, detail="This story was deleted")


def record_event(session, story: Story, event_type: str) -> None:
    meeting = get_record(session, Meeting, story.meeting_id)
    session.add(AnalyticsEvent(
        board_id=meeting.board_id, story_id=story.id, event_type=event_type,
        confidence=story.confidence, invest_score=story.invest_score,
    ))


def latest_revision_number(session, story: Story) -> int:
    """The newest revision number, saving the original as revision 1 if it was never recorded."""
    latest = session.exec(
        select(func.max(StoryRevision.revision)).where(StoryRevision.story_id == story.id)
    ).one() or 0
    if latest == 0:
        # Preserve the original even for records created outside the processing job.
        session.add(StoryRevision(story_id=story.id, revision=1, draft=story.draft, editor="ai"))
        latest = 1
    return latest


@router.get("/{story_id}", response_model=StoryRead)
def get_story(story_id: UUID, session: SessionDep):
    return get_record(session, Story, story_id)


@router.get("/{story_id}/import", response_model=ImportRead)
def get_story_import(story_id: UUID, session: SessionDep):
    """Read the saved export result without calling Trello or retrying creation."""
    story = get_record(session, Story, story_id)
    import_record = session.exec(select(Import).where(Import.story_id == story_id)).first()
    if import_record is None:
        raise HTTPException(status_code=404, detail="Story has no export attempt")
    return ImportRead.model_validate(import_record).model_copy(update={"story_title": story.draft.get("title")})


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
    check_not_deleted(story)
    check_pending_import(session, story)
    if story.status == "imported":
        raise HTTPException(status_code=409, detail="Imported stories cannot be edited here")
    latest_revision = latest_revision_number(session, story)
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


@router.delete("/{story_id}", status_code=204)
def delete_story(story_id: UUID, session: SessionDep):
    """Remove a rejected draft from the app. The row stays in the database, along with its history.

    Deleting an already deleted story does nothing. Deleted stories no longer appear in story lists
    and can't be changed, but analytics still count them.
    """
    story = get_record(session, Story, story_id, lock=True)
    if story.deleted_at is not None:
        return Response(status_code=204)
    check_pending_import(session, story)
    if story.status != "rejected":
        raise HTTPException(status_code=409, detail="Only rejected stories can be deleted")
    story.deleted_at = utc_now()
    record_event(session, story, "story_deleted")
    session.commit()
    return Response(status_code=204)


@router.post("/{story_id}/decision", response_model=StoryRead)
def decide_story(story_id: UUID, body: StoryDecision, session: SessionDep):
    story = get_record(session, Story, story_id, lock=True)
    check_not_deleted(story)
    check_pending_import(session, story)
    if story.status == "imported":
        raise HTTPException(status_code=409, detail="Imported stories cannot be reviewed here")
    if body.decision == "restore":
        if story.status not in {"rejected", "draft"}:
            raise HTTPException(status_code=409, detail="Only rejected stories can be restored to drafts")
        new_status = "draft"
        event_type = "story_restored"
    else:
        new_status = "accepted" if body.decision == "accept" else "rejected"
        event_type = f"story_{new_status}"
    if story.status != new_status:
        story.status = new_status
        record_event(session, story, event_type)
        session.commit()
        session.refresh(story)
    return story


def _story_text(draft: dict) -> str:
    return " ".join(str(draft.get(key) or "") for key in ("title", "as_a", "i_want", "so_that"))


def _apply_review(story: Story, review) -> None:
    story.confidence = review.confidence
    story.invest_score = review.invest_score
    story.critique = review.model_dump(mode="json")


@router.post("/{story_id}/request-edits", response_model=EditRequestResult)
def request_story_edits(story_id: UUID, body: EditRequest, session: SessionDep):
    """Have the AI rewrite a draft from a reviewer's instructions, then review the result again.

    Takes a while: it looks up related board work, rewrites the story from the meeting transcript, and
    re-runs the INVEST review. Nothing stays locked meanwhile, so if the story is changed by someone
    else in the meantime the request is refused rather than overwriting their work.
    """
    story = get_record(session, Story, story_id)
    check_not_deleted(story)
    check_pending_import(session, story)
    if story.status == "imported":
        raise HTTPException(status_code=409, detail="Imported stories cannot be edited here")
    if not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY is not configured")
    model = body.model or default_model()
    if model not in available_models():
        raise HTTPException(status_code=422, detail=f"Unsupported model: {model}")

    meeting = get_record(session, Meeting, story.meeting_id)
    saved_transcript = session.exec(
        select(PipelineOutput)
        .where(PipelineOutput.meeting_id == meeting.id, PipelineOutput.stage == "transcribe")
        .order_by(PipelineOutput.created_at.desc(), PipelineOutput.id).limit(1)
    ).first()
    if saved_transcript is None:
        raise HTTPException(status_code=409, detail="This story's meeting has no transcript to revise it from")
    transcript = Transcript.model_validate(saved_transcript.output)

    # What the story looked like when the work started, to notice if it changes under us.
    original_draft = copy.deepcopy(story.draft)
    original_version = story.updated_at
    meeting_id = meeting.id

    # Find related work on the board using the story and the request, not the whole meeting.
    focus = Transcript(language=transcript.language, segments=[TranscriptSegment(
        speaker="Reviewer", start_s=0, end_s=0, text=f"{_story_text(original_draft)} {body.instructions}",
    )])
    try:
        context = build_board_context(session, meeting_id, focus)
    except Exception:
        logger.warning("Board context unavailable for story %s; revising without it", story_id, exc_info=True)
        context = None
    # End the read transaction so no connection sits open while the AI works.
    session.rollback()

    try:
        revision, reviews = revise_story(original_draft, body.instructions, transcript, context, model)
    except RevisionError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None
    except Exception:
        # Provider errors can contain request data or credentials, so keep them out of the response.
        logger.exception("Revising story %s failed", story_id)
        raise HTTPException(status_code=502, detail="The AI could not revise this story. Try again.") from None

    story = get_record(session, Story, story_id, lock=True)
    check_not_deleted(story)
    check_pending_import(session, story)
    if story.status == "imported":
        raise HTTPException(status_code=409, detail="Imported stories cannot be edited here")
    if story.draft != original_draft or story.updated_at != original_version:
        raise HTTPException(status_code=409, detail="This story was changed while the AI was working. Try again.")

    latest_revision = latest_revision_number(session, story)
    first, *others = revision.stories
    story.draft = first.model_dump(mode="json")
    story.status = "draft"
    # The review is for the revised draft, so these scores are current.
    _apply_review(story, reviews[0])
    session.add(StoryRevision(story_id=story.id, revision=latest_revision + 1, draft=story.draft, editor="ai"))
    record_event(session, story, "story_ai_edited")

    new_stories = []
    for draft, review in zip(others, reviews[1:]):
        child = Story(meeting_id=story.meeting_id, draft=draft.model_dump(mode="json"))
        _apply_review(child, review)
        session.add(child)
        session.flush()
        session.add(StoryRevision(story_id=child.id, revision=1, draft=child.draft, editor="ai"))
        record_event(session, child, "story_created")
        new_stories.append(child)
    session.commit()
    session.refresh(story)
    for child in new_stories:
        session.refresh(child)
    return EditRequestResult(
        story=StoryRead.model_validate(story),
        new_stories=[StoryRead.model_validate(child) for child in new_stories],
        summary=revision.summary,
    )

