"""Refresh an explicit snapshot of existing work using the connected account."""

from typing import Annotated
from uuid import UUID
import re

import requests
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select

from adapters.jira.client import JiraClient
from adapters.trello.client import TrelloClient
from services.models import Board, StoryContext
from services.worker.board_context import MAX_CANDIDATES

from .dependencies import SessionDep, get_record
from .export_clients import get_export_client

router = APIRouter(prefix="/api/boards", tags=["Board context"])


def adf_text(node) -> str:
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    return " ".join([node.get("text", "")] + [adf_text(child) for child in node.get("content", [])]).strip()


def fetch_context(board, client) -> dict[str, dict]:
    if board.provider == "trello":
        if not re.fullmatch(r"[A-Za-z0-9]+", board.external_id):
            raise HTTPException(409, "Expected a Trello board ID or short link ID")
        cards = client.list_context_cards(board.external_id)
        if len(cards) > MAX_CANDIDATES:
            raise HTTPException(409, "Board context sync currently supports up to 500 cards")
        checklists = {}
        for checklist in client.list_context_checklists(board.external_id):
            checklists.setdefault(checklist.get("idCard"), []).append(checklist)
        result = {}
        for card in cards:
            epics = [label["name"][5:].strip() for label in card.get("labels", [])
                     if label.get("name", "").lower().startswith("epic:")]
            description = card.get("desc", "")[:6000]
            stated_epic = re.search(r"^Epic:\s*(.+)$", description, re.MULTILINE | re.IGNORECASE)
            criteria, tasks = [], []
            for checklist in checklists.get(card["id"], []):
                items = [item["name"][:500] for item in checklist.get("checkItems", [])][:30]
                if checklist.get("name", "").lower() == "acceptance criteria":
                    criteria.extend(items)
                elif checklist.get("name", "").lower().startswith("solution tasks"):
                    tasks.extend(items)
            result[card["id"]] = {
                "title": card["name"][:300], "description": description,
                "acceptance_criteria": criteria[:30], "solution_tasks": tasks[:30],
                "epic": epics[0] if epics else stated_epic.group(1).strip() if stated_epic else None,
                "status": "archived" if card.get("closed") else "open",
            }
        return result
    try:
        cloud_id, project_id = board.external_id.split(":")
        cloud_id = str(UUID(cloud_id))
        if not project_id.isascii() or not project_id.isdigit():
            raise ValueError()
    except ValueError:
        raise HTTPException(409, "Jira external_id must be CLOUD_UUID:PROJECT_ID") from None
    issues, token, seen = [], None, set()
    for _ in range(6):
        page = client.list_context_issues(cloud_id, project_id, token)
        issues.extend(page.get("issues", []))
        token = page.get("nextPageToken")
        if page.get("isLast") is False and not token:
            raise HTTPException(409, "Project context sync returned an incomplete page")
        if len(issues) > MAX_CANDIDATES or token in seen:
            raise HTTPException(409, "Project context sync is incomplete or exceeds 500 issues")
        if not token:
            break
        seen.add(token)
    else:
        raise HTTPException(409, "Project context sync exceeds its page limit")
    epic_names = {issue["key"]: issue["fields"].get("summary") for issue in issues
                  if issue["fields"].get("issuetype", {}).get("hierarchyLevel") == 1
                  or issue["fields"].get("issuetype", {}).get("name", "").lower() == "epic"}
    result = {}
    for issue in issues:
        fields = issue["fields"]
        parent = fields.get("parent") or {}
        epic = epic_names.get(issue["key"]) or epic_names.get(parent.get("key"))
        parent_fields = parent.get("fields", {})
        parent_type = parent_fields.get("issuetype", {})
        if not epic and (parent_type.get("hierarchyLevel") == 1 or parent_type.get("name", "").lower() == "epic"):
            epic = parent_fields.get("summary")
        result[issue["key"]] = {
            "title": fields.get("summary", "")[:300], "description": adf_text(fields.get("description"))[:6000],
            "epic": epic, "status": (fields.get("status") or {}).get("name"),
        }
    return result


@router.post("/{board_id}/context/sync")
def sync_board_context(
    board_id: UUID, session: SessionDep,
    client: Annotated[TrelloClient | JiraClient, Depends(get_export_client)],
):
    """Refresh external context before processing a meeting; never writes to the provider."""
    board = get_record(session, Board, board_id, lock=True)
    try:
        payloads = fetch_context(board, client)
    except requests.RequestException:
        raise HTTPException(502, "Could not read board context from the connected provider") from None
    rows = session.exec(select(StoryContext).where(
        StoryContext.board_id == board_id, StoryContext.source == board.provider,
    )).all()
    existing = {row.external_id: row for row in rows}
    for external_id, payload in payloads.items():
        row = existing.pop(external_id, None)
        if row is None:
            row = StoryContext(board_id=board_id, source=board.provider, external_id=external_id, payload=payload)
            session.add(row)
        elif row.payload != payload:
            row.payload = payload
            row.embedding = None
            row.embedding_model = None
            row.content_hash = None
    for removed in existing.values():
        session.delete(removed)
    session.commit()
    return {"board_id": str(board_id), "synced_items": len(payloads)}
