"""Discover Trello destinations for the account connected in this browser."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from adapters.trello.client import TrelloClient

from .exports import get_trello_client
from .schemas import TrelloBoardRead, TrelloListRead

router = APIRouter(prefix="/api/integrations/trello", tags=["Trello"])
TrelloClientDep = Annotated[TrelloClient, Depends(get_trello_client)]


@router.get("/boards", response_model=list[TrelloBoardRead])
def list_trello_boards(client: TrelloClientDep):
    """List open boards accessible to this browser's connected Trello account."""
    boards = client.list_boards()
    return [board for board in boards if not board.get("closed", False)]


@router.get("/boards/{external_board_id}/lists", response_model=list[TrelloListRead])
def list_trello_lists(
    external_board_id: Annotated[
        str, Path(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9]+$"),
    ],
    client: TrelloClientDep,
):
    """List open destinations using a Trello board ID, before or after registration."""
    lists = client.list_lists(external_board_id)
    return [trello_list for trello_list in lists if not trello_list.get("closed", False)]
