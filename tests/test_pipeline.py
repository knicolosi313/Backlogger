"""Exercise the Gemini pipeline with a fake SDK client; no audio or API calls."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google import genai

from services.worker import pipeline
from services.worker.objects import ClockTranscript
from services.worker.prompts.critique import CRITIQUE_SYSTEM_PROMPT
from services.worker.prompts.extract import EXTRACT_PROMPTS, EXTRACT_SYSTEM_PROMPT
from services.worker.prompts.transcribe import TRANSCRIBE_SYSTEM_PROMPT, get_transcribe_prompt

# 4321 tokens of audio is 135 seconds, at Gemini's 32 tokens per second
AUDIO_SECONDS = 4321 / 32


def uploaded_file(state):
    return SimpleNamespace(name="files/synthetic-audio", state=SimpleNamespace(name=state))


@pytest.fixture
def gemini(monkeypatch, gemini_responses):
    client = Mock()
    client.files.upload.return_value = uploaded_file("ACTIVE")
    client.models.count_tokens.return_value = SimpleNamespace(total_tokens=4321)
    client.models.generate_content.side_effect = gemini_responses
    monkeypatch.setattr(genai, "Client", Mock(return_value=client))
    monkeypatch.setattr(pipeline.time, "sleep", Mock())
    return client


def test_all_passes_use_the_right_prompt_schema_and_previous_results(gemini, pipeline_results):
    checkpoints = []

    def save_stage(stage, result):
        # Saving must happen before the next Gemini request.
        checkpoints.append((stage, result, gemini.models.generate_content.call_count))

    results = pipeline._run_pipeline("synthetic.wav", "test-model", save_stage)

    assert results == pipeline_results
    gemini.files.upload.assert_called_once_with(file="synthetic.wav", config=None)
    calls = gemini.models.generate_content.call_args_list
    assert len(calls) == 3
    expected_prompts = [get_transcribe_prompt(AUDIO_SECONDS), EXTRACT_SYSTEM_PROMPT, CRITIQUE_SYSTEM_PROMPT]
    # The transcript pass answers in MM:SS and is converted to seconds afterwards.
    expected_schemas = [ClockTranscript, type(pipeline_results[1]), type(pipeline_results[2])]
    for request, schema, prompt in zip(calls, expected_schemas, expected_prompts):
        assert request.kwargs["model"] == "test-model"
        config = request.kwargs["config"]
        assert config.system_instruction == prompt
        assert config.response_schema is schema
        assert config.response_mime_type == "application/json"

    transcript, extraction, _ = pipeline_results
    assert calls[0].kwargs["contents"][0] is gemini.files.upload.return_value
    assert transcript.model_dump_json() in calls[1].kwargs["contents"]
    critique_input = calls[2].kwargs["contents"]
    assert transcript.model_dump_json() in critique_input
    stories = json.loads(critique_input.split("Draft stories:\n", 1)[1])
    assert stories == [{"index": 0, **extraction.stories[0].model_dump()}]
    assert checkpoints == [
        ("transcribe", pipeline_results[0], 1),
        ("extract", pipeline_results[1], 2),
        ("critique", pipeline_results[2], 3),
    ]


def test_standalone_pipeline_returns_results_without_database(gemini, pipeline_results, monkeypatch):
    engine = Mock(side_effect=AssertionError("Unexpected database connection"))
    monkeypatch.setattr(pipeline, "get_engine", engine)

    assert pipeline.run_pipeline("synthetic.wav") == pipeline_results
    engine.assert_not_called()


def test_board_context_reaches_extraction_and_critique_and_unknown_links_are_removed(gemini, pipeline_results):
    board_context = {
        "epic_names": ["Account access"],
        "existing_stories": [{"source": "local", "id": "existing-story", "story": "Reset password"}],
    }
    pipeline_results[1].stories[0].related_story_ids = ["local:existing-story", "local:invented"]
    load_context = Mock(return_value=board_context)
    pipeline._run_pipeline("synthetic.wav", "test-model", board_context=load_context)
    load_context.assert_called_once_with(pipeline_results[0])
    for call in gemini.models.generate_content.call_args_list[1:]:
        assert "Account access" in call.kwargs["contents"]
        assert "existing-story" in call.kwargs["contents"]
    assert pipeline_results[1].stories[0].related_story_ids == ["local:existing-story"]


@pytest.mark.parametrize("filename, mime_type", [
    ("meeting.webm", "audio/webm"), ("MEETING.WEBM", "audio/webm"), ("meeting.m4a", "audio/mp4"),
])
def test_uploads_audio_only_containers_with_an_audio_mime_type(gemini, filename, mime_type):
    # The SDK guesses video/webm for .webm, which Gemini fails to process as audio.
    pipeline._run_pipeline(f"media/abc/{filename}", "test-model")

    gemini.files.upload.assert_called_once_with(
        file=f"media/abc/{filename}", config={"mime_type": mime_type},
    )


def test_waits_until_uploaded_audio_is_ready(gemini):
    gemini.files.upload.return_value = uploaded_file("PROCESSING")
    gemini.files.get.side_effect = [uploaded_file("PROCESSING"), uploaded_file("ACTIVE")]

    pipeline._run_pipeline("synthetic.wav", "test-model")

    assert gemini.files.get.call_count == 2
    assert pipeline.time.sleep.call_count == 2
    gemini.files.get.assert_called_with(name="files/synthetic-audio")


@pytest.mark.parametrize("initial_state", ["FAILED", "PROCESSING"])
def test_failed_audio_never_reaches_generation(gemini, initial_state):
    gemini.files.upload.return_value = uploaded_file(initial_state)
    gemini.files.get.return_value = uploaded_file("FAILED")

    with pytest.raises(RuntimeError, match="could not process the audio"):
        pipeline._run_pipeline("synthetic.wav", "test-model")

    gemini.models.generate_content.assert_not_called()


@pytest.mark.parametrize("failed_pass", [0, 1, 2], ids=["transcribe", "extract", "critique"])
def test_model_failure_stops_later_passes(gemini, gemini_responses, failed_pass):
    gemini.models.generate_content.side_effect = [
        *gemini_responses[:failed_pass],
        RuntimeError("Model request failed"),
    ]
    saved_stages = []

    with pytest.raises(RuntimeError, match="Model request failed"):
        pipeline._run_pipeline(
            "synthetic.wav", "test-model",
            lambda stage, result: saved_stages.append(stage),
        )

    assert saved_stages == ["transcribe", "extract", "critique"][:failed_pass]
    assert gemini.models.generate_content.call_count == failed_pass + 1


def test_checkpoint_failure_stops_pipeline(gemini):
    save_stage = Mock(side_effect=RuntimeError("Could not save output"))

    with pytest.raises(RuntimeError, match="Could not save output"):
        pipeline._run_pipeline("synthetic.wav", "test-model", save_stage)

    assert gemini.models.generate_content.call_count == 1


@pytest.mark.parametrize("creativity", ["conservative", "balanced", "creative", "exploratory"])
def test_creativity_changes_only_the_story_generation_prompt(gemini, creativity):
    pipeline.run_pipeline("synthetic.wav", creativity=creativity)
    calls = gemini.models.generate_content.call_args_list
    assert calls[0].kwargs["config"].system_instruction == get_transcribe_prompt(AUDIO_SECONDS)
    assert calls[1].kwargs["config"].system_instruction == EXTRACT_PROMPTS[creativity]
    assert calls[2].kwargs["config"].system_instruction == CRITIQUE_SYSTEM_PROMPT


def test_pipeline_uses_configured_creativity_default(gemini, monkeypatch):
    monkeypatch.setenv("DEFAULT_CREATIVITY", "creative")
    pipeline.run_pipeline("synthetic.wav")
    request = gemini.models.generate_content.call_args_list[1]
    assert request.kwargs["config"].system_instruction == EXTRACT_PROMPTS["creative"]


@pytest.mark.parametrize("origin,limit", [("discussed", 0.95), ("inferred", 0.84), ("proposed", 0.59)])
def test_discovery_provenance_reaches_critique_and_limits_confidence(gemini, pipeline_results, origin, limit):
    from services.worker.objects import DraftStory
    extraction = pipeline_results[1]
    extraction.stories[0] = DraftStory.model_validate(extraction.stories[0].model_dump() | {
        "origin": origin, "assumptions": [] if origin == "discussed" else ["This is an unconfirmed additional workflow"],
    })
    pipeline_results[2].reviews[0].confidence = 0.95
    score = pipeline_results[2].reviews[0].invest_score
    saved = {}
    pipeline._run_pipeline("synthetic.wav", "test-model", lambda stage, output: saved.update({stage: output}), creativity="exploratory")
    request = gemini.models.generate_content.call_args_list[2].kwargs
    assert "Generation creativity level: exploratory" in request["contents"]
    assert f'"origin": "{origin}"' in request["contents"]
    assert saved["critique"].reviews[0].confidence == limit
    assert saved["critique"].reviews[0].invest_score == score


def test_unknown_creativity_fails_before_upload_or_database(gemini, monkeypatch):
    engine = Mock(side_effect=AssertionError("Unexpected database connection"))
    monkeypatch.setattr(pipeline, "get_engine", engine)
    with pytest.raises(ValueError, match="Choose conservative"):
        pipeline.run_pipeline("synthetic.wav", creativity="unknown")
    gemini.files.upload.assert_not_called()
    engine.assert_not_called()


def test_transcript_pass_is_told_the_real_length_of_the_recording(gemini):
    pipeline._run_pipeline("synthetic.wav", "test-model")
    gemini.models.count_tokens.assert_called_once_with(
        model="test-model", contents=[gemini.files.upload.return_value],
    )
    prompt = gemini.models.generate_content.call_args_list[0].kwargs["config"].system_instruction
    assert prompt.startswith(TRANSCRIBE_SYSTEM_PROMPT)
    assert "exactly 2:15 long" in prompt


def test_unmeasurable_audio_still_transcribes_without_a_length(gemini, pipeline_results):
    gemini.models.count_tokens.side_effect = RuntimeError("Token counting unavailable")
    results = pipeline._run_pipeline("synthetic.wav", "test-model")
    assert results == pipeline_results
    prompt = gemini.models.generate_content.call_args_list[0].kwargs["config"].system_instruction
    assert prompt == TRANSCRIBE_SYSTEM_PROMPT


def test_a_stretched_timeline_is_squeezed_back_into_the_recording(gemini, gemini_responses):
    # Gemini has returned timelines 1.6x too long. Here the quote is at 2:00 of a 2:15 recording.
    gemini_responses[0].parsed.segments[0].start = "03:12"
    gemini_responses[0].parsed.segments[0].end = "03:36"
    gemini.models.generate_content.side_effect = gemini_responses
    transcript, extraction, _ = pipeline._run_pipeline("synthetic.wav", "test-model")
    segment = transcript.segments[0]
    assert segment.end_s <= AUDIO_SECONDS
    assert segment.start_s == pytest.approx(192 * AUDIO_SECONDS / 216, abs=0.1)
    # and the story's quote time follows the transcript, so it can't point past the end either
    assert extraction.stories[0].source.start_s == segment.start_s
    assert extraction.stories[0].source.end_s <= AUDIO_SECONDS


def test_story_quote_time_comes_from_the_transcript_not_the_models_numbers(gemini, gemini_responses):
    story = gemini_responses[1].parsed.stories[0]
    story.source.start_s, story.source.end_s = 999.0, 1004.0
    _, extraction, _ = pipeline._run_pipeline("synthetic.wav", "test-model")
    assert extraction.stories[0].source.start_s == 2.0
