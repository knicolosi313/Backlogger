"""Meeting creation, uploads, processing, and saved transcripts."""

import logging
import os
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlmodel import select

from services.models import AutoImportRun, Board, Meeting, PipelineOutput
from services.worker.objects import Transcript
from services.worker.prompts.extract import get_extract_prompt

from .dependencies import SessionDep, get_record
from .jobs import process_meeting
from .media import CONTENT_TYPES, delete_media, media_exists, processing_audio, save_audio
from .schemas import MeetingCreate, MeetingStatus, ProcessAccepted, ProcessOptions

router = APIRouter(prefix="/api/meetings", tags=["Meetings"])
logger = logging.getLogger(__name__)


@router.post("", response_model=Meeting, status_code=201)
def create_meeting(body: MeetingCreate, session: SessionDep):
    get_record(session, Board, body.board_id)
    meeting = Meeting(board_id=body.board_id, title=body.title)
    session.add(meeting)
    session.commit()
    session.refresh(meeting)
    return meeting


@router.get("", response_model=list[Meeting])
def list_meetings(
    session: SessionDep,
    board_id: UUID | None = None,
    status: MeetingStatus | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    query = select(Meeting)
    if board_id is not None:
        get_record(session, Board, board_id)
        query = query.where(Meeting.board_id == board_id)
    if status is not None:
        query = query.where(Meeting.status == status)
    return session.exec(
        query.order_by(Meeting.created_at.desc(), Meeting.id).offset(offset).limit(limit)
    ).all()


@router.get("/{meeting_id}", response_model=Meeting)
def get_meeting(meeting_id: UUID, session: SessionDep):
    return get_record(session, Meeting, meeting_id)


@router.post("/{meeting_id}/media", response_model=Meeting)
def upload_media(meeting_id: UUID, file: UploadFile, session: SessionDep):
    meeting = get_record(session, Meeting, meeting_id, lock=True)
    if meeting.status not in {"uploaded", "failed"}:
        raise HTTPException(status_code=409, detail="Meeting cannot receive audio in its current state")
    previous_key = meeting.media_key
    key = save_audio(meeting_id, file)
    try:
        meeting.media_key = key
        meeting.status = "uploaded"
        session.commit()
    except Exception:
        try:
            delete_media(key)
        except Exception:
            logger.warning("Could not clean up a failed media upload for meeting %s", meeting_id)
        raise
    if previous_key and previous_key != key:
        try:
            delete_media(previous_key)
        except Exception:
            logger.warning("Could not remove replaced media for meeting %s", meeting_id)
    session.refresh(meeting)
    return meeting


@router.post("/{meeting_id}/process", response_model=ProcessAccepted, status_code=202)
def start_processing(
    meeting_id: UUID, tasks: BackgroundTasks, session: SessionDep, request: Request,
    options: ProcessOptions | None = None,
):
    meeting = get_record(session, Meeting, meeting_id, lock=True)
    if meeting.status not in {"uploaded", "failed"}:
        raise HTTPException(status_code=409, detail="Meeting is already processing or completed")
    if not meeting.media_key or not media_exists(meeting.media_key):
        raise HTTPException(status_code=409, detail="Upload audio before processing")
    if not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY is not configured")

    creativity = options.creativity if options is not None else None
    if creativity is None:
        creativity = os.getenv("DEFAULT_CREATIVITY") or "balanced"
    try:
        get_extract_prompt(creativity)
    except ValueError:
        raise HTTPException(status_code=503, detail="DEFAULT_CREATIVITY is not a supported creativity level") from None

    auto_import = options.auto_import if options is not None else None
    if auto_import is not None:
        from .auto_import import configure_auto_import
        configure_auto_import(request, session, meeting, auto_import)
    elif meeting.status == "failed":
        previous_run = session.exec(select(AutoImportRun).where(AutoImportRun.meeting_id == meeting.id).with_for_update()).first()
        if previous_run is not None and not previous_run.story_ids and previous_run.status != "running":
            # Retrying audio processing with the toggle off must not retain old opt-in settings.
            session.delete(previous_run)
    media_key = meeting.media_key
    meeting.status = "processing"
    session.commit()
    arguments = [meeting_id, media_key, os.getenv("GEMINI_MODEL") or "gemini-3.1-flash-lite", creativity]
    if auto_import is not None:
        arguments.append(True)
    tasks.add_task(process_meeting, *arguments)
    return ProcessAccepted(
        meeting_id=meeting_id, status="processing", status_url=f"/api/meetings/{meeting_id}",
    )


@router.get("/{meeting_id}/audio", response_class=StreamingResponse)
def get_meeting_audio(meeting_id: UUID, session: SessionDep):
    """Serve audio through the backend without exposing storage credentials."""
    meeting = get_record(session, Meeting, meeting_id)
    key = meeting.media_key
    if not key or not media_exists(key):
        raise HTTPException(404, "Meeting audio is unavailable")
    content_types = {suffix: content_type for content_type, suffix in CONTENT_TYPES.items()}
    content_types.update({".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac"})

    def chunks():
        # Keep the temporary remote download alive until the response finishes.
        with processing_audio(key) as path, path.open("rb") as audio:
            while chunk := audio.read(1024 * 1024):
                yield chunk

    return StreamingResponse(
        chunks(), media_type=content_types.get(Path(key).suffix.lower(), "application/octet-stream"),
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/{meeting_id}/transcript", response_model=Transcript)
def get_transcript(meeting_id: UUID, session: SessionDep):
    get_record(session, Meeting, meeting_id)
    output = session.exec(
        select(PipelineOutput)
        .where(PipelineOutput.meeting_id == meeting_id, PipelineOutput.stage == "transcribe")
        .order_by(PipelineOutput.created_at.desc(), PipelineOutput.id)
        .limit(1)
    ).first()
    if output is None:
        raise HTTPException(status_code=404, detail="Transcript is not available yet")
    return output.output
