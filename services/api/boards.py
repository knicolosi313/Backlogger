"""Board registration, draft listings, and analytics."""

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, literal_column
from sqlmodel import select

from adapters.trello.client import TrelloClient
from adapters.jira.client import JiraClient

from services.models import AnalyticsEvent, Board, Import, Meeting, Story, StoryRevision

from .dependencies import SessionDep, get_record
from .exports import export_story
from .export_clients import get_export_client
from .jira_exports import export_jira_story
from .schemas import ImportCreate, ImportRead, JiraImportCreate
from .schemas import AttentionItem, BoardAnalytics, BoardCreate, DailyAnalytics, StoryRead, StoryStatus

router = APIRouter(prefix="/api/boards", tags=["Boards"])

# Pending stories at or below these need a human look first (INVEST score is 0-6, so 3 is 50%).
LOW_INVEST_SCORE = 3
LOW_CONFIDENCE = 0.6


@router.post("/{board_id}/imports", response_model=ImportRead)
def import_story(
    board_id: UUID,
    body: ImportCreate | JiraImportCreate,
    session: SessionDep,
    client: Annotated[TrelloClient | JiraClient, Depends(get_export_client)],
):
    if isinstance(body, JiraImportCreate):
        if not isinstance(client, JiraClient):
            raise HTTPException(status_code=409, detail="Jira export fields require a Jira board")
        return export_jira_story(session, client, board_id, body)
    if not isinstance(client, TrelloClient):
        raise HTTPException(status_code=409, detail="Trello export fields require a Trello board")
    return export_story(session, client, board_id, body)


