"""SQLModel mappings for the numbered SQL migrations in infra/db.

Run SQL migrations rather than metadata.create_all(): they also manage TimescaleDB
and database constraints. Replace JSON dictionaries to persist edits; in-place
mutations inside nested JSON are not tracked by the ORM.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Double, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Record(SQLModel):
    """Fields shared by most tables: an ID and a creation time."""

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        sa_column_kwargs={"server_default": text("gen_random_uuid()")},
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": text("now()")},
    )


class Board(Record, table=True):
    __tablename__ = "boards"
    __table_args__ = (
        UniqueConstraint("provider", "external_id"),
        {"schema": "backlogger"},
    )

    provider: str = Field(sa_type=Text)
    external_id: str = Field(sa_type=Text)
    name: str = Field(sa_type=Text)


class Meeting(Record, table=True):
    __tablename__ = "meetings"
    __table_args__ = {"schema": "backlogger"}

    board_id: UUID = Field(foreign_key="backlogger.boards.id")
    title: str = Field(sa_type=Text)
    media_key: str | None = Field(default=None, sa_type=Text)
    status: str = Field(default="uploaded", sa_type=Text)


class PipelineOutput(Record, table=True):
    __tablename__ = "pipeline_outputs"
    __table_args__ = (
        UniqueConstraint("meeting_id", "run_id", "stage"),
        {"schema": "backlogger"},
    )

    meeting_id: UUID = Field(foreign_key="backlogger.meetings.id")
    run_id: UUID
    stage: str = Field(sa_type=Text)
    model: str = Field(sa_type=Text)
    output: dict[str, Any] = Field(sa_type=JSONB)


class Story(Record, table=True):
    __tablename__ = "stories"
    __table_args__ = {"schema": "backlogger"}

    meeting_id: UUID = Field(foreign_key="backlogger.meetings.id")
    draft: dict[str, Any] = Field(sa_type=JSONB)
    status: str = Field(default="draft", sa_type=Text)
    confidence: float | None = Field(default=None, sa_type=Double)
    invest_score: int | None = None
    # An absent critique must be SQL NULL, not the JSON value null.
    critique: dict[str, Any] | None = Field(
        default=None,
        sa_type=JSONB(none_as_null=True),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": text("now()"), "onupdate": utc_now},
    )


class StoryRevision(Record, table=True):
    __tablename__ = "story_revisions"
    __table_args__ = (
        UniqueConstraint("story_id", "revision"),
        {"schema": "backlogger"},
    )

    story_id: UUID = Field(foreign_key="backlogger.stories.id")
    revision: int
    draft: dict[str, Any] = Field(sa_type=JSONB)
    editor: str = Field(sa_type=Text)


class Import(Record, table=True):
    __tablename__ = "imports"
    __table_args__ = {"schema": "backlogger"}

    story_id: UUID = Field(foreign_key="backlogger.stories.id", unique=True)
    idempotency_key: str = Field(sa_type=Text, unique=True)
    external_card_id: str | None = Field(default=None, sa_type=Text)
    status: str = Field(default="pending", sa_type=Text)


class AnalyticsEvent(SQLModel, table=True):
    __tablename__ = "analytics_events"
    __table_args__ = {"schema": "backlogger"}

    occurred_at: datetime = Field(
        default_factory=utc_now,
        primary_key=True,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": text("now()")},
    )
    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        sa_column_kwargs={"server_default": text("gen_random_uuid()")},
    )
    board_id: UUID = Field(foreign_key="backlogger.boards.id")
    story_id: UUID | None = Field(default=None, foreign_key="backlogger.stories.id")
    event_type: str = Field(sa_type=Text)
    confidence: float | None = Field(default=None, sa_type=Double)
    invest_score: int | None = None
    properties: dict[str, Any] = Field(
        default_factory=dict,
        sa_type=JSONB,
        sa_column_kwargs={"server_default": text("'{}'::jsonb")},
    )


class TrelloConnection(Record, table=True):
    """One browser's encrypted Trello token and short-lived connection challenge."""

    __tablename__ = "trello_connections"
    __table_args__ = {"schema": "backlogger"}

    session_hash: str = Field(sa_type=Text, unique=True)
    encrypted_token: str | None = Field(default=None, sa_type=Text)
    member_id: str | None = Field(default=None, sa_type=Text)
    username: str | None = Field(default=None, sa_type=Text)
    connected_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    expires_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    state_hash: str | None = Field(default=None, sa_type=Text)
    state_expires_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class JiraConnection(Record, table=True):
    """A browser's encrypted Atlassian access/refresh tokens and OAuth state."""

    __tablename__ = "jira_connections"
    __table_args__ = {"schema": "backlogger"}

    session_hash: str = Field(sa_type=Text, unique=True)
    encrypted_token: str | None = Field(default=None, sa_type=Text)
    encrypted_refresh_token: str | None = Field(default=None, sa_type=Text)
    account_id: str | None = Field(default=None, sa_type=Text)
    display_name: str | None = Field(default=None, sa_type=Text)
    expires_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    state_hash: str | None = Field(default=None, sa_type=Text)
    state_expires_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class SchemaMigration(SQLModel, table=True):
    __tablename__ = "schema_migrations"
    __table_args__ = {"schema": "backlogger"}

    version: str = Field(primary_key=True, sa_type=Text)
    applied_at: datetime = Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": text("now()")},
    )
