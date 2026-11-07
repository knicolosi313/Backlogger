"""Database configuration and session lifetime, without a real connection."""

from unittest.mock import Mock

import pytest

from services import db


@pytest.fixture
def engine_factory(monkeypatch):
    # Never read the user's real .env or leave a fake engine in the process cache.
    db.get_engine.cache_clear()
    monkeypatch.setattr(db, "load_dotenv", Mock())
    factory = Mock()
    monkeypatch.setattr(db, "create_engine", factory)
    yield factory
    db.get_engine.cache_clear()


def test_engine_is_cached_and_preserves_connection_options(monkeypatch, engine_factory):
    monkeypatch.setenv("DATABASE_URL", "postgres://user:synthetic@localhost/tsdb?sslmode=require")

    first = db.get_engine()
    second = db.get_engine()

    assert first is second
    engine_factory.assert_called_once()
    url = engine_factory.call_args.args[0]
    assert url.drivername == "postgresql+psycopg"
    assert url.query["sslmode"] == "require"
    assert engine_factory.call_args.kwargs["pool_pre_ping"] is True
    assert engine_factory.call_args.kwargs["hide_parameters"] is True
    assert db.load_dotenv.call_args.kwargs["override"] is False


def test_missing_database_url_has_a_clear_error(monkeypatch, engine_factory):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="Set DATABASE_URL"):
        db.get_engine()

    engine_factory.assert_not_called()


@pytest.mark.parametrize("url", ["sqlite:///local.db", "mysql://localhost/db", "not a URL"])
def test_unsupported_database_urls_are_rejected(url):
    with pytest.raises(ValueError, match="PostgreSQL"):
        db.database_url(url)


@pytest.mark.parametrize("fail", [False, True], ids=["normal-exit", "request-failure"])
def test_session_dependency_closes_without_implicitly_committing(monkeypatch, fail):
    engine = object()
    session = Mock()
    context = Mock()
    context.__enter__ = Mock(return_value=session)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    factory = Mock(return_value=context)
    monkeypatch.setattr(db, "Session", factory)

    dependency = db.get_session()
    assert next(dependency) is session
    if fail:
        with pytest.raises(RuntimeError, match="Request failed"):
            dependency.throw(RuntimeError("Request failed"))
    else:
        dependency.close()

    factory.assert_called_once_with(engine)
    context.__exit__.assert_called_once()
    session.commit.assert_not_called()
