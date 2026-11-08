"""Jira Cloud OAuth 2.0 authorization and rotating refresh tokens."""

import hmac
import os
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

import requests
from cryptography.fernet import InvalidToken
from fastapi import HTTPException, Request
from sqlmodel import Session, select

from adapters.jira.client import JiraClient
from services.models import JiraConnection, utc_now

from .dependencies import SessionDep
from .trello_auth import hash_secret, load_settings, token_cipher

COOKIE_NAME = "backlogger_jira_session"
STATE_LIFETIME = timedelta(minutes=10)
SCOPES = "read:jira-work write:jira-work read:me offline_access"


def configuration():
    load_settings()
    client_id = os.getenv("JIRA_CLIENT_ID")
    secret = os.getenv("JIRA_CLIENT_SECRET")
    callback = os.getenv("JIRA_OAUTH_CALLBACK_URL") or "http://127.0.0.1:8000/api/integrations/jira/callback"
    if not client_id or not secret:
        raise HTTPException(status_code=503, detail="Configure JIRA_CLIENT_ID and JIRA_CLIENT_SECRET")
    parsed = urlsplit(callback)
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    if not parsed.hostname or not (parsed.scheme == "https" or local_http) or any([
        parsed.query, parsed.fragment, parsed.username, parsed.password,
    ]):
        raise HTTPException(status_code=503, detail="Invalid Jira OAuth callback URL")
    token_cipher()
    return client_id, secret, callback


def browser_connection(request: Request, session: Session, *, lock=False):
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie or len(cookie) > 128:
        return None
    query = select(JiraConnection).where(JiraConnection.session_hash == hash_secret(cookie))
    if lock:
        query = query.with_for_update()
    return session.exec(query).first()


def start_connection(request: Request, session: Session):
    client_id, _, callback = configuration()
    record = browser_connection(request, session, lock=True)
    cookie = request.cookies.get(COOKIE_NAME)
    if record is None:
        cookie = secrets.token_urlsafe(32)
        record = JiraConnection(session_hash=hash_secret(cookie))
    state = secrets.token_urlsafe(32)
    record.state_hash = hash_secret(state)
    record.state_expires_at = utc_now() + STATE_LIFETIME
    session.add(record)
    session.commit()
    return "https://auth.atlassian.com/authorize?" + urlencode({
        "audience": "api.atlassian.com", "client_id": client_id, "scope": SCOPES,
        "redirect_uri": callback, "state": state, "response_type": "code", "prompt": "consent",
    }), cookie


def exchange_tokens(payload):
    response = requests.post("https://auth.atlassian.com/oauth/token", json=payload, timeout=10)
    response.raise_for_status()
    return response.json()


def save_tokens(record: JiraConnection, tokens: dict):
    cipher = token_cipher()
    record.encrypted_token = cipher.encrypt(tokens["access_token"].encode()).decode()
    if tokens.get("refresh_token"):
        record.encrypted_refresh_token = cipher.encrypt(tokens["refresh_token"].encode()).decode()
    record.expires_at = utc_now() + timedelta(seconds=int(tokens["expires_in"]))


def finish_connection(request: Request, session: Session, state: str, code: str):
    client_id, secret, callback = configuration()
    record = browser_connection(request, session, lock=True)
    if (
        record is None or record.state_hash is None or record.state_expires_at is None
        or record.state_expires_at <= utc_now()
        or not hmac.compare_digest(record.state_hash, hash_secret(state))
    ):
        raise HTTPException(status_code=400, detail="Jira connection request is invalid or expired")
    tokens = exchange_tokens({
        "grant_type": "authorization_code", "client_id": client_id, "client_secret": secret,
        "code": code, "redirect_uri": callback,
    })
    member = JiraClient(tokens["access_token"]).get_current_member()
    # Do not retain an old account's refresh token when replacing its connection.
    record.encrypted_refresh_token = None
    save_tokens(record, tokens)
    record.account_id = member["account_id"]
    record.display_name = member["name"]
    record.state_hash = None
    record.state_expires_at = None
    session.commit()
    session.refresh(record)
    return record


def get_jira_client(request: Request, session: SessionDep) -> JiraClient:
    return client_for_connection(browser_connection(request, session, lock=True), session)


def client_for_connection(record: JiraConnection | None, session: Session) -> JiraClient:
    """Resolve/refresh a saved account at export time, after meeting processing."""
    client_id, secret, _ = configuration()
    if record is None or not record.encrypted_token:
        raise HTTPException(status_code=401, detail="Connect your Jira account first")
    cipher = token_cipher()
    try:
        if record.expires_at is None or record.expires_at <= utc_now() + timedelta(seconds=60):
            if not record.encrypted_refresh_token:
                raise HTTPException(status_code=401, detail="Reconnect your Jira account")
            refresh_token = cipher.decrypt(record.encrypted_refresh_token.encode()).decode()
            try:
                tokens = exchange_tokens({
                    "grant_type": "refresh_token", "client_id": client_id,
                    "client_secret": secret, "refresh_token": refresh_token,
                })
            except requests.HTTPError as error:
                if error.response is not None and error.response.status_code in {400, 401, 403}:
                    record.encrypted_token = None
                    record.encrypted_refresh_token = None
                    session.commit()
                    raise HTTPException(status_code=401, detail="Reconnect your Jira account") from None
                raise
            save_tokens(record, tokens)
            # Persist rotated refresh tokens before any subsequent Jira request.
            session.commit()
        token = cipher.decrypt(record.encrypted_token.encode()).decode()
    except InvalidToken:
        raise HTTPException(status_code=503, detail="Stored Jira token cannot be decrypted") from None
    return JiraClient(token)
