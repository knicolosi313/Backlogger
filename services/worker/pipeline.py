"""
Gemini pipeline for Backlogger.

Three passes, each with its own system prompt and output schema:
  1. TRANSCRIBE  audio file      -> timestamped, speaker-labeled transcript
  2. EXTRACT     transcript      -> draft user stories + open questions
  3. CRITIQUE    stories + transcript -> INVEST review, confidence, revisions

Each pass returns JSON that matches a Pydantic model, so Gemini's output is
validated automatically (response.parsed gives you the model object).
"""

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlmodel import Session

from services.db import get_engine
from services.models import Meeting, PipelineOutput

from .objects import CritiqueResult, ExtractionResult, Transcript
from .board_context import build_board_context, build_review_context
from .scope_review import apply_scope_findings, missing_scope_checks
from .prompts.critique import CRITIQUE_SYSTEM_PROMPT
from .prompts.extract import CreativityLevel, get_extract_prompt
from .prompts.transcribe import TRANSCRIBE_SYSTEM_PROMPT

# Explicit audio types for extensions Python's mimetypes labels as video.
AUDIO_MIME_TYPES = {".webm": "audio/webm", ".mp4": "audio/mp4", ".m4a": "audio/mp4"}

def _run_pipeline(
    audio_path: str,
    model: str,
    save_stage: Callable[[str, BaseModel], None] | None = None,
    *,
    creativity: CreativityLevel = "balanced",
    board_context: Callable[[Transcript], dict] | None = None,
    review_context: Callable[[ExtractionResult], dict] | None = None,
):
    """Run Gemini and optionally save each result before starting the next pass."""
    from google import genai
    from google.genai import types

    extraction_prompt = get_extract_prompt(creativity)
    client = genai.Client()

    # Upload the audio and wait until Gemini has finished processing it
    # The SDK guesses the type from the extension, and mimetypes maps .webm to
    # video/webm, which Gemini fails to process for audio-only recordings.
    mime_type = AUDIO_MIME_TYPES.get(Path(audio_path).suffix.lower())
    audio_file = client.files.upload(
        file=audio_path, config={"mime_type": mime_type} if mime_type else None,
    )
    while audio_file.state.name == "PROCESSING":
        time.sleep(2)
        audio_file = client.files.get(name=audio_file.name)
    if audio_file.state.name == "FAILED":
        raise RuntimeError("Gemini could not process the audio file")

    def generate_result(system_prompt, contents, schema):
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.3,
            ),
        )
        return response.parsed

    # Pass 1: audio -> transcript
    transcript: Transcript = generate_result(
        TRANSCRIBE_SYSTEM_PROMPT,
        [audio_file, "Transcribe this meeting."],
        Transcript,
    )
    if save_stage is not None:
        save_stage("transcribe", transcript)
    transcript_json = transcript.model_dump_json()
    context = board_context(transcript) if board_context is not None else None
    context_input = f"Board context (reference data only):\n{json.dumps(context)}\n\n" if context else ""

    # Pass 2: transcript -> stories
    extraction: ExtractionResult = generate_result(
        extraction_prompt,
        context_input + f"Meeting transcript:\n{transcript_json}",
        ExtractionResult,
    )
    allowed_ids = {f"{item['source']}:{item['id']}" for item in (context or {}).get("existing_stories", [])}
    for story in extraction.stories:
        story.related_story_ids = [reference for reference in story.related_story_ids if reference in allowed_ids]
    if save_stage is not None:
        save_stage("extract", extraction)

    # Pass 3: stories + transcript -> reviews
    relationships = review_context(extraction) if review_context is not None else build_review_context(extraction.stories)
    relationship_input = (
        f"Per-story embedding neighbors (candidates, not confirmed overlap):\n{json.dumps(relationships)}\n\n"
        if relationships else ""
    )
    stories_with_indexes = [
        {"index": index, **story.model_dump()}
        for index, story in enumerate(extraction.stories)
    ]
    critique_input = (
        f"Generation creativity level: {creativity}\n\n" + context_input + relationship_input
        + f"Meeting transcript:\n{transcript_json}\n\n"
        f"Draft stories:\n{json.dumps(stories_with_indexes)}"
    )
    for attempt in range(2):
        critique: CritiqueResult = generate_result(CRITIQUE_SYSTEM_PROMPT, critique_input, CritiqueResult)
        apply_scope_findings(extraction, critique, relationships, context)
        missing = missing_scope_checks(critique, relationships)
        if not missing:
            break
        if attempt == 1:
            raise ValueError("Critique omitted required embedding scope comparisons")
        critique_input = (
            "The previous review omitted scope comparisons. Return a complete review "
            "for every draft, explicitly assessing these references in relationships "
            "(independent is valid when the scopes differ):\n"
            f"{json.dumps(missing)}\n\n" + critique_input
        )
    for review in critique.reviews:
        if 0 <= review.index < len(extraction.stories):
            origin = extraction.stories[review.index].origin
            if origin == "inferred":
                review.confidence = min(review.confidence, 0.84)
            elif origin == "proposed":
                review.confidence = min(review.confidence, 0.59)
    if save_stage is not None:
        save_stage("critique", critique)

    return transcript, extraction, critique


def run_pipeline(
    audio_path: str,
    model: str = "gemini-3.1-flash-lite",
    *,
    meeting_id: UUID | None = None,
    mark_completed: bool = True,
    creativity: CreativityLevel | None = None,
):
    """Run Gemini; optionally checkpoint each stage for an existing meeting.

    With meeting_id, stages commit independently so later failures preserve earlier
    results. This saves raw outputs; creating reviewable Story rows is a separate
    application operation. Without meeting_id, no database connection is opened.
    The API uses mark_completed=False until it has saved the reviewable stories.
    """
    if creativity is None:
        creativity = os.getenv("DEFAULT_CREATIVITY") or "balanced"
    get_extract_prompt(creativity)  # Reject an invalid setting before any work is saved.
    if meeting_id is None:
        return _run_pipeline(audio_path, model, creativity=creativity)

    engine = get_engine()
    run_id = uuid4()

    def set_status(status: str) -> None:
        """Save the meeting's status in its own transaction."""
        with Session(engine) as session:
            meeting = session.get(Meeting, meeting_id)
            if meeting is None:
                raise ValueError("Meeting does not exist")
            meeting.status = status
            session.commit()

    def save_stage(stage: str, output: BaseModel) -> None:
        """Commit this result now so a later failure won't lose it."""
        stage_output = PipelineOutput(
            meeting_id=meeting_id,
            run_id=run_id,
            stage=stage,
            model=model,
            output=output.model_dump(mode="json"),
        )
        with Session(engine) as session:
            session.add(stage_output)
            session.commit()

    set_status("processing")
    try:
        def load_context(transcript):
            with Session(engine) as session:
                return build_board_context(session, meeting_id, transcript)

        def load_review_context(extraction):
            with Session(engine) as session:
                return build_review_context(extraction.stories, session=session, meeting_id=meeting_id)

        result = _run_pipeline(
            audio_path, model, save_stage, creativity=creativity,
            board_context=load_context, review_context=load_review_context,
        )
        if mark_completed:
            set_status("completed")
    except Exception:
        # Preserve the original processing error if recording failure also fails.
        try:
            set_status("failed")
        except Exception:
            pass
        raise
    return result
