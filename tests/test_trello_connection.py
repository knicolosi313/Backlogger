"""Browser-scoped Trello authorization, with fake Trello and rolled-back storage."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from adapters.trello.client import TrelloClient
from services.api import trello_auth
from services.api.server import app
from services.db import get_session
from services.models import TrelloConnection, utc_now


@pytest.fixture
def connection_case(database_connection, monkeypatch):
    encryption_key = Fernet.generate_key().decode()
    monkeypatch.setenv("TRELLO_API_KEY", "synthetic-app-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", encryption_key)
    monkeypatch.setenv("TRELLO_OAUTH_CALLBACK_URL", "http://127.0.0.1:8000/api/integrations/trello/callback")
    monkeypatch.setattr(trello_auth, "load_dotenv", lambda *args, **kwargs: None)

    def new_session():
        return Session(bind=database_connection, join_transaction_mode="create_savepoint")

    def session_dependency():
        with new_session() as session:
            yield session

    clients = {}

    def make_trello_client(api_key, token):
        if token not in clients:
            client = Mock(spec=TrelloClient)
            account = "one" if token == "synthetic-token-one" else "two"
            client.get_current_member.return_value = {"id": f"member-{account}", "username": f"user-{account}"}
            client.list_boards.return_value = [{"id": f"board{account}", "name": f"Board {account}"}]
            clients[token] = client
        return clients[token]

    factory = Mock(side_effect=make_trello_client)
    monkeypatch.setattr(trello_auth, "TrelloClient", factory)
    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = session_dependency
    try:
        with TestClient(app) as browser:
            yield SimpleNamespace(
                browser=browser, session=new_session, clients=clients,
                factory=factory, encryption_key=encryption_key,
            )
    finally:
        app.dependency_overrides = previous_overrides


def begin(browser):
    response = browser.get("/api/integrations/trello/connect", follow_redirects=False)
    assert response.status_code == 303, response.text
    authorize = urlsplit(response.headers["location"])
    assert authorize.netloc == "trello.com"
    options = parse_qs(authorize.query)
    assert options["scope"] == ["read,write"]
    assert options["expiration"] == ["30days"]
    assert options["callback_method"] == ["fragment"]
    callback = urlsplit(options["return_url"][0])
    return parse_qs(callback.query)["state"][0], response


def complete(browser, state, token="synthetic-token-one"):
    return browser.post("/api/integrations/trello/connection", json={"state": state, "token": token})


def test_connect_encrypts_token_and_uses_it_for_discovery(connection_case):
    case = connection_case
    assert case.browser.get("/api/integrations/trello/connection").json()["connected"] is False
    state, redirect = begin(case.browser)
    cookie_header = redirect.headers["set-cookie"].lower()
    assert "httponly" in cookie_header
    assert "samesite=lax" in cookie_header
    result = complete(case.browser, state)
    assert result.status_code == 200, result.text
    assert result.json()["username"] == "user-one"
    assert result.json()["connected"] is True
    assert "synthetic-token-one" not in result.text
    assert result.headers["cache-control"] == "no-store"

    cookie = case.browser.cookies.get(trello_auth.COOKIE_NAME)
    with case.session() as session:
        record = session.exec(select(TrelloConnection).where(
            TrelloConnection.session_hash == trello_auth.hash_secret(cookie),
        )).one()
        assert record.session_hash != cookie
        assert record.encrypted_token != "synthetic-token-one"
        assert Fernet(case.encryption_key.encode()).decrypt(record.encrypted_token.encode()).decode() == "synthetic-token-one"
        assert record.state_hash is None

    boards = case.browser.get("/api/integrations/trello/boards")
    assert boards.status_code == 200, boards.text
    assert boards.json()[0]["id"] == "boardone"
    assert case.factory.call_args.args == ("synthetic-app-key", "synthetic-token-one")


def test_state_is_bound_to_browser_and_can_only_be_used_once(connection_case):
    case = connection_case
    state, _ = begin(case.browser)
    with TestClient(app) as other_browser:
        assert complete(other_browser, state).status_code == 400
    assert complete(case.browser, "x" * 43).status_code == 400
    assert complete(case.browser, state).status_code == 200
    assert complete(case.browser, state).status_code == 400


def test_two_browsers_use_different_trello_accounts(connection_case):
    case = connection_case
    first_state, _ = begin(case.browser)
    assert complete(case.browser, first_state).status_code == 200
    with TestClient(app) as other_browser:
        assert other_browser.get("/api/integrations/trello/boards").status_code == 401
        second_state, _ = begin(other_browser)
        assert complete(other_browser, second_state, "synthetic-token-two").status_code == 200
        assert other_browser.get("/api/integrations/trello/boards").json()[0]["id"] == "boardtwo"
        assert case.browser.get("/api/integrations/trello/boards").json()[0]["id"] == "boardone"


def test_disconnect_forgets_token_and_does_not_affect_other_browser(connection_case):
    case = connection_case
    state, _ = begin(case.browser)
    assert complete(case.browser, state).status_code == 200
    old_cookie = case.browser.cookies.get(trello_auth.COOKIE_NAME)
    with TestClient(app) as other_browser:
        other_state, _ = begin(other_browser)
        assert complete(other_browser, other_state, "synthetic-token-two").status_code == 200
        assert case.browser.delete("/api/integrations/trello/connection").json() == {
            "connected": False, "member_id": None, "username": None, "expires_at": None,
        }
        assert case.browser.get("/api/integrations/trello/boards").status_code == 401
        assert other_browser.get("/api/integrations/trello/boards").status_code == 200
    with case.session() as session:
        assert session.exec(select(TrelloConnection).where(
            TrelloConnection.session_hash == trello_auth.hash_secret(old_cookie),
        )).first() is None


def test_expired_state_and_expired_connection_are_rejected(connection_case):
    case = connection_case
    state, _ = begin(case.browser)
    cookie = case.browser.cookies.get(trello_auth.COOKIE_NAME)
    with case.session() as session:
        record = session.exec(select(TrelloConnection).where(
            TrelloConnection.session_hash == trello_auth.hash_secret(cookie),
        )).one()
        record.state_expires_at = utc_now() - timedelta(seconds=1)
        session.commit()
    assert complete(case.browser, state).status_code == 400
    state, _ = begin(case.browser)
    assert complete(case.browser, state).status_code == 200
    with case.session() as session:
        record = session.exec(select(TrelloConnection).where(
            TrelloConnection.session_hash == trello_auth.hash_secret(cookie),
        )).one()
        record.expires_at = utc_now() - timedelta(seconds=1)
        session.commit()
    assert case.browser.get("/api/integrations/trello/boards").status_code == 401
    assert case.browser.get("/api/integrations/trello/connection").json()["connected"] is False


def test_invalid_trello_token_does_not_save_a_connection(connection_case):
    case = connection_case
    state, _ = begin(case.browser)
    case.factory.side_effect = requests.HTTPError("token=SECRET")
    response = complete(case.browser, state)
    assert response.status_code == 502
    assert "SECRET" not in response.text
    assert case.browser.get("/api/integrations/trello/connection").json()["connected"] is False


def test_unrecognized_cookie_is_replaced(connection_case):
    case = connection_case
    case.browser.cookies.set(trello_auth.COOKIE_NAME, "attacker-chosen-value")
    _, response = begin(case.browser)
    assert "attacker-chosen-value" not in response.headers["set-cookie"]


def test_callback_page_scrubs_fragment_and_does_not_echo_query_secrets():
    with TestClient(app) as browser:
        response = browser.get("/api/integrations/trello/callback?state=SECRET")
    assert response.status_code == 200
    assert "SECRET" not in response.text
    assert "history.replaceState" in response.text
    assert "credentials: 'same-origin'" in response.text
    assert "nonce-" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "no-referrer"


def test_connection_validation_errors_do_not_echo_tokens():
    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = lambda: Mock()
    try:
        with TestClient(app) as browser:
            response = complete(browser, "x" * 43, "SECRET" * 100)
        assert response.status_code == 422
        assert "SECRET" not in response.text
    finally:
        app.dependency_overrides = previous_overrides


def test_missing_encryption_key_blocks_connection_before_redirect(connection_case, monkeypatch):
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    response = connection_case.browser.get("/api/integrations/trello/connect", follow_redirects=False)
    assert response.status_code == 503


def test_wrong_encryption_key_does_not_expose_the_saved_token(connection_case, monkeypatch):
    case = connection_case
    state, _ = begin(case.browser)
    assert complete(case.browser, state).status_code == 200
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    response = case.browser.get("/api/integrations/trello/boards")
    assert response.status_code == 503
    assert "synthetic-token-one" not in response.text
