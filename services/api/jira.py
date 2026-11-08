"""Browser-scoped Jira Cloud connections and export-destination discovery."""

import secrets
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from adapters.jira.client import JiraClient

from . import jira_auth
from .dependencies import SessionDep
from .schemas import (
    JiraConnectionComplete, JiraConnectionRead, JiraIssueTypePage, JiraProjectPage, JiraSiteRead,
)

router = APIRouter(prefix="/api/integrations/jira", tags=["Jira Cloud"])
JiraClientDep = Annotated[JiraClient, Depends(jira_auth.get_jira_client)]
ProjectId = Annotated[str, Path(pattern=r"^[0-9]+$")]


def connection_status(record):
    connected = bool(record and record.encrypted_token and (
        record.encrypted_refresh_token or (record.expires_at and record.expires_at > jira_auth.utc_now())
    ))
    return JiraConnectionRead(
        connected=connected,
        account_id=record.account_id if connected else None,
        display_name=record.display_name if connected else None,
        expires_at=record.expires_at if connected else None,
    )


@router.get("/connect")
def connect_jira(request: Request, session: SessionDep):
    url, cookie = jira_auth.start_connection(request, session)
    response = RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})
    response.set_cookie(
        jira_auth.COOKIE_NAME, cookie, path="/api", max_age=30 * 24 * 60 * 60,
        httponly=True, samesite="lax",
        secure=urlsplit(jira_auth.configuration()[2]).scheme == "https",
    )
    return response


@router.get("/callback", response_class=HTMLResponse, include_in_schema=False)
def jira_callback():
    nonce = secrets.token_urlsafe(24)
    page = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Connect Jira</title></head>
<body><h1>Connect Jira</h1><p id="message">Finishing connection...</p>
<script nonce="NONCE">
(async () => {
  const params = new URLSearchParams(location.search);
  const state = params.get('state'), code = params.get('code');
  history.replaceState(null, '', location.pathname);
  const message = document.getElementById('message');
  if (!state || !code || params.has('error')) {
    message.textContent = 'Connection cancelled. Return to Backlogger to try again.'; return;
  }
  try {
    const response = await fetch('/api/integrations/jira/connection', {
      method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({state, code})
    });
    if (!response.ok) throw new Error('Connection failed');
    message.textContent = 'Jira connected. You can close this page and return to Backlogger.';
  } catch { message.textContent = 'Could not connect Jira. Return to Backlogger and try again.'; }
})();
</script></body></html>""".replace("NONCE", nonce)
    return HTMLResponse(page, headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
    })


@router.post("/connection", response_model=JiraConnectionRead)
def complete_jira(body: JiraConnectionComplete, request: Request, session: SessionDep, response: Response):
    record = jira_auth.finish_connection(request, session, body.state, body.code.get_secret_value())
    response.headers["Cache-Control"] = "no-store"
    return connection_status(record)


@router.get("/connection", response_model=JiraConnectionRead)
def get_connection(request: Request, session: SessionDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return connection_status(jira_auth.browser_connection(request, session))


@router.delete("/connection", response_model=JiraConnectionRead)
def disconnect(request: Request, session: SessionDep, response: Response):
    record = jira_auth.browser_connection(request, session, lock=True)
    if record is not None:
        session.delete(record)
        session.commit()
    response.delete_cookie(jira_auth.COOKIE_NAME, path="/api")
    response.headers["Cache-Control"] = "no-store"
    return JiraConnectionRead(connected=False)


def check_site(client: JiraClient, cloud_id: str):
    if not any(site["id"] == cloud_id for site in client.list_sites()):
        raise HTTPException(status_code=404, detail="Jira site is not accessible to this account")


@router.get("/sites", response_model=list[JiraSiteRead])
def list_sites(client: JiraClientDep):
    return client.list_sites()


@router.get("/sites/{cloud_id}/projects", response_model=JiraProjectPage)
def list_projects(cloud_id: UUID, client: JiraClientDep, start_at: int = Query(default=0, ge=0)):
    check_site(client, str(cloud_id))
    return client.list_projects(str(cloud_id), start_at)


@router.get("/sites/{cloud_id}/projects/{project_id}/issue-types", response_model=JiraIssueTypePage)
def list_issue_types(
    cloud_id: UUID, project_id: ProjectId, client: JiraClientDep,
    start_at: int = Query(default=0, ge=0),
):
    check_site(client, str(cloud_id))
    return client.list_issue_types(str(cloud_id), project_id, start_at)


@router.get("/sites/{cloud_id}/projects/{project_id}/issue-types/{issue_type_id}/fields")
def get_create_fields(
    cloud_id: UUID, project_id: ProjectId,
    issue_type_id: Annotated[str, Path(pattern=r"^[0-9]+$")], client: JiraClientDep,
    start_at: int = Query(default=0, ge=0),
):
    check_site(client, str(cloud_id))
    return client.get_create_fields(str(cloud_id), project_id, issue_type_id, start_at)
