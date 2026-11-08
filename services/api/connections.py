"""Connect and disconnect the current browser's Trello account."""

import secrets
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from .dependencies import SessionDep
from .schemas import TrelloConnectionComplete, TrelloConnectionRead
from .trello_auth import (
    COOKIE_NAME, COOKIE_PATH, TOKEN_LIFETIME, browser_connection, callback_url,
    connection_is_active, finish_connection, start_connection,
)

router = APIRouter(prefix="/api/integrations/trello", tags=["Trello connection"])


def connection_status(connection) -> TrelloConnectionRead:
    if not connection_is_active(connection):
        return TrelloConnectionRead(connected=False)
    return TrelloConnectionRead(
        connected=True, member_id=connection.member_id,
        username=connection.username, expires_at=connection.expires_at,
    )


@router.get("/connect")
def connect_trello(request: Request, session: SessionDep):
    """Open this URL in a browser or popup to authorize Trello."""
    url, cookie = start_connection(request, session)
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(
        COOKIE_NAME, cookie, path=COOKIE_PATH,
        max_age=int(TOKEN_LIFETIME.total_seconds()), httponly=True,
        secure=urlsplit(callback_url()).scheme == "https", samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/callback", response_class=HTMLResponse, include_in_schema=False)
def trello_callback():
    # Trello returns its token in the fragment, which the HTTP server cannot read.
    # This same-origin page immediately removes it from the URL and submits it.
    nonce = secrets.token_urlsafe(24)
    page = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Connect Trello</title></head>
<body>
<h1>Connect Trello</h1>
<p id="message">Finishing your Trello connection...</p>
<script nonce="NONCE">
(async () => {
    const state = new URLSearchParams(window.location.search).get('state');
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    const token = fragment.get('token');
    window.history.replaceState(null, '', window.location.pathname);
    const message = document.getElementById('message');
    if (!state || !token || fragment.has('error')) {
        message.textContent = 'Trello connection was cancelled. Return to Backlogger to try again.';
        return;
    }
    try {
        const response = await fetch('/api/integrations/trello/connection', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({state, token})
        });
        if (!response.ok) {
            const failures = {
                400: 'The connection session is missing, expired, or replaced by another attempt. Close this tab, confirm the extension API URL matches the origin of this page, and start Connect Trello again.',
                422: 'Trello returned an invalid connection response. Start Connect Trello again.',
                502: 'The backend could not validate your account with Trello. Check the backend API key and Trello connectivity.',
                503: 'The backend configuration or database is unavailable. Check the backend logs and environment settings.',
                504: 'Trello did not respond in time. Start Connect Trello again.'
            };
            message.textContent = `Could not connect Trello (HTTP ${response.status}). ` +
                (failures[response.status] || 'Check the backend logs, then start Connect Trello again.');
            return;
        }
        message.textContent = 'Trello connected. You can close this page and return to Backlogger.';
    } catch {
        message.textContent = 'Could not reach the backend to finish connecting Trello. Check that the local backend is running, then start Connect Trello again.';
    }
})();
</script>
</body></html>""".replace("NONCE", nonce)
    return HTMLResponse(page, headers={
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": (
            f"default-src 'none'; script-src 'nonce-{nonce}'; connect-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        ),
    })


@router.post("/connection", response_model=TrelloConnectionRead)
def complete_trello_connection(
    body: TrelloConnectionComplete, request: Request, session: SessionDep, response: Response,
):
    connection = finish_connection(request, session, body.state, body.token.get_secret_value())
    response.headers["Cache-Control"] = "no-store"
    return connection_status(connection)


@router.get("/connection", response_model=TrelloConnectionRead)
def get_trello_connection(request: Request, session: SessionDep, response: Response):
    """Return locally saved connection metadata; never return a token."""
    response.headers["Cache-Control"] = "no-store"
    return connection_status(browser_connection(request, session))


@router.delete("/connection", response_model=TrelloConnectionRead)
def disconnect_trello(request: Request, session: SessionDep, response: Response):
    """Forget this browser's connection; users can revoke access in Trello too."""
    connection = browser_connection(request, session, lock=True)
    if connection is not None:
        session.delete(connection)
        session.commit()
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH)
    response.headers["Cache-Control"] = "no-store"
    return TrelloConnectionRead(connected=False)
