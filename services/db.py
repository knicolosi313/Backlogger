"""Shared SQLModel engine and sessions. SQL migrations own the database schema."""

import os
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlmodel import Session, create_engine


def database_url(connection_string: str) -> URL:
    """Accept Tiger's postgres/postgresql URI and select the Psycopg 3 driver."""
    if connection_string.startswith("postgres://"):
        connection_string = connection_string.replace("postgres://", "postgresql://", 1)
    try:
        url = make_url(connection_string)
        # Force validation of the port without including credentials in errors.
        _ = url.port
    except (ArgumentError, ValueError):
        raise ValueError("DATABASE_URL must be a valid PostgreSQL connection URI") from None
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("DATABASE_URL must use PostgreSQL with Psycopg 3")
    return url.set(drivername="postgresql+psycopg")


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Create one pool per process, lazily; importing modules never connects."""
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env", override=False)

    connection_string = os.getenv("DATABASE_URL")
    if not connection_string:
        raise ValueError("Set DATABASE_URL in the environment or project-root .env")
    return create_engine(
        database_url(connection_string),
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 10},
    )


def get_session() -> Iterator[Session]:
    """FastAPI dependency; callers explicitly commit writes or use session.begin()."""
    with Session(get_engine()) as session:
        yield session
