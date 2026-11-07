"""
Gemini pipeline for Backlogger.

Three passes, each with its own system prompt and output schema:
  1. TRANSCRIBE  audio file      -> timestamped, speaker-labeled transcript
  2. EXTRACT     transcript      -> draft user stories + open questions
  3. CRITIQUE    stories + transcript -> INVEST review, confidence, revisions

Each pass returns JSON that matches a Pydantic model, so Gemini's output is
validated automatically (response.parsed gives you the model object).
"""

from .objects import CritiqueResult, ExtractionResult, Transcript
from .prompts.critique import CRITIQUE_SYSTEM_PROMPT
from .prompts.extract import EXTRACT_SYSTEM_PROMPT
from .prompts.transcribe import TRANSCRIBE_SYSTEM_PROMPT

# =============================================================================
# RUNNING THE PIPELINE
# =============================================================================

def run_pipeline(audio_path: str, model: str = "gemini-3.1-flash-lite"):
    """Example of chaining the three passes. Swap the model name for whichever
    Gemini model your team is using."""
    import json
    import time
    from google import genai
    from google.genai import types

    client = genai.Client()

    # Upload the audio and wait until Gemini has finished processing it
    file = client.files.upload(file=audio_path)
    while file.state.name == "PROCESSING":
        time.sleep(2)
        file = client.files.get(name=file.name)
    if file.state.name == "FAILED":
        raise RuntimeError("Gemini could not process the audio file")

    def call(system_prompt, contents, schema):
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
    transcript: Transcript = call(
        TRANSCRIBE_SYSTEM_PROMPT,
        [file, "Transcribe this meeting."],
        Transcript,
    )
    transcript_json = transcript.model_dump_json()

    # Pass 2: transcript -> stories
    extraction: ExtractionResult = call(
        EXTRACT_SYSTEM_PROMPT,
        f"Meeting transcript:\n{transcript_json}",
        ExtractionResult,
    )

    # Pass 3: stories + transcript -> reviews
    indexed = [{"index": i, **s.model_dump()} for i, s in enumerate(extraction.stories)]
    critique: CritiqueResult = call(
        CRITIQUE_SYSTEM_PROMPT,
        f"Meeting transcript:\n{transcript_json}\n\n"
        f"Draft stories:\n{json.dumps(indexed)}",
        CritiqueResult,
    )

    return transcript, extraction, critique
