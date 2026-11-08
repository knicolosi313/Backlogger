"""Keep the times in a transcript and its story quotes inside the real recording.

Gemini is unreliable at telling time in audio. On longer recordings it has returned timelines
stretched to 1.6x the real length, so quotes pointed past the end of the video. Three defenses:
the model is told the true length and answers in MM:SS (which it follows far better than seconds),
the times are checked against that length here, and each story's quote time is anchored to the
transcript text instead of trusting numbers the model copied.
"""

import logging
import re

from .objects import ClockTranscript, DraftStory, Transcript, TranscriptSegment
from .timecode import parse_clock

logger = logging.getLogger(__name__)

# Google documents audio as 32 tokens per second. A 134.9 second recording counted 4321 tokens.
AUDIO_TOKENS_PER_SECOND = 32
# A little overrun is normal rounding. Beyond this the whole timeline is treated as stretched.
OVERRUN_TOLERANCE_SECONDS = 1.5
# How many opening words identify where a quote begins, and how many of them must be found.
OPENING_WORDS = 6
MIN_OPENING_MATCH = 0.75


def audio_seconds(client, model: str, audio_file) -> float | None:
    """The real length of the uploaded audio, or None if it can't be measured."""
    try:
        tokens = client.models.count_tokens(model=model, contents=[audio_file]).total_tokens
    except Exception:
        logger.warning("Could not measure the audio length", exc_info=True)
        return None
    return tokens / AUDIO_TOKENS_PER_SECOND if isinstance(tokens, int) and tokens > 0 else None


def to_transcript(clock: ClockTranscript, duration: float | None) -> Transcript:
    """Turn MM:SS segments into seconds that are in order and inside the recording."""
    segments = [
        (segment, parse_clock(segment.start), parse_clock(segment.end)) for segment in clock.segments
    ]
    latest = max((end for _, _, end in segments), default=0.0)
    scale = 1.0
    if duration and latest > duration + OVERRUN_TOLERANCE_SECONDS:
        # Still stretched even though the model was told the length. Squeeze the timeline back
        # rather than leave times that point past the end.
        scale = duration / latest
        logger.warning("Transcript ran to %.1fs in a %.1fs recording; rescaling by %.2f", latest, duration, scale)

    limit = duration if duration else float("inf")
    result: list[TranscriptSegment] = []
    previous_start = 0.0
    for segment, start, end in segments:
        start = min(max(start * scale, previous_start), limit)
        end = min(max(end * scale, start), limit)
        result.append(TranscriptSegment(
            speaker=segment.speaker, start_s=round(start, 1), end_s=round(end, 1), text=segment.text,
        ))
        previous_start = start
    return Transcript(language=clock.language, segments=result)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _locate(quote: str, transcript: Transcript) -> tuple[TranscriptSegment, float] | None:
    """The segment where the quote begins, and the moment inside it. None if it can't be found."""
    opening = _words(quote)[:OPENING_WORDS]
    if not opening:
        return None
    best: tuple[float, int, TranscriptSegment, float] | None = None
    for index, segment in enumerate(transcript.segments):
        words = _words(segment.text)
        if not words:
            continue
        # The opening words in a row is the strongest evidence, and says how far in the quote starts.
        for offset in range(len(words) - len(opening) + 1):
            if words[offset:offset + len(opening)] == opening:
                fraction = offset / len(words)
                moment = segment.start_s + fraction * (segment.end_s - segment.start_s)
                return segment, moment
        found = sum(1 for word in opening if word in words) / len(opening)
        if best is None or found > best[0]:
            best = (found, index, segment, segment.start_s)
    if best is not None and best[0] >= MIN_OPENING_MATCH:
        return best[2], best[3]
    return None


def anchor_sources(stories: list[DraftStory], transcript: Transcript, duration: float | None) -> None:
    """Set each story's quote time from where the quote really is in the transcript."""
    limit = duration if duration else float("inf")
    for story in stories:
        source = story.source
        located = _locate(source.quote, transcript)
        if located is not None:
            segment, moment = located
            source.start_s = round(moment, 1)
            source.end_s = max(segment.end_s, source.start_s)
        else:
            # The quote isn't in the transcript, so the model's own numbers are all there is.
            source.start_s = round(min(max(source.start_s, 0.0), limit), 1)
            source.end_s = round(min(max(source.end_s, source.start_s), limit), 1)
