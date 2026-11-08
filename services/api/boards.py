"""Board registration, draft listings, and analytics."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import case, func, literal_column
from sqlmodel import select

from services.models import AnalyticsEvent, Board, Meeting, Story, StoryRevision

from .dependencies import SessionDep, get_record
from .schemas import BoardAnalytics, BoardCreate, DailyAnalytics, StoryRead, StoryStatus

router = APIRouter(prefix="/api/boards", tags=["Boards"])


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
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    get_record(session, Board, board_id)
    query = select(Story).join(Meeting).where(Meeting.board_id == board_id)
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
    return BoardAnalytics(
        board_id=board_id,
        story_counts=counts,
        acceptance_rate=accepted / reviewed if reviewed else None,
        draft_outcome_counts=outcome_counts,
        draft_outcome_percentages=outcome_percentages,
        average_invest_score=average_score,
        average_confidence=average_confidence,
        daily_events=[DailyAnalytics(day=day, event_type=kind, count=count) for day, kind, count in events],
    )
