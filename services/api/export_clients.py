"""Choose the connected account for a registered board's provider."""

from uuid import UUID

from fastapi import HTTPException, Request

from services.models import Board

from .dependencies import SessionDep, get_record
from .jira_auth import get_jira_client
from .trello_auth import get_trello_client


def get_export_client(board_id: UUID, request: Request, session: SessionDep):
    board = get_record(session, Board, board_id)
    if board.provider == "trello":
        return get_trello_client(request, session)
    if board.provider == "jira":
        return get_jira_client(request, session)
    raise HTTPException(status_code=409, detail="Exports support Trello and Jira Cloud")
