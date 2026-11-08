"""Request and response bodies for the API."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from services.worker.objects import DraftStory, StoryReview

MeetingStatus = Literal["uploaded", "processing", "completed", "failed"]
StoryStatus = Literal["draft", "needs_changes", "accepted", "imported", "rejected"]
DraftOutcome = Literal["accepted", "edited", "rejected"]


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BoardCreate(RequestBody):
    provider: Literal["trello", "github", "jira"] = "trello"
    external_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)


class TrelloBoardRead(BaseModel):
    """A board returned by Trello; id is the external ID, not our database UUID."""

    id: str
    name: str
    url: str | None = None
    closed: bool = False


class TrelloListRead(BaseModel):
    id: str
    name: str


class MeetingCreate(RequestBody):
    board_id: UUID
    title: str = Field(min_length=1, max_length=255)


class ProcessAccepted(BaseModel):
    meeting_id: UUID
    status: MeetingStatus
    status_url: str


class StoryEdit(RequestBody):
    draft: DraftStory


class StoryDecision(RequestBody):
    decision: Literal["accept", "reject"]


class ImportCreate(RequestBody):
    story_id: UUID
    list_id: str = Field(min_length=1, max_length=255)
    evidence_url: HttpUrl


class ImportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    story_id: UUID
    idempotency_key: str
    external_card_id: str | None
    status: Literal["pending", "completed", "failed"]
    created_at: datetime


class StoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    meeting_id: UUID
    draft: DraftStory
    status: StoryStatus
    confidence: float | None
    invest_score: int | None
    critique: StoryReview | None
    created_at: datetime
    updated_at: datetime


class DailyAnalytics(BaseModel):
    day: datetime
    event_type: str
    count: int


class BoardAnalytics(BaseModel):
    board_id: UUID
    story_counts: dict[str, int]
    acceptance_rate: float | None
    draft_outcome_counts: dict[DraftOutcome, int]
    draft_outcome_percentages: dict[DraftOutcome, float] = Field(
        description="Percent of categorized stories (0–100), rounded to two decimals; zero if none."
    )
    average_invest_score: float | None
    average_confidence: float | None
    daily_events: list[DailyAnalytics]
