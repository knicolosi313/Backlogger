"""Validate the structured output contracts used by Gemini."""

import pytest
from pydantic import ValidationError

from services.worker.objects import DraftStory, InvestFlag, StoryReview


def test_pipeline_results_round_trip_as_json(pipeline_results):
    for result in pipeline_results:
        assert type(result).model_validate_json(result.model_dump_json()) == result


def test_story_requires_evidence(pipeline_results):
    story = pipeline_results[1].stories[0].model_dump()
    del story["source"]

    with pytest.raises(ValidationError) as error:
        DraftStory.model_validate(story)

    assert error.value.errors()[0]["loc"] == ("source",)


def test_story_rejects_unknown_priority(pipeline_results):
    story = pipeline_results[1].stories[0].model_dump()
    story["priority"] = "urgent"

    with pytest.raises(ValidationError):
        DraftStory.model_validate(story)


@pytest.mark.parametrize("field,value", [
    ("invest_score", -1), ("invest_score", 7),
    ("confidence", -0.01), ("confidence", 1.01),
])
def test_review_rejects_out_of_range_scores(pipeline_results, field, value):
    review = pipeline_results[2].reviews[0].model_dump()
    review[field] = value

    with pytest.raises(ValidationError):
        StoryReview.model_validate(review)


def test_review_accepts_score_boundaries(pipeline_results):
    review = pipeline_results[2].reviews[0].model_dump()
    for score, confidence in [(0, 0.0), (6, 1.0)]:
        validated = StoryReview.model_validate({
            **review, "invest_score": score, "confidence": confidence,
        })
        assert validated.invest_score == score
        assert validated.confidence == confidence


def test_invest_flag_rejects_unknown_criterion():
    with pytest.raises(ValidationError):
        InvestFlag(criterion="Popularity", issue="Not an INVEST criterion")


def test_legacy_story_without_solution_tasks_still_loads(pipeline_results):
    data = pipeline_results[1].stories[0].model_dump()
    data.pop("solution_tasks")
    data["acceptance_criteria"] = ["Existing single criterion"]
    story = DraftStory.model_validate(data)
    assert story.solution_tasks == []
    assert story.acceptance_criteria == ["Existing single criterion"]


def test_solution_tasks_are_preserved_in_story_json(pipeline_results):
    story = pipeline_results[1].stories[0]
    restored = DraftStory.model_validate_json(story.model_dump_json())
    assert len(restored.acceptance_criteria) == 2
    assert restored.solution_tasks == ["Add the password reset request flow", "Implement reset email delivery"]


def test_existing_drafts_load_without_discovery_metadata(pipeline_results):
    data = pipeline_results[1].stories[0].model_dump(exclude={"origin", "assumptions"})
    restored = DraftStory.model_validate(data)
    assert restored.origin == "discussed"
    assert restored.assumptions == []


@pytest.mark.parametrize("origin", ["inferred", "proposed"])
def test_unconfirmed_discovery_stories_cannot_claim_no_clarification_is_needed(pipeline_results, origin):
    data = pipeline_results[1].stories[0].model_dump() | {
        "origin": origin, "needs_clarification": False, "assumptions": ["Users would benefit from recovery reminders"],
    }
    story = DraftStory.model_validate(data)
    assert story.needs_clarification is True
    assert DraftStory.model_validate_json(story.model_dump_json()).assumptions == story.assumptions
