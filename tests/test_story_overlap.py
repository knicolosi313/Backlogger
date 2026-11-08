"""Embedding candidates and semantic scope findings are separate decisions."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services.worker import board_context, pipeline
from services.worker.objects import CritiqueResult, ExtractionResult, StoryRelationship
from services.worker.scope_review import apply_scope_findings
from services.worker.scope_review import flag_unassessed_scope, missing_scope_checks


@pytest.fixture
def calculator_case(pipeline_results):
    draft = pipeline_results[1].stories[0]
    broad = draft.model_copy(update={
        "title": "Implement all calculator functions", "i_want": "addition, subtraction, multiplication, and division",
        "acceptance_criteria": ["Add two values", "Subtract two values", "Multiply two values", "Divide two values"],
        "solution_tasks": [],
    }, deep=True)
    narrow = draft.model_copy(update={
        "title": "Implement multiplication", "i_want": "to multiply two values", "acceptance_criteria": ["Return the product of two values"],
        "solution_tasks": [],
    }, deep=True)
    extraction = ExtractionResult(stories=[broad, narrow], open_questions=[])
    base = pipeline_results[2].reviews[0]
    critique = CritiqueResult(reviews=[base.model_copy(update={
        "index": index, "flags": [], "relationships": [], "invest_score": 6, "confidence": 0.95,
    }, deep=True) for index in range(2)])
    return extraction, critique


def test_embeddings_compare_new_stories_before_they_have_database_ids(calculator_case, monkeypatch):
    extraction, _ = calculator_case
    monkeypatch.setenv("STORY_EMBEDDINGS_ENABLED", "true")
    embed = Mock(return_value=[[1.0, 0.0], [0.96, 0.28]])
    monkeypatch.setattr(board_context, "embed_texts", embed)
    result = board_context.build_review_context(extraction.stories)
    assert result["retrieval_method"] == "embedding"
    assert result["draft_pairs"] == [{"left_index": 0, "right_index": 1, "similarity": 0.96}]
    assert result["board_matches"] == []
    texts = embed.call_args.args[0]
    assert "all calculator functions" in texts[0]
    assert "Return the product" in texts[1]


def test_confirmed_containment_flags_both_stories_and_updates_scores(calculator_case):
    extraction, critique = calculator_case
    critique.reviews[0].relationships = [StoryRelationship(
        other_story_index=1, relationship="contains", reason="The broad story already includes multiplication.",
    )]
    apply_scope_findings(extraction, critique, {})
    apply_scope_findings(extraction, critique, {})  # Retry must not duplicate the same finding.
    for review in critique.reviews:
        assert len(review.flags) == 1
        assert review.flags[0].criterion == "Independent"
        assert "multiplication" in review.flags[0].issue
        assert review.invest_score == 5
        assert review.confidence == 0.84
        assert not review.recommend_reject


def test_similar_embeddings_do_not_fail_independence_without_scope_overlap(calculator_case):
    extraction, critique = calculator_case
    critique.reviews[0].relationships = [StoryRelationship(
        other_story_index=1, relationship="independent", reason="Addition and multiplication deliver different outcomes.",
    )]
    apply_scope_findings(extraction, critique, {
        "draft_pairs": [{"left_index": 0, "right_index": 1, "similarity": 0.99}],
    })
    assert all(not review.flags and review.invest_score == 6 for review in critique.reviews)


def test_existing_board_match_adds_a_visible_independence_flag(calculator_case):
    extraction, critique = calculator_case
    critique.reviews[1].relationships = [StoryRelationship(
        existing_story_id="jira:CALC-42", relationship="contained_by", reason="CALC-42 already implements multiplication.",
    )]
    apply_scope_findings(extraction, critique, {"board_matches": [{"reference": "jira:CALC-42"}]})
    assert "jira:CALC-42" in critique.reviews[1].flags[0].issue
    assert critique.reviews[0].flags == []


@pytest.mark.parametrize("relationship", [
    {"other_story_index": 0}, {"other_story_index": 99},
    {"existing_story_id": "local:invented"}, {"other_story_index": 1, "existing_story_id": "jira:CALC-42"},
])
def test_unknown_or_invalid_model_references_cannot_create_conflict_flags(calculator_case, relationship):
    extraction, critique = calculator_case
    critique.reviews[0].relationships = [StoryRelationship(**relationship, relationship="overlap", reason="Alleged overlap")]
    apply_scope_findings(extraction, critique, {})
    assert critique.reviews[0].relationships == []
    assert all(not review.flags for review in critique.reviews)


def test_a_dependency_only_flags_the_dependent_story(calculator_case):
    extraction, critique = calculator_case
    critique.reviews[1].relationships = [StoryRelationship(
        other_story_index=0, relationship="depends_on", reason="This story cannot be delivered before the other.",
    )]
    apply_scope_findings(extraction, critique, {})
    assert not critique.reviews[0].flags
    assert critique.reviews[1].flags[0].criterion == "Independent"


def test_embedding_failure_does_not_invent_overlap(calculator_case, monkeypatch):
    extraction, critique = calculator_case
    monkeypatch.setenv("STORY_EMBEDDINGS_ENABLED", "true")
    monkeypatch.setattr(board_context, "embed_texts", Mock(side_effect=RuntimeError("Unavailable")))
    context = board_context.build_review_context(extraction.stories)
    assert context["retrieval_method"] == "unavailable"
    apply_scope_findings(extraction, critique, context)
    assert all(not review.flags for review in critique.reviews)


def test_each_neighbor_requires_a_scope_decision_even_if_it_is_independent(calculator_case):
    _, critique = calculator_case
    context = {
        "draft_pairs": [{"left_index": 0, "right_index": 1}],
        "board_matches": [{"draft_index": 0, "reference": "trello:card1"}],
    }
    assert len(missing_scope_checks(critique, context)) == 2
    critique.reviews[1].relationships = [StoryRelationship(
        other_story_index=0, relationship="independent", reason="Different user outcomes.",
    )]
    assert missing_scope_checks(critique, context) == [{"story_index": 0, "existing_story_id": "trello:card1"}]


def test_an_unassessed_board_match_flags_only_the_draft_and_unassessed_pairs_flag_both(calculator_case):
    extraction, critique = calculator_case
    flag_unassessed_scope(extraction, critique, [
        {"story_index": 0, "existing_story_id": "trello:card1"},
        {"story_index": 0, "other_story_index": 1},
        {"story_index": 9, "other_story_index": 1},  # a review that doesn't exist is skipped, not a crash
    ])
    first, second = critique.reviews
    assert [flag.issue.split(":")[0] for flag in first.flags] == [
        "Scope not compared with trello", "Scope not compared with draft #1 \"Implement multiplication\"",
    ]
    assert len(second.flags) == 1  # only the real pair; the entries with a bad index are skipped
    assert first.confidence <= 0.84 and first.invest_score < 6


@pytest.mark.parametrize("complete_retry", [True, False])
def test_pipeline_retries_missing_scope_checks_instead_of_silently_passing_them(
    calculator_case, pipeline_results, gemini_responses, monkeypatch, complete_retry,
):
    from google import genai
    extraction, critique = calculator_case
    retried = critique.model_copy(deep=True)
    if complete_retry:
        retried.reviews[0].relationships = [StoryRelationship(
            other_story_index=1, relationship="contains", reason="Multiplication is already in the broad scope.",
        )]
    client = Mock()
    client.files.upload.return_value = SimpleNamespace(state=SimpleNamespace(name="ACTIVE"))
    client.models.generate_content.side_effect = [SimpleNamespace(parsed=value) for value in (gemini_responses[0].parsed, extraction, critique, retried)]
    monkeypatch.setattr(genai, "Client", Mock(return_value=client))
    get_context = lambda result: {"draft_pairs": [{"left_index": 0, "right_index": 1, "similarity": 0.96}]}
    saved = {}
    call = lambda: pipeline._run_pipeline(
        "synthetic.wav", "test-model", lambda stage, output: saved.update({stage: output}), review_context=get_context,
    )
    if complete_retry:
        call()
        assert saved["critique"].reviews[1].flags[0].criterion == "Independent"
    else:
        # still incomplete after the retry: keep the stories, but never pass the pair off as independent
        call()
        for review in saved["critique"].reviews:
            assert review.flags[0].criterion == "Independent"
            assert "Scope not compared with" in review.flags[0].issue
            assert review.confidence <= 0.84
        assert saved["critique"].reviews[0].invest_score < 6
    assert "previous review omitted" in client.models.generate_content.call_args.kwargs["contents"]


def test_pipeline_supplies_embedding_pairs_and_saves_consistent_flags(
    calculator_case, pipeline_results, gemini_responses, monkeypatch,
):
    from google import genai
    extraction, critique = calculator_case
    critique.reviews[0].relationships = [StoryRelationship(
        other_story_index=1, relationship="contains", reason="All operations includes the multiplication story.",
    )]
    monkeypatch.setenv("STORY_EMBEDDINGS_ENABLED", "true")
    monkeypatch.setattr(board_context, "embed_texts", Mock(return_value=[[1.0, 0.0], [0.96, 0.28]]))
    client = Mock()
    client.files.upload.return_value = SimpleNamespace(state=SimpleNamespace(name="ACTIVE"))
    client.models.generate_content.side_effect = [SimpleNamespace(parsed=value) for value in (gemini_responses[0].parsed, extraction, critique)]
    monkeypatch.setattr(genai, "Client", Mock(return_value=client))
    saved = {}
    _, _, result = pipeline._run_pipeline("synthetic.wav", "test-model", lambda stage, output: saved.update({stage: output.model_dump()}))
    review_request = client.models.generate_content.call_args_list[2].kwargs
    assert '"left_index": 0' in review_request["contents"]
    assert '"right_index": 1' in review_request["contents"]
    assert '"similarity": 0.96' in review_request["contents"]
    assert "Implement all calculator functions" in review_request["contents"]
    assert "Implement multiplication" in review_request["contents"]
    assert all(review.flags[0].criterion == "Independent" for review in result.reviews)
    assert saved["critique"]["reviews"][1]["invest_score"] == 5
