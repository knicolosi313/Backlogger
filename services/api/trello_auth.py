"""Browser-scoped Trello authorization and encrypted token storage."""

import hashlib
import hmac
import os
import secrets
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from fastapi import HTTPException, Request
from sqlmodel import Session, select

from adapters.trello.client import TrelloClient
from services.models import TrelloConnection, utc_now

from .dependencies import SessionDep

COOKIE_NAME = "backlogger_trello_session"
COOKIE_PATH = "/api"
STATE_LIFETIME = timedelta(minutes=10)
TOKEN_LIFETIME = timedelta(days=30)


def load_settings() -> None:
    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env", override=False)


def trello_api_key() -> str:
    load_settings()
    key = os.getenv("TRELLO_API_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="Configure TRELLO_API_KEY")
    return key


def token_cipher() -> Fernet:
    load_settings()
    key = os.getenv("TOKEN_ENCRYPTION_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="Configure TOKEN_ENCRYPTION_KEY")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise HTTPException(status_code=503, detail="TOKEN_ENCRYPTION_KEY must be a Fernet key") from None


def callback_url() -> str:
    load_settings()
    url = os.getenv("TRELLO_OAUTH_CALLBACK_URL") or "http://127.0.0.1:8000/api/integrations/trello/callback"
    parsed = urlsplit(url)
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    if not parsed.hostname or not (parsed.scheme == "https" or local_http):
        raise HTTPException(status_code=503, detail="Use HTTPS for the Trello callback, or HTTP on localhost")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise HTTPException(status_code=503, detail="Trello callback must not contain credentials, a query, or a fragment")
    return url


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def browser_connection(
    request: Request, session: Session, *, lock: bool = False,
) -> TrelloConnection | None:
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie or len(cookie) > 128:
        return None
    query = select(TrelloConnection).where(TrelloConnection.session_hash == hash_secret(cookie))
    if lock:
        query = query.with_for_update()
    return session.exec(query).first()


def start_connection(request: Request, session: Session) -> tuple[str, str]:
    api_key = trello_api_key()
    token_cipher()  # Check configuration before sending the user to Trello.
    callback = callback_url()
    connection = browser_connection(request, session, lock=True)
    cookie = request.cookies.get(COOKIE_NAME)
    if connection is None:
        # Never reuse an unrecognized cookie: the server chooses session IDs.
        cookie = secrets.token_urlsafe(32)
        connection = TrelloConnection(session_hash=hash_secret(cookie))
    state = secrets.token_urlsafe(32)
    connection.state_hash = hash_secret(state)
    connection.state_expires_at = utc_now() + STATE_LIFETIME
    session.add(connection)
    session.commit()
    authorize_url = "https://trello.com/1/authorize?" + urlencode({
        "key": api_key,
        "name": "Backlogger",
        "scope": "read,write",
        "expiration": "30days",
        "response_type": "token",
        "callback_method": "fragment",
        "return_url": callback + "?" + urlencode({"state": state}),
    })
    return authorize_url, cookie


def finish_connection(request: Request, session: Session, state: str, token: str):
    connection = browser_connection(request, session, lock=True)
    now = utc_now()
    if (
        connection is None or connection.state_hash is None
        or connection.state_expires_at is None or connection.state_expires_at <= now
        or not hmac.compare_digest(connection.state_hash, hash_secret(state))
    ):
        raise HTTPException(status_code=400, detail="Connection request is invalid or expired; start again")

    client = TrelloClient(trello_api_key(), token)
    member = client.get_current_member()
    # Conservatively count the token lifetime from the start of authorization.
    connection.expires_at = connection.state_expires_at - STATE_LIFETIME + TOKEN_LIFETIME
    connection.encrypted_token = token_cipher().encrypt(token.encode()).decode()
    connection.member_id = member["id"]
    connection.username = member["username"]
    connection.connected_at = now
    connection.state_hash = None
    connection.state_expires_at = None
    session.commit()
    session.refresh(connection)
    return connection


def connection_is_active(connection: TrelloConnection | None) -> bool:
    return bool(
        connection is not None and connection.encrypted_token
        and connection.expires_at is not None and connection.expires_at > utc_now()
    )


def get_trello_client(request: Request, session: SessionDep) -> TrelloClient:
    api_key = trello_api_key()
    connection = browser_connection(request, session)
    if not connection_is_active(connection):
        raise HTTPException(status_code=401, detail="Connect your Trello account first")
    try:
        token = token_cipher().decrypt(connection.encrypted_token.encode()).decode()
    except InvalidToken:
        raise HTTPException(status_code=503, detail="Stored Trello token cannot be decrypted") from None
    return TrelloClient(api_key, token)
