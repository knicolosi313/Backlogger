"""Transcript and quote times must stay inside the recording. No Gemini or database needed."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services.worker.objects import ClockSegment, ClockTranscript, DraftStory, Transcript, TranscriptSegment
from services.worker.timecode import format_clock, parse_clock
from services.worker.timeline import anchor_sources, audio_seconds, to_transcript


@pytest.mark.parametrize("seconds,expected", [
    (0, "0:00"), (9, "0:09"), (59.99, "0:59"), (60, "1:00"), (134.9, "2:14"),
    (3599, "59:59"), (3725, "1:02:05"), (-5, "0:00"),
])
def test_clock_times_look_like_a_video_player(seconds, expected):
    assert format_clock(seconds) == expected


@pytest.mark.parametrize("text,expected", [
    ("02:14", 134.0), ("2:14", 134.0), ("1:02:05", 3725.0), ("2:14.5", 134.5), ("90", 90.0), (" 00:07 ", 7.0),
])
def test_clock_times_are_read_back_into_seconds(text, expected):
    assert parse_clock(text) == expected


@pytest.mark.parametrize("text", ["", ":", "a:b", "1:2:3:4", "-1:00", "nan", "inf", "1::2", "two minutes"])
def test_garbled_clock_times_are_rejected(text):
    with pytest.raises(ValueError):
        parse_clock(text)


def clock(*segments, language="en"):
    return ClockTranscript(language=language, segments=[
        ClockSegment(speaker="Alex", start=start, end=end, text=text) for start, end, text in segments
    ])


def test_times_inside_the_recording_are_kept():
    transcript = to_transcript(clock(("00:02", "00:05", "One"), ("00:05", "01:10", "Two")), duration=135.0)
    assert [(s.start_s, s.end_s) for s in transcript.segments] == [(2.0, 5.0), (5.0, 70.0)]
    assert transcript.language == "en"


def test_a_timeline_stretched_past_the_recording_is_squeezed_back():
    # The real failure: a 134.9s recording came back ending at 3:33 (213s), 1.58x too long.
    stretched = clock(("00:00", "00:20", "a"), ("01:46", "02:30", "b"), ("02:30", "03:33", "c"))
    transcript = to_transcript(stretched, duration=134.9)
    assert transcript.segments[-1].end_s == pytest.approx(134.9, abs=0.1)
    assert all(0 <= s.start_s <= s.end_s <= 134.9 for s in transcript.segments)
    # the squeeze is proportional, so order and spacing are kept
    assert transcript.segments[1].start_s == pytest.approx(106 * 134.9 / 213, abs=0.1)


def test_a_small_overrun_is_clamped_not_rescaled():
    transcript = to_transcript(clock(("00:00", "01:00", "a"), ("01:00", "02:16", "b")), duration=135.0)
    assert transcript.segments[0].end_s == 60.0
    assert transcript.segments[-1].end_s == 135.0


def test_segments_never_run_backwards():
    transcript = to_transcript(clock(("00:10", "00:20", "a"), ("00:05", "00:08", "b")), duration=60.0)
    first, second = transcript.segments
    assert second.start_s >= first.start_s
    assert second.end_s >= second.start_s


def test_without_a_known_length_times_are_kept_as_given():
    transcript = to_transcript(clock(("00:00", "10:00", "a")), duration=None)
    assert transcript.segments[0].end_s == 600.0


def test_an_unreadable_time_fails_instead_of_guessing():
    with pytest.raises(ValueError):
        to_transcript(clock(("soon", "00:05", "a")), duration=60.0)


@pytest.mark.parametrize("tokens,expected", [(4321, 4321 / 32), (32, 1.0)])
def test_audio_length_comes_from_gemini_counting_32_tokens_per_second(tokens, expected):
    client = Mock()
    client.models.count_tokens.return_value = SimpleNamespace(total_tokens=tokens)
    assert audio_seconds(client, "model", "file") == expected
    client.models.count_tokens.assert_called_once_with(model="model", contents=["file"])


@pytest.mark.parametrize("failure", [RuntimeError("unavailable"), None])
def test_audio_length_is_none_when_it_cannot_be_measured(failure):
    client = Mock()
    client.models.count_tokens.side_effect = failure
    client.models.count_tokens.return_value = SimpleNamespace(total_tokens=0)
    assert audio_seconds(client, "model", "file") is None


TRANSCRIPT = Transcript(language="en", segments=[
    TranscriptSegment(speaker="A", start_s=0.0, end_s=10.0, text="Let's start with the sprint goals today."),
    TranscriptSegment(
        speaker="B", start_s=10.0, end_s=30.0,
        text="So we need a settings page, like a profile page where you can set your location on, and also email alerts.",
    ),
    TranscriptSegment(speaker="A", start_s=30.0, end_s=40.0, text="Agreed, let's ship the alerts first."),
])


def story(quote, start_s=999.0, end_s=1004.0):
    return DraftStory(
        title="T", as_a="user", i_want="x", so_that="y", acceptance_criteria=["c"], priority="med",
        needs_clarification=False, source={"quote": quote, "start_s": start_s, "end_s": end_s, "speaker": "B"},
    )


def test_quote_time_is_where_the_quote_begins_inside_a_long_segment():
    drafted = story("a profile page where you can set your location on")
    anchor_sources([drafted], TRANSCRIPT, duration=40.0)
    # 'a profile page...' starts partway through B's 10s-30s segment, not at its start
    assert 10.0 < drafted.source.start_s < 30.0
    assert drafted.source.end_s == 30.0


def test_a_quote_at_the_start_of_a_segment_begins_at_that_segment():
    drafted = story("Agreed, let's ship the alerts first.")
    anchor_sources([drafted], TRANSCRIPT, duration=40.0)
    assert drafted.source.start_s == 30.0


def test_the_models_own_time_is_ignored_when_the_quote_is_found():
    drafted = story("Let's start with the sprint goals", start_s=500.0, end_s=520.0)
    anchor_sources([drafted], TRANSCRIPT, duration=40.0)
    assert (drafted.source.start_s, drafted.source.end_s) == (0.0, 10.0)


def test_a_slightly_reworded_quote_still_lands_on_its_segment():
    # one of the opening words differs (the model dropped "we")
    drafted = story("So need a settings page like a profile page")
    anchor_sources([drafted], TRANSCRIPT, duration=40.0)
    assert drafted.source.start_s == 10.0


def test_a_quote_that_is_not_in_the_transcript_keeps_the_models_time_but_inside_the_recording():
    drafted = story("Something nobody ever said out loud", start_s=300.0, end_s=310.0)
    anchor_sources([drafted], TRANSCRIPT, duration=40.0)
    assert 0 <= drafted.source.start_s <= drafted.source.end_s <= 40.0


def test_an_empty_quote_cannot_point_past_the_end():
    drafted = story("", start_s=300.0, end_s=310.0)
    anchor_sources([drafted], TRANSCRIPT, duration=40.0)
    assert drafted.source.end_s <= 40.0
