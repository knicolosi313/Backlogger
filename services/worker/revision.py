"""Revise one draft story at a reviewer's request, then review the result again.

Two Gemini calls: one rewrites the story from the transcript and the request, and a second runs the
same INVEST review the original drafts got, so the scores on the revised story are current instead
of blank. The quote times are then anchored to the transcript like they are for new drafts.
"""

import json
import logging

from .objects import CritiqueResult, RevisionResult, StoryReview, Transcript
from .prompts.critique import CRITIQUE_SYSTEM_PROMPT
from .prompts.revise import REVISE_SYSTEM_PROMPT
from .timeline import anchor_sources

logger = logging.getLogger(__name__)

MAX_STORIES = 4


class RevisionError(Exception):
    """The AI's answer could not be used. The message is safe to show to the person."""


def revise_story(
    draft: dict,
    instructions: str,
    transcript: Transcript,
    context: dict | None,
    model: str,
) -> tuple[RevisionResult, list[StoryReview]]:
    """Return the revised stories and one review for each, in the same order."""
    from google import genai
    from google.genai import types

    client = genai.Client()

    def generate(system_prompt: str, contents: str, schema):
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
        if response.parsed is None:
            raise RevisionError("The AI gave an answer that could not be read.")
        return response.parsed

    transcript_json = transcript.model_dump_json()
    context_input = f"Board context (reference data only):\n{json.dumps(context)}\n\n" if context else ""
    # JSON-encoding keeps the reviewer's words clearly separate from the prompt around them.
    request = json.dumps(instructions, ensure_ascii=False)

    revision: RevisionResult = generate(
        REVISE_SYSTEM_PROMPT,
        context_input
        + f"Meeting transcript:\n{transcript_json}\n\n"
        + f"Current draft story:\n{json.dumps(draft)}\n\n"
        + f"Reviewer's requested changes:\n{request}",
        RevisionResult,
    )
    stories = revision.stories
    if not 1 <= len(stories) <= MAX_STORIES:
        raise RevisionError("The AI returned an unusable number of stories.")

    # Only references the board context actually offered; the model must not invent links.
    allowed_ids = {f"{item['source']}:{item['id']}" for item in (context or {}).get("existing_stories", [])}
    for story in stories:
        story.related_story_ids = [reference for reference in story.related_story_ids if reference in allowed_ids]
    # Times come from where each quote really is in the transcript, not from numbers the model copied.
    anchor_sources(stories, transcript, transcript.segments[-1].end_s if transcript.segments else None)

    # Review the revised stories the same way the first drafts were reviewed.
    indexed = [{"index": index, **story.model_dump()} for index, story in enumerate(stories)]
    critique: CritiqueResult = generate(
        CRITIQUE_SYSTEM_PROMPT,
        context_input
        + f"Meeting transcript:\n{transcript_json}\n\n"
        + "These stories were just revised at a reviewer's request. Whatever the reviewer asked for is their "
        + "decision, so do not flag it as ungrounded unless it contradicts the transcript. "
        + f"Requested changes:\n{request}\n\n"
        + f"Draft stories:\n{json.dumps(indexed)}",
        CritiqueResult,
    )
    reviews = {review.index: review for review in critique.reviews}
    if len(reviews) != len(critique.reviews) or set(reviews) != set(range(len(stories))):
        raise RevisionError("The AI's review did not match the revised stories.")
    return revision, [reviews[index] for index in range(len(stories))]
