"""Keep explicit model findings consistent with the visible INVEST flags."""

import json

from .board_context import content_text
from .objects import InvestFlag, StoryRelationship


def missing_scope_checks(critique, context):
    """Require an explicit scope decision for each embedding candidate supplied."""
    pairs, board_checks = set(), set()
    for review in critique.reviews:
        for relationship in review.relationships:
            if relationship.other_story_index is not None:
                pairs.add(tuple(sorted((review.index, relationship.other_story_index))))
            elif relationship.existing_story_id:
                board_checks.add((review.index, relationship.existing_story_id))
    missing = []
    for pair in context.get("draft_pairs", []):
        if (pair["left_index"], pair["right_index"]) not in pairs:
            missing.append({"story_index": pair["left_index"], "other_story_index": pair["right_index"]})
    for match in context.get("board_matches", []):
        if (match["draft_index"], match["reference"]) not in board_checks:
            missing.append({"story_index": match["draft_index"], "existing_story_id": match["reference"]})
    return missing


def scope_followup_input(extraction, review_context, missing):
    """The omitted comparisons with both stories' text, so they can be asked about on their own."""
    board_text = {match["reference"]: match.get("story") for match in review_context.get("board_matches", [])}
    items = []
    for item in missing:
        index, other = item["story_index"], item.get("other_story_index")
        entry = {"story_index": index, "story": content_text(extraction.stories[index].model_dump())}
        if other is not None:
            entry.update(other_story_index=other, other_story=content_text(extraction.stories[other].model_dump()))
        else:
            entry.update(existing_story_id=item["existing_story_id"], existing_story=board_text.get(item["existing_story_id"]))
        items.append(entry)
    return "Compare these story pairs:\n" + json.dumps(items, ensure_ascii=False)


def merge_scope_comparisons(critique, comparisons):
    """Record follow-up decisions on the story they are about. apply_scope_findings validates them."""
    reviews = {review.index: review for review in critique.reviews}
    for item in comparisons.comparisons:
        review = reviews.get(item.story_index)
        # exactly one target, or it can't be told what was compared
        if review is None or (item.other_story_index is None) == (item.existing_story_id is None):
            continue
        target = (item.other_story_index, item.existing_story_id)
        if any((r.other_story_index, r.existing_story_id) == target for r in review.relationships):
            continue
        review.relationships.append(StoryRelationship(
            other_story_index=item.other_story_index, existing_story_id=item.existing_story_id,
            relationship=item.relationship, reason=item.reason,
        ))


_INVEST_CRITERIA = {"Independent", "Negotiable", "Valuable", "Estimable", "Small", "Testable"}


def _flag_independent(review, issue):
    """Add an Independent flag once and keep the score and confidence consistent with it."""
    if not any(item.criterion == "Independent" and item.issue == issue for item in review.flags):
        review.flags.append(InvestFlag(criterion="Independent", issue=issue))
    failures = {item.criterion for item in review.flags} & _INVEST_CRITERIA
    review.invest_score = min(review.invest_score, 6 - len(failures))
    review.confidence = min(review.confidence, 0.84)


def flag_unassessed_scope(extraction, critique, missing):
    """Mark stories whose required scope comparisons the review never made.

    The model sometimes skips comparisons even after being asked again. That must not
    pass silently as "independent", but it also shouldn't throw away a whole meeting.
    The flag fails the Independent check, so these stories are never auto-imported and a
    person is told to check them for overlap.
    """
    reviews = {review.index: review for review in critique.reviews}
    count = len(extraction.stories)
    for item in missing:
        left, other = item["story_index"], item.get("other_story_index")
        # a missing draft pair affects both drafts, a missing board match only the draft
        sides = [(left, other), (other, left)] if other is not None else [(left, item["existing_story_id"])]
        for index, against in sides:
            review = reviews.get(index)
            if review is None or not 0 <= index < count:
                continue
            if isinstance(against, int):
                if not 0 <= against < count:
                    continue
                against = f'draft #{against} "{extraction.stories[against].title}"'
            _flag_independent(
                review,
                f"Scope not compared with {against}: the automated review did not assess this overlap. "
                "Check it manually before accepting.",
            )


def apply_scope_findings(extraction, critique, review_context, board_context=None):
    reviews = {review.index: review for review in critique.reviews}
    allowed_board_ids = {
        f"{item['source']}:{item['id']}" for item in (board_context or {}).get("existing_stories", [])
    } | {item["reference"] for item in review_context.get("board_matches", [])}

    def flag(review, reference, reason):
        _flag_independent(review, f"Scope conflict with {reference}: {reason}")

    for review in critique.reviews:
        if not 0 <= review.index < len(extraction.stories):
            continue
        valid = []
        for relationship in review.relationships:
            other = relationship.other_story_index
            existing_id = relationship.existing_story_id
            if other is not None:
                if existing_id is not None or other == review.index or other not in reviews or not 0 <= other < len(extraction.stories):
                    continue
                reference = f'draft #{other} "{extraction.stories[other].title}"'
            elif existing_id in allowed_board_ids:
                reference = existing_id
            else:
                continue
            valid.append(relationship)
            if relationship.relationship == "independent":
                continue
            flag(review, reference, relationship.reason)
            if other is not None and relationship.relationship != "depends_on":
                flag(reviews[other], f'draft #{review.index} "{extraction.stories[review.index].title}"', relationship.reason)
        review.relationships = valid
