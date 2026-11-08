"""Meeting creation, uploads, processing, and saved transcripts."""

import os
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, UploadFile
from sqlmodel import select

from services.models import Board, Meeting, PipelineOutput
from services.worker.objects import Transcript

from .dependencies import SessionDep, get_record
from .jobs import process_meeting
from .media import media_path, save_audio
from .schemas import MeetingCreate, MeetingStatus, ProcessAccepted

router = APIRouter(prefix="/api/meetings", tags=["Meetings"])


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
    key = save_audio(meeting_id, file)
    try:
        meeting.media_key = key
        meeting.status = "uploaded"
        session.commit()
    except Exception:
        media_path(key).unlink(missing_ok=True)
        raise
    session.refresh(meeting)
    return meeting


@router.post("/{meeting_id}/process", response_model=ProcessAccepted, status_code=202)
def start_processing(meeting_id: UUID, tasks: BackgroundTasks, session: SessionDep):
    meeting = get_record(session, Meeting, meeting_id, lock=True)
    if meeting.status not in {"uploaded", "failed"}:
        raise HTTPException(status_code=409, detail="Meeting is already processing or completed")
    if not meeting.media_key or not media_path(meeting.media_key).is_file():
        raise HTTPException(status_code=409, detail="Upload audio before processing")
    if not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY is not configured")

    media_key = meeting.media_key
    meeting.status = "processing"
    session.commit()
    tasks.add_task(
        process_meeting, meeting_id, media_key,
        os.getenv("GEMINI_MODEL") or "gemini-3.1-flash-lite",
    )
    return ProcessAccepted(
        meeting_id=meeting_id, status="processing", status_url=f"/api/meetings/{meeting_id}",
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
