"""Opted-in automatic exports using the same idempotent services as manual review."""

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from pydantic import ValidationError
from sqlmodel import Session, select

from services.db import get_engine
from services.models import AnalyticsEvent, AutoImportRun, Board, JiraConnection, Meeting, Story, TrelloConnection, utc_now
from services.worker.objects import DraftStory, StoryReview

from . import jira_auth, trello_auth
from .dependencies import SessionDep, get_record
from .exports import export_story
from .jira_exports import export_jira_story, validate_jira_destination
from .schemas import AutoImportOptions, AutoImportStatus, ImportCreate, JiraImportCreate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/meetings", tags=["Auto-import"])

# Trello stories export side by side. TrelloClient keeps their combined requests under Trello's limit.
# Jira stays one at a time because they share one account whose token may need refreshing.
TRELLO_EXPORT_WORKERS = 3


def eligible_story(story: Story, threshold: float, *, retry=False) -> bool:
    """Only unambiguous, discussed, fully reviewed work can skip manual approval."""
    if story.status not in ({"draft", "accepted"} if retry else {"draft"}):
        return False
    if story.confidence is None or not math.isfinite(story.confidence) or story.confidence < threshold or story.invest_score != 6:
        return False
    try:
        draft = DraftStory.model_validate(story.draft)
        review = StoryReview.model_validate(story.critique)
    except (ValidationError, TypeError):
        return False
    source = draft.source
    return bool(
        draft.origin == "discussed" and not draft.assumptions and not draft.needs_clarification
        and draft.acceptance_criteria and all(item.strip() for item in draft.acceptance_criteria)
        and source.quote.strip() and source.speaker.strip()
        and math.isfinite(source.start_s) and math.isfinite(source.end_s)
        and 0 <= source.start_s < source.end_s
        and not review.flags and not review.recommend_reject and not review.recommend_split
        and review.invest_score == 6 and review.confidence >= threshold
        and all(item.relationship == "independent" for item in review.relationships)
    )


def browser_account(request, session, provider):
    if provider == "trello":
        return trello_auth.browser_connection(request, session)
    if provider == "jira":
        return jira_auth.browser_connection(request, session)
    raise HTTPException(409, "Auto-import supports Trello and Jira Cloud")


def account_identity(account, provider):
    return (account.member_id if provider == "trello" else account.account_id) if account else None


def configure_auto_import(request, session, meeting, options):
    board = get_record(session, Board, meeting.board_id)
    if board.provider == "trello" and options.list_id:
        client = trello_auth.get_trello_client(request, session)
        if not any(item["id"] == options.list_id and not item.get("closed") for item in client.list_lists(board.external_id)):
            raise HTTPException(409, "Choose an open list on this Trello board")
    elif board.provider == "jira" and options.issue_type_id:
        client = jira_auth.get_jira_client(request, session)
        try:
            cloud_id, project_id = board.external_id.split(":")
            cloud_id = str(UUID(cloud_id))
            if not project_id.isascii() or not project_id.isdigit():
                raise ValueError()
        except ValueError:
            raise HTTPException(409, "Jira external_id must be CLOUD_UUID:PROJECT_ID") from None
        if not any(site["id"] == cloud_id for site in client.list_sites()):
            raise HTTPException(404, "Jira site is not accessible to this account")
        validate_jira_destination(client, cloud_id, project_id, options.issue_type_id, options.additional_fields)
    else:
        raise HTTPException(409, "Auto-import destination does not match the meeting's provider")
    account = browser_account(request, session, board.provider)
    if account is None:
        raise HTTPException(401, "Connect the destination account before enabling auto-import")
    identity = account_identity(account, board.provider)
    if not identity:
        raise HTTPException(401, "Reconnect the destination account before enabling auto-import")
    # Refreshing Jira credentials can commit and release the original meeting lock.
    session.refresh(meeting, with_for_update=True)
    if meeting.status not in {"uploaded", "failed"}:
        raise HTTPException(409, "Meeting is already processing or completed")
    run = session.exec(select(AutoImportRun).where(AutoImportRun.meeting_id == meeting.id).with_for_update()).first()
    if run is not None and run.status == "running":
        raise HTTPException(409, "Auto-import is already running")
    saved_options = {"settings": options.model_dump(mode="json"), "account_identity": identity}
    if run is None:
        run = AutoImportRun(meeting_id=meeting.id, connection_id=account.id, options=saved_options)
        session.add(run)
    else:
        run.connection_id, run.options = account.id, saved_options
        run.status, run.story_ids, run.error = "pending", [], None
        run.imported, run.skipped, run.failed = 0, 0, 0


def save_progress(meeting_id, **values):
    with Session(get_engine()) as session:
        run = session.exec(select(AutoImportRun).where(AutoImportRun.meeting_id == meeting_id)).one()
        for key, value in values.items():
            setattr(run, key, value)
        run.updated_at = utc_now()
        session.commit()


def processing_failed(meeting_id):
    try:
        save_progress(meeting_id, status="failed", error="Meeting processing failed; no automatic exports were started.")
    except Exception:
        logger.warning("Could not update auto-import status for meeting %s", meeting_id)


