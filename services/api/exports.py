"""Coordinate accepted-story exports between PostgreSQL and Trello."""

from uuid import UUID

import requests
from fastapi import HTTPException
from pydantic import ValidationError
from sqlmodel import Session, select

from adapters.trello.client import TrelloClient
from services.models import AnalyticsEvent, Board, Import, Meeting, Story
from services.worker.objects import DraftStory

from .dependencies import get_record
from .schemas import ImportCreate, ImportRead
from .trello_auth import get_trello_client


def _get_ai_draft_label(client: TrelloClient, trello_board_id: str) -> str:
    """Reuse the board's AI Draft label, or create it if it is missing."""
    for label in client.list_labels(trello_board_id):
        if label["name"] == "AI Draft":
            return label["id"]

    label = client.create_label(trello_board_id, "AI Draft", "blue")
    return label["id"]


def _add_card_details(
    client: TrelloClient,
    card_id: str,
    label_id: str,
    evidence_url: str,
) -> None:
    """Add the label and avoid attaching the same evidence link twice."""
    client.add_label(card_id, label_id)

    for attachment in client.list_attachments(card_id):
        if attachment.get("url") == evidence_url:
            return

    client.attach_evidence_link(card_id, evidence_url)


def _ensure_story_checklists(client: TrelloClient, card_id: str, draft: DraftStory) -> None:
    """Add missing items without resetting checks or duplicating text."""
    checklists = client.list_checklists(card_id)
    groups = [
        ("Acceptance Criteria", draft.acceptance_criteria),
        ("Solution Tasks (proposed)", draft.solution_tasks),
    ]
    for name, items in groups:
        if not items:
            continue
        checklist = next((item for item in checklists if item["name"] == name), None)
        if checklist is None:
            checklist = client.create_checklist(card_id, name)
        existing_names = {item["name"] for item in checklist.get("checkItems", [])}
        for item in items:
            if item not in existing_names:
                client.add_checklist_item(checklist["id"], item)
                existing_names.add(item)


def export_story(
    session: Session,
    client: TrelloClient,
    board_id: UUID,
    body: ImportCreate,
) -> ImportRead:
    """Export an accepted story, or resume its previous export attempt."""
    try:
        return _export_story(session, client, board_id, body)
    except requests.RequestException:
        session.rollback()
        # Requests exceptions may contain authentication tokens in their URLs.
        raise HTTPException(
            status_code=502,
            detail="Trello request failed. Retry this export to reconcile any saved card.",
        ) from None


def _export_story(
    session: Session,
    client: TrelloClient,
    board_id: UUID,
    body: ImportCreate,
) -> ImportRead:
    # Check that this story belongs to the requested Trello board.
    board = get_record(session, Board, board_id)
    if board.provider != "trello":
        raise HTTPException(status_code=409, detail="Only Trello exports are supported")
    trello_board_id = board.external_id
    story = get_record(session, Story, body.story_id, lock=True)
    meeting = get_record(session, Meeting, story.meeting_id)
    if meeting.board_id != board_id:
        raise HTTPException(status_code=404, detail="Story does not belong to this board")

    import_query = select(Import).where(Import.story_id == story.id)
    import_record = session.exec(import_query).first()
    if import_record is not None and import_record.status == "completed":
        return ImportRead.model_validate(import_record)
    if story.status != "accepted":
        raise HTTPException(status_code=409, detail="Accept the story before exporting it")
    try:
        draft = DraftStory.model_validate(story.draft)
    except ValidationError:
        raise HTTPException(status_code=409, detail="Stored story draft is invalid") from None

    # Keep the same key across retries, and reuse a card we already know about.
    import_key = f"trello:{story.id}"
    card_id = None
    if import_record is not None:
        import_key = import_record.idempotency_key
        card_id = import_record.external_card_id

    if card_id is None:
        existing_card = client.find_import_by_key(trello_board_id, import_key)
        if existing_card is not None:
            card_id = existing_card["id"]
        elif import_record is not None and import_record.status == "pending":
            # A timed-out request may still create a card. Don't send another one.
            raise HTTPException(
                status_code=409,
                detail="Card creation is pending or uncertain. No matching card was found; no new card was created.",
            )

    if card_id is None:
        open_lists = client.list_lists(trello_board_id)
        open_list_ids = {trello_list["id"] for trello_list in open_lists}
        if body.list_id not in open_list_ids:
            raise HTTPException(status_code=409, detail="Choose an open list on this Trello board")

    label_id = _get_ai_draft_label(client, trello_board_id)

    # Save our intent before asking Trello to create anything. If the request
    # times out, the next attempt will see pending and search for the marker.
    if import_record is None:
        import_record = Import(story_id=story.id, idempotency_key=import_key)
        session.add(import_record)
    import_record.status = "pending"
    import_record.external_card_id = card_id
    import_id = import_record.id
    story_id = story.id
    session.commit()

    # Committing releases the lock. Lock again, then check whether another
    # request finished this export while we were waiting.
    story = get_record(session, Story, story_id, lock=True)
    import_record = session.get(Import, import_id)
    if import_record.status == "completed":
        return ImportRead.model_validate(import_record)
    card_id = import_record.external_card_id
    if card_id is None:
        description = client.format_story(draft)
        card = client.create_card(
            body.list_id,
            draft.title,
            description,
            idempotency_key=import_key,
        )
        card_id = card["id"]
        import_record.external_card_id = card_id

        # Save the card ID immediately so a failed label/link request won't
        # cause the next attempt to create another card.
        session.commit()
        story = get_record(session, Story, story_id, lock=True)
        import_record = session.get(Import, import_id)
        if import_record.status == "completed":
            return ImportRead.model_validate(import_record)

    _ensure_story_checklists(client, card_id, draft)
    _add_card_details(client, card_id, label_id, str(body.evidence_url))

    # Finish the database changes together: import, story, and analytics event.
    import_record.status = "completed"
    story.status = "imported"
    event = AnalyticsEvent(
        board_id=board_id,
        story_id=story_id,
        event_type="story_imported",
        confidence=story.confidence,
        invest_score=story.invest_score,
        properties={"external_card_id": card_id},
    )
    session.add(event)
    session.flush()
    result = ImportRead.model_validate(import_record)
    session.commit()
    return result
