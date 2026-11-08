"""Export accepted stories to Jira Cloud without blindly retrying issue creation."""

from uuid import UUID

import requests
from fastapi import HTTPException
from pydantic import ValidationError
from sqlmodel import Session, select

from adapters.jira.client import JiraClient
from services.models import AnalyticsEvent, Board, Import, Meeting, Story
from services.worker.objects import DraftStory

from .dependencies import get_record
from .schemas import ImportRead, JiraImportCreate


def export_jira_story(session: Session, client: JiraClient, board_id: UUID, body: JiraImportCreate):
    try:
        return _export(session, client, board_id, body)
    except requests.RequestException:
        session.rollback()
        raise HTTPException(status_code=502, detail="Jira request failed; retry to reconcile the saved issue") from None


def validate_jira_destination(client, cloud_id, project_id, issue_type_id, additional_fields):
    """Preflight access and required fields for manual and automatic imports."""
    client.get_project(cloud_id, project_id)
    provided_fields = {"project", "issuetype", "summary", "description", "labels", *additional_fields}
    start_at = 0
    while True:
        metadata = client.get_create_fields(cloud_id, project_id, issue_type_id, start_at)
        fields = metadata["fields"]
        missing = [
            field.get("fieldId", field.get("key")) for field in fields
            if field.get("required") and not field.get("hasDefaultValue")
            and field.get("fieldId", field.get("key")) not in provided_fields
        ]
        if missing:
            raise HTTPException(status_code=409, detail={"message": "Jira requires additional fields", "fields": missing})
        start_at += len(fields)
        if not fields or start_at >= metadata.get("total", start_at):
            break


def _export(session, client, board_id, body):
    board = get_record(session, Board, board_id)
    if board.provider != "jira":
        raise HTTPException(status_code=409, detail="Choose a registered Jira project")
    try:
        cloud_id, project_id = board.external_id.split(":")
        cloud_id = str(UUID(cloud_id))
        if not project_id.isdigit():
            raise ValueError()
    except ValueError:
        raise HTTPException(status_code=409, detail="Jira external_id must be CLOUD_UUID:PROJECT_ID") from None

    story = get_record(session, Story, body.story_id, lock=True)
    meeting = get_record(session, Meeting, story.meeting_id)
    if meeting.board_id != board_id:
        raise HTTPException(status_code=404, detail="Story does not belong to this project")
    record = session.exec(select(Import).where(Import.story_id == story.id)).first()
    if record is not None and record.status == "completed":
        return ImportRead.model_validate(record)
    if story.status != "accepted":
        raise HTTPException(status_code=409, detail="Accept the story before exporting it")
    try:
        draft = DraftStory.model_validate(story.draft)
    except ValidationError:
        raise HTTPException(status_code=409, detail="Stored story draft is invalid") from None
    if not any(site["id"] == cloud_id for site in client.list_sites()):
        raise HTTPException(status_code=404, detail="Jira site is not accessible to this account")

    marker = f"backlogger-story-{story.id}"
    issue_key = record.external_card_id if record is not None else None
    if issue_key is None:
        try:
            existing = client.find_import_by_key(cloud_id, project_id, marker)
        except ValueError:
            raise HTTPException(status_code=409, detail="Multiple Jira issues match this import; reconcile them manually") from None
        if existing is not None:
            issue_key = existing["key"]
        elif record is not None and record.status == "pending":
            raise HTTPException(status_code=409, detail="Issue creation is pending or uncertain; no new issue was created")
    if issue_key is None:
        validate_jira_destination(client, cloud_id, project_id, body.issue_type_id, body.additional_fields)

    if record is None:
        record = Import(story_id=story.id, idempotency_key=f"jira:{story.id}")
        session.add(record)
    record.status = "pending"
    record.external_card_id = issue_key
    import_id, story_id = record.id, story.id
    session.commit()

    story = get_record(session, Story, story_id, lock=True)
    record = session.get(Import, import_id)
    if record.status == "completed":
        return ImportRead.model_validate(record)
    issue_key = record.external_card_id
    if issue_key is None:
        fields = {
            **body.additional_fields,
            "project": {"id": project_id}, "issuetype": {"id": body.issue_type_id},
            "summary": draft.title,
            "description": client.format_story(draft, str(body.evidence_url)),
            "labels": ["ai-draft", marker],
        }
        try:
            issue = client.create_issue(cloud_id, fields)
        except requests.HTTPError as error:
            # Explicit validation rejection means no issue was created. Let the
            # user fix field values and retry, unlike an uncertain timeout/5xx.
            if error.response is not None and error.response.status_code in {400, 422}:
                record.status = "failed"
                session.commit()
            raise
        issue_key = issue["key"]
        record.external_card_id = issue_key
        session.commit()
        story = get_record(session, Story, story_id, lock=True)
        record = session.get(Import, import_id)
        if record.status == "completed":
            return ImportRead.model_validate(record)

    story.status = "imported"
    record.status = "completed"
    session.add(AnalyticsEvent(
        board_id=board_id, story_id=story_id, event_type="story_imported",
        confidence=story.confidence, invest_score=story.invest_score,
        properties={"provider": "jira", "external_issue_key": issue_key},
    ))
    session.flush()
    result = ImportRead.model_validate(record)
    session.commit()
    return result
