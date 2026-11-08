"""Request and response bodies for the API."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, field_validator, model_validator

from services.worker.objects import DraftStory, StoryReview
from services.worker.prompts.extract import CreativityLevel

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


class TrelloConnectionComplete(RequestBody):
    state: str = Field(min_length=32, max_length=128)
    token: SecretStr = Field(min_length=1, max_length=512)


class TrelloConnectionRead(BaseModel):
    connected: bool
    member_id: str | None = None
    username: str | None = None
    expires_at: datetime | None = None


class MeetingCreate(RequestBody):
    board_id: UUID
    title: str = Field(min_length=1, max_length=255)


class ProcessAccepted(BaseModel):
    meeting_id: UUID
    status: MeetingStatus
    status_url: str


class AutoImportOptions(RequestBody):
    confidence_threshold: float = Field(default=0.85, ge=0.5, le=1)
    list_id: str | None = Field(default=None, min_length=1, max_length=255)
    issue_type_id: str | None = Field(default=None, pattern=r"^[0-9]+$")
    additional_fields: dict[str, Any] = Field(default_factory=dict)
    evidence_base_url: HttpUrl

    @model_validator(mode="after")
    def validate_destination(self):
        if bool(self.list_id) == bool(self.issue_type_id):
            raise ValueError("Choose exactly one Trello list or Jira issue type")
        if {"project", "issuetype", "summary", "description", "labels"}.intersection(self.additional_fields):
            raise ValueError("Additional fields cannot replace core export fields")
        if self.evidence_base_url.username or self.evidence_base_url.password or self.evidence_base_url.query or self.evidence_base_url.fragment:
            raise ValueError("Evidence base URL must not include credentials, a query, or a fragment")
        return self


class AutoImportStatus(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    meeting_id: UUID
    status: Literal["pending", "running", "completed", "failed"]
    imported: int
    skipped: int
    failed: int
    error: str | None
    updated_at: datetime


class ProcessOptions(RequestBody):
    creativity: CreativityLevel | None = None
    auto_import: AutoImportOptions | None = None


class StoryEdit(RequestBody):
    draft: DraftStory


class StoryDecision(RequestBody):
    decision: Literal["accept", "reject", "restore"]


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


class JiraConnectionComplete(RequestBody):
    state: str = Field(min_length=32, max_length=128)
    code: SecretStr = Field(min_length=1, max_length=4096)


class JiraConnectionRead(BaseModel):
    connected: bool
    account_id: str | None = None
    display_name: str | None = None
    expires_at: datetime | None = None


class JiraSiteRead(BaseModel):
    id: str
    name: str
    url: str


class JiraProjectRead(BaseModel):
    id: str
    key: str
    name: str


class JiraProjectPage(BaseModel):
    values: list[JiraProjectRead]
    startAt: int = 0
    maxResults: int = 50
    total: int | None = None
    isLast: bool = False


class JiraIssueTypeRead(BaseModel):
    id: str
    name: str
    subtask: bool = False


class JiraIssueTypePage(BaseModel):
    issueTypes: list[JiraIssueTypeRead]
    startAt: int = 0
    maxResults: int = 50
    total: int | None = None


class JiraImportCreate(RequestBody):
    story_id: UUID
    issue_type_id: str = Field(pattern=r"^[0-9]+$")
    evidence_url: HttpUrl
    additional_fields: dict[str, Any] = Field(default_factory=dict)

    @field_validator("additional_fields")
    @classmethod
    def protect_export_fields(cls, fields):
        protected = {"project", "issuetype", "summary", "description", "labels"}
        if protected.intersection(fields):
            raise ValueError("Additional fields cannot replace the export's core fields")
        return fields


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


class AttentionItem(BaseModel):
    story_id: UUID
    title: str
    reason: str


class BoardAnalytics(BaseModel):
    board_id: UUID
    story_counts: dict[str, int]
    invest_issues: dict[str, int] = Field(
        default_factory=dict,
        description="Stories flagged per INVEST criterion (lowercase, plus 'grounded'); rejected stories excluded.",
    )
    needs_attention: list[AttentionItem] = Field(
        default_factory=list,
        description="Newest pending stories (draft/needs_changes) with low confidence or INVEST score, at most 20.",
    )
    acceptance_rate: float | None
    draft_outcome_counts: dict[DraftOutcome, int]
    draft_outcome_percentages: dict[DraftOutcome, float] = Field(
        description="Percent of categorized stories (0–100), rounded to two decimals; zero if none."
    )
    average_invest_score: float | None
    average_confidence: float | None
    daily_events: list[DailyAnalytics]