@router.get("/{board_id}/imports", response_model=list[ImportRead])
def list_imports(
    board_id: UUID,
    session: SessionDep,
    status: Literal["pending", "completed", "failed"] | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Read export history, including attempts that still need reconciliation."""
    get_record(session, Board, board_id)
    query = (
        select(Import).join(Story, Import.story_id == Story.id)
        .join(Meeting, Story.meeting_id == Meeting.id)
        .where(Meeting.board_id == board_id)
    )
    if status is not None:
        query = query.where(Import.status == status)
    return session.exec(
        query.order_by(Import.created_at.desc(), Import.id).offset(offset).limit(limit)
    ).all()


@router.post("", response_model=Board, status_code=201)
def create_board(body: BoardCreate, session: SessionDep):
    board = Board(**body.model_dump())
    session.add(board)
    session.commit()
    session.refresh(board)
    return board


@router.get("", response_model=list[Board])
def list_boards(
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return session.exec(
        select(Board).order_by(Board.created_at.desc(), Board.id).offset(offset).limit(limit)
    ).all()


@router.get("/{board_id}", response_model=Board)
def get_board(board_id: UUID, session: SessionDep):
    return get_record(session, Board, board_id)


@router.get("/{board_id}/stories", response_model=list[StoryRead])
def list_stories(
    board_id: UUID,
    session: SessionDep,
    status: StoryStatus | None = None,
    meeting_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    get_record(session, Board, board_id)
    query = select(Story).join(Meeting).where(Meeting.board_id == board_id)
    if meeting_id is not None:
        query = query.where(Story.meeting_id == meeting_id)
    if status is not None:
        query = query.where(Story.status == status)
    return session.exec(
        query.order_by(Story.created_at.desc(), Story.id).offset(offset).limit(limit)
    ).all()


@router.get("/{board_id}/analytics", response_model=BoardAnalytics)
def board_analytics(
    board_id: UUID, session: SessionDep, days: int = Query(default=30, ge=1, le=365),
):
    get_record(session, Board, board_id)
    counts = dict.fromkeys(["draft", "needs_changes", "accepted", "imported", "rejected"], 0)
    rows = session.exec(
        select(Story.status, func.count(Story.id))
        .join(Meeting).where(Meeting.board_id == board_id).group_by(Story.status)
    ).all()
    counts.update(dict(rows))
    accepted = counts["accepted"] + counts["imported"]
    reviewed = accepted + counts["rejected"]

    # A human revision counts as an edit, even after the story is accepted.
    # EXISTS counts each story once regardless of how many edits it has.
    has_human_edit = select(StoryRevision.id).where(
        StoryRevision.story_id == Story.id,
        StoryRevision.editor == "human",
    ).exists()
    outcome = case(
        (Story.status == "rejected", "rejected"),
        (has_human_edit, "edited"),
        (Story.status.in_(["accepted", "imported"]), "accepted"),
        else_=None,
    )
    outcome_rows = session.exec(
        select(outcome, func.count(Story.id))
        .join(Meeting, Story.meeting_id == Meeting.id)
        .where(Meeting.board_id == board_id)
        .group_by(outcome)
    ).all()
    outcome_counts = {"accepted": 0, "edited": 0, "rejected": 0}
    for name, count in outcome_rows:
        if name is not None:
            outcome_counts[name] = count
    outcome_total = sum(outcome_counts.values())
    outcome_percentages = {
        name: round(count / outcome_total * 100, 2) if outcome_total else 0.0
        for name, count in outcome_counts.items()
    }

    average_score, average_confidence = session.exec(
        select(func.avg(Story.invest_score), func.avg(Story.confidence))
        .join(Meeting, Story.meeting_id == Meeting.id).where(Meeting.board_id == board_id)
    ).one()
    day = func.time_bucket(literal_column("INTERVAL '1 day'"), AnalyticsEvent.occurred_at)
    events = session.exec(
        select(day, AnalyticsEvent.event_type, func.count(AnalyticsEvent.id))
        .where(
            AnalyticsEvent.board_id == board_id,
            AnalyticsEvent.occurred_at >= datetime.now(timezone.utc) - timedelta(days=days),
        )
        .group_by(day, AnalyticsEvent.event_type).order_by(day, AnalyticsEvent.event_type)
    ).all()
    invest_issues = dict.fromkeys(
        ["independent", "negotiable", "valuable", "estimable", "small", "testable", "grounded"], 0,
    )
    needs_attention = []
    board_stories = session.exec(
        select(Story).join(Meeting, Story.meeting_id == Meeting.id)
        .where(Meeting.board_id == board_id, Story.status != "rejected")
        .order_by(Story.created_at.desc(), Story.id)
    ).all()
    for story in board_stories:
        # Count each story once per criterion, however many flags it has.
        flagged = {
            flag["criterion"].lower() for flag in (story.critique or {}).get("flags", [])
            if isinstance(flag.get("criterion"), str)
        }
        for criterion in flagged & invest_issues.keys():
            invest_issues[criterion] += 1

        if story.status in {"draft", "needs_changes"} and len(needs_attention) < 20:
            # Scores are null after a human edit; an unscored story is not "low".
            if not str((story.draft.get("source") or {}).get("quote") or "").strip():
                reason = "No evidence from the meeting"
            elif story.invest_score is not None and story.invest_score <= LOW_INVEST_SCORE:
                reason = "Low INVEST quality"
            elif story.confidence is not None and story.confidence < LOW_CONFIDENCE:
                reason = "Low confidence"
            else:
                continue
            needs_attention.append(AttentionItem(
                story_id=story.id, title=story.draft.get("title", "Untitled story"), reason=reason,
            ))

    return BoardAnalytics(
        board_id=board_id,
        story_counts=counts,
        invest_issues=invest_issues,
        needs_attention=needs_attention,
        acceptance_rate=accepted / reviewed if reviewed else None,
        draft_outcome_counts=outcome_counts,
        draft_outcome_percentages=outcome_percentages,
        average_invest_score=average_score,
        average_confidence=average_confidence,
        daily_events=[DailyAnalytics(day=day, event_type=kind, count=count) for day, kind, count in events],
    )
