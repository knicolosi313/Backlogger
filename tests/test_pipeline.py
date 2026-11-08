"""Exercise the Gemini pipeline with a fake SDK client; no audio or API calls."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google import genai

from services.worker import pipeline
from services.worker.prompts.critique import CRITIQUE_SYSTEM_PROMPT
from services.worker.prompts.extract import EXTRACT_PROMPTS, EXTRACT_SYSTEM_PROMPT
from services.worker.prompts.transcribe import TRANSCRIBE_SYSTEM_PROMPT


def uploaded_file(state):
    return SimpleNamespace(name="files/synthetic-audio", state=SimpleNamespace(name=state))


@pytest.fixture
def gemini(monkeypatch, pipeline_results):
    client = Mock()
    client.files.upload.return_value = uploaded_file("ACTIVE")
    client.models.generate_content.side_effect = [
        SimpleNamespace(parsed=result) for result in pipeline_results
    ]
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
    expected_prompts = [TRANSCRIBE_SYSTEM_PROMPT, EXTRACT_SYSTEM_PROMPT, CRITIQUE_SYSTEM_PROMPT]
    for request, result, prompt in zip(calls, pipeline_results, expected_prompts):
        assert request.kwargs["model"] == "test-model"
        config = request.kwargs["config"]
        assert config.system_instruction == prompt
        assert config.response_schema is type(result)
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
def test_model_failure_stops_later_passes(gemini, pipeline_results, failed_pass):
    gemini.models.generate_content.side_effect = [
        *[SimpleNamespace(parsed=result) for result in pipeline_results[:failed_pass]],
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
    assert calls[0].kwargs["config"].system_instruction == TRANSCRIBE_SYSTEM_PROMPT
    assert calls[1].kwargs["config"].system_instruction == EXTRACT_PROMPTS[creativity]
    assert calls[2].kwargs["config"].system_instruction == CRITIQUE_SYSTEM_PROMPT


def test_pipeline_uses_configured_creativity_default(gemini, monkeypatch):
    monkeypatch.setenv("DEFAULT_CREATIVITY", "creative")
    pipeline.run_pipeline("synthetic.wav")
    request = gemini.models.generate_content.call_args_list[1]
    assert request.kwargs["config"].system_instruction == EXTRACT_PROMPTS["creative"]


def test_unknown_creativity_fails_before_upload_or_database(gemini, monkeypatch):
    engine = Mock(side_effect=AssertionError("Unexpected database connection"))
    monkeypatch.setattr(pipeline, "get_engine", engine)
    with pytest.raises(ValueError, match="Choose conservative"):
        pipeline.run_pipeline("synthetic.wav", creativity="unknown")
    gemini.files.upload.assert_not_called()
    engine.assert_not_called()
