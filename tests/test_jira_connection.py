"""Jira OAuth state, encrypted tokens, browser isolation, and refresh rotation."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
import requests
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from adapters.jira.client import JiraClient
from services.api import jira_auth
from services.api.server import app
from services.db import get_session
from services.models import JiraConnection, utc_now


@pytest.fixture
def jira_connection(database_connection, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("JIRA_CLIENT_ID", "synthetic-client")
    monkeypatch.setenv("JIRA_CLIENT_SECRET", "synthetic-secret")
    monkeypatch.setenv("JIRA_OAUTH_CALLBACK_URL", "http://127.0.0.1:8000/api/integrations/jira/callback")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setattr(jira_auth, "load_settings", lambda: None)
    exchange = Mock(return_value={"access_token": "access-one", "refresh_token": "refresh-one", "expires_in": 3600})
    monkeypatch.setattr(jira_auth, "exchange_tokens", exchange)
    cloud_id = str(uuid4())
    clients = {}

    def factory(token):
        if token not in clients:
            client = Mock(spec=JiraClient)
            client.get_current_member.return_value = {"account_id": token.replace("access", "account"), "name": "Test User"}
            client.list_sites.return_value = [{"id": cloud_id, "name": "Demo", "url": "https://demo.atlassian.net"}]
            client.list_projects.return_value = {"values": [{"id": "10001", "key": "DEMO", "name": "Demo"}]}
            client.list_issue_types.return_value = {"issueTypes": [{"id": "10002", "name": "Story"}]}
            client.get_create_fields.return_value = {"fields": [], "total": 0}
            clients[token] = client
        return clients[token]

    monkeypatch.setattr(jira_auth, "JiraClient", factory)

    def session_factory():
        return Session(bind=database_connection, join_transaction_mode="create_savepoint")

    def dependency():
        with session_factory() as session:
            yield session

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = dependency
    try:
        with TestClient(app) as browser:
            yield SimpleNamespace(browser=browser, exchange=exchange, clients=clients, key=key, session=session_factory, cloud_id=cloud_id)
    finally:
        app.dependency_overrides = previous


def begin(browser):
    response = browser.get("/api/integrations/jira/connect", follow_redirects=False)
    assert response.status_code == 303, response.text
    options = parse_qs(urlsplit(response.headers["location"]).query)
    assert options["response_type"] == ["code"]
    assert "offline_access" in options["scope"][0]
    assert "read:me" in options["scope"][0]
    assert "synthetic-secret" not in response.headers["location"]
    assert "httponly" in response.headers["set-cookie"].lower()
    return options["state"][0]


def finish(browser, state):
    return browser.post("/api/integrations/jira/connection", json={"state": state, "code": "synthetic-code"})


def test_connect_discover_and_store_encrypted_tokens(jira_connection):
    case = jira_connection
    state = begin(case.browser)
    response = finish(case.browser, state)
    assert response.status_code == 200, response.text
    assert response.json()["connected"] is True
    assert "access-one" not in response.text and "refresh-one" not in response.text
    with case.session() as session:
        cookie = case.browser.cookies.get(jira_auth.COOKIE_NAME)
        record = session.exec(select(JiraConnection).where(JiraConnection.session_hash == jira_auth.hash_secret(cookie))).one()
        assert record.state_hash is None
        cipher = Fernet(case.key.encode())
        assert cipher.decrypt(record.encrypted_token.encode()) == b"access-one"
        assert cipher.decrypt(record.encrypted_refresh_token.encode()) == b"refresh-one"
    assert case.browser.get("/api/integrations/jira/sites").status_code == 200
    base = f"/api/integrations/jira/sites/{case.cloud_id}/projects"
    assert case.browser.get(base).json()["values"][0]["key"] == "DEMO"
    assert case.browser.get(base + "/10001/issue-types").status_code == 200
    assert case.browser.get(base + "/10001/issue-types/10002/fields").json()["fields"] == []
    assert case.browser.get(f"/api/integrations/jira/sites/{uuid4()}/projects").status_code == 404


def test_state_is_browser_bound_single_use_and_expires(jira_connection):
    case = jira_connection
    state = begin(case.browser)
    with TestClient(app) as other:
        assert finish(other, state).status_code == 400
    assert finish(case.browser, "x" * 43).status_code == 400
    assert finish(case.browser, state).status_code == 200
    assert finish(case.browser, state).status_code == 400
    state = begin(case.browser)
    with case.session() as session:
        record = jira_auth.browser_connection(
            SimpleNamespace(cookies={jira_auth.COOKIE_NAME: case.browser.cookies.get(jira_auth.COOKIE_NAME)}), session,
        )
        record.state_expires_at = utc_now() - timedelta(seconds=1)
        session.commit()
    assert finish(case.browser, state).status_code == 400


def test_expired_access_token_refreshes_and_rotates_refresh_token(jira_connection):
    case = jira_connection
    assert finish(case.browser, begin(case.browser)).status_code == 200
    cookie = case.browser.cookies.get(jira_auth.COOKIE_NAME)
    with case.session() as session:
        record = session.exec(select(JiraConnection).where(JiraConnection.session_hash == jira_auth.hash_secret(cookie))).one()
        record.expires_at = utc_now() - timedelta(seconds=1)
        session.commit()
    case.exchange.return_value = {"access_token": "access-two", "refresh_token": "refresh-two", "expires_in": 3600}
    assert case.browser.get("/api/integrations/jira/sites").status_code == 200
    assert case.exchange.call_args.args[0]["grant_type"] == "refresh_token"
    assert case.exchange.call_args.args[0]["refresh_token"] == "refresh-one"
    assert "access-two" in case.clients
    with case.session() as session:
        record = session.exec(select(JiraConnection).where(JiraConnection.session_hash == jira_auth.hash_secret(cookie))).one()
        assert Fernet(case.key.encode()).decrypt(record.encrypted_refresh_token.encode()) == b"refresh-two"


def test_disconnect_and_browser_isolation(jira_connection):
    case = jira_connection
    assert finish(case.browser, begin(case.browser)).status_code == 200
    with TestClient(app) as other:
        assert other.get("/api/integrations/jira/sites").status_code == 401
        case.exchange.return_value = {"access_token": "access-two", "refresh_token": "refresh-two", "expires_in": 3600}
        assert finish(other, begin(other)).status_code == 200
        assert case.browser.delete("/api/integrations/jira/connection").json()["connected"] is False
        assert case.browser.get("/api/integrations/jira/sites").status_code == 401
        assert other.get("/api/integrations/jira/sites").status_code == 200


def test_invalid_refresh_grant_requires_reconnection(jira_connection):
    case = jira_connection
    assert finish(case.browser, begin(case.browser)).status_code == 200
    cookie = case.browser.cookies.get(jira_auth.COOKIE_NAME)
    with case.session() as session:
        record = session.exec(select(JiraConnection).where(JiraConnection.session_hash == jira_auth.hash_secret(cookie))).one()
        record.expires_at = utc_now() - timedelta(seconds=1)
        session.commit()
    response = requests.Response()
    response.status_code = 403
    case.exchange.side_effect = requests.HTTPError("refresh_token=PRIVATE", response=response)
    result = case.browser.get("/api/integrations/jira/sites")
    assert result.status_code == 401
    assert "PRIVATE" not in result.text
    assert case.browser.get("/api/integrations/jira/connection").json()["connected"] is False


def test_oauth_exchange_failure_does_not_connect_or_leak_secrets(jira_connection):
    case = jira_connection
    state = begin(case.browser)
    case.exchange.side_effect = requests.HTTPError("client_secret=PRIVATE")
    result = finish(case.browser, state)
    assert result.status_code == 502
    assert "PRIVATE" not in result.text
    assert case.browser.get("/api/integrations/jira/connection").json()["connected"] is False


def test_callback_does_not_echo_codes_and_validation_hides_input():
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = lambda: Mock()
    try:
        with TestClient(app) as browser:
            page = browser.get("/api/integrations/jira/callback?code=PRIVATE")
            assert "PRIVATE" not in page.text
            assert "history.replaceState" in page.text
            assert "nonce-" in page.headers["content-security-policy"]
            response = browser.post("/api/integrations/jira/connection", json={"state": "x" * 43, "code": "PRIVATE" * 1000})
            assert response.status_code == 422
            assert "PRIVATE" not in response.text
    finally:
        app.dependency_overrides = previous