def run_auto_import(meeting_id: UUID):
    imported = failed = skipped = 0
    try:
        with Session(get_engine()) as session:
            run = session.exec(select(AutoImportRun).where(AutoImportRun.meeting_id == meeting_id).with_for_update()).first()
            if run is None or run.status != "pending":
                return
            meeting = get_record(session, Meeting, meeting_id)
            if meeting.status != "completed":
                return
            board = get_record(session, Board, meeting.board_id)
            options = AutoImportOptions.model_validate(run.options["settings"])
            identity = run.options["account_identity"]
            stories = session.exec(select(Story).where(Story.meeting_id == meeting_id).order_by(Story.created_at, Story.id)).all()
            if not run.story_ids:
                run.story_ids = [str(story.id) for story in stories if eligible_story(story, options.confidence_threshold)]
            story_ids = [UUID(value) for value in run.story_ids]
            skipped = len(stories) - len(story_ids)
            run.status, run.error = "running", None
            board_id, provider, account_id = board.id, board.provider, run.connection_id
            session.commit()

        def export_one(story_id) -> str:
            """Export one story in its own session. Returns "imported", "skipped" or "failed"."""
            try:
                with Session(get_engine()) as session:
                    story = get_record(session, Story, story_id, lock=True)
                    if story.status == "imported":
                        return "imported"
                    if not eligible_story(story, options.confidence_threshold, retry=True):
                        return "skipped"
                    # Load credentials at export time, honoring disconnects and Jira refresh.
                    if provider == "trello":
                        account = session.get(TrelloConnection, account_id)
                        if account_identity(account, provider) != identity:
                            raise HTTPException(401, "Reconnect the original account")
                        client = trello_auth.client_for_connection(account)
                    else:
                        account = session.exec(select(JiraConnection).where(JiraConnection.id == account_id).with_for_update()).first()
                        if account_identity(account, provider) != identity:
                            raise HTTPException(401, "Reconnect the original account")
                        client = jira_auth.client_for_connection(account, session)
                    # Jira refresh commits. Reacquire the story lock and recheck
                    # so human edits/rejections during refresh cannot be overwritten.
                    session.refresh(story, with_for_update=True)
                    if story.status == "imported":
                        return "imported"
                    if not eligible_story(story, options.confidence_threshold, retry=True):
                        return "skipped"
                    if story.status == "draft":
                        story.status = "accepted"
                        session.add(AnalyticsEvent(
                            board_id=board_id, story_id=story.id, event_type="story_auto_accepted",
                            confidence=story.confidence, invest_score=story.invest_score,
                            properties={"meeting_id": str(meeting_id)},
                        ))
                        session.commit()
                    evidence = f"{str(options.evidence_base_url).rstrip('/')}/api/meetings/{meeting_id}/audio#t={max(0, story.draft['source']['start_s'])}"
                    if provider == "trello":
                        export_story(session, client, board_id, ImportCreate(story_id=story_id, list_id=options.list_id, evidence_url=evidence))
                    else:
                        export_jira_story(session, client, board_id, JiraImportCreate(
                            story_id=story_id, issue_type_id=options.issue_type_id,
                            additional_fields=options.additional_fields, evidence_url=evidence,
                        ))
                    return "imported"
            except Exception:
                logger.warning("Auto-import could not export story %s", story_id)
                return "failed"

        # Results come back in story order, and only this thread updates the counts and progress.
        workers = TRELLO_EXPORT_WORKERS if provider == "trello" else 1
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for outcome in pool.map(export_one, story_ids):
                if outcome == "imported":
                    imported += 1
                elif outcome == "skipped":
                    skipped += 1
                else:
                    failed += 1
                save_progress(meeting_id, imported=imported, skipped=skipped, failed=failed)
        save_progress(
            meeting_id, status="failed" if failed else "completed", imported=imported, skipped=skipped, failed=failed,
            error="Some stories could not be imported. Reconnect/check the destination, then retry; saved exports will be reused." if failed else None,
        )
    except Exception:
        logger.warning("Auto-import failed for meeting %s", meeting_id)
        try:
            save_progress(meeting_id, status="failed", imported=imported, skipped=skipped, failed=max(1, failed), error="Auto-import could not finish. Check the connection and destination, then retry.")
        except Exception:
            logger.warning("Could not save auto-import failure for meeting %s", meeting_id)


def owned_run(meeting_id, request, session, *, lock=False):
    meeting = get_record(session, Meeting, meeting_id)
    query = select(AutoImportRun).where(AutoImportRun.meeting_id == meeting_id)
    run = session.exec(query.with_for_update() if lock else query).first()
    if run is None:
        raise HTTPException(404, "Auto-import was not enabled for this meeting")
    board = get_record(session, Board, meeting.board_id)
    account = browser_account(request, session, board.provider)
    if account is None or account.id != run.connection_id or account_identity(account, board.provider) != run.options.get("account_identity"):
        raise HTTPException(403, "Use the account that enabled auto-import for this meeting")
    return run, meeting


@router.get("/{meeting_id}/auto-import", response_model=AutoImportStatus)
def get_auto_import(meeting_id: UUID, request: Request, session: SessionDep, response: Response):
    run, _ = owned_run(meeting_id, request, session)
    response.headers["Cache-Control"] = "private, no-store"
    return run


@router.post("/{meeting_id}/auto-import/retry", response_model=AutoImportStatus, status_code=202)
def retry_auto_import(meeting_id: UUID, request: Request, session: SessionDep, tasks: BackgroundTasks):
    run, meeting = owned_run(meeting_id, request, session, lock=True)
    if meeting.status != "completed":
        raise HTTPException(409, "Finish meeting processing before retrying auto-import")
    if run.status == "completed":
        return run
    if run.status == "running" and run.updated_at > utc_now() - timedelta(minutes=15):
        raise HTTPException(409, "Auto-import is still running")
    run.status, run.error = "pending", None
    session.flush()
    result = AutoImportStatus.model_validate(run)
    # Leave no open request transaction while the background worker commits.
    session.commit()
    tasks.add_task(run_auto_import, meeting_id)
    return result
