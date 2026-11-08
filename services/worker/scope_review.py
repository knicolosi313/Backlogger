"""Keep explicit model findings consistent with the visible INVEST flags."""

from .objects import InvestFlag


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


def apply_scope_findings(extraction, critique, review_context, board_context=None):
    reviews = {review.index: review for review in critique.reviews}
    allowed_board_ids = {
        f"{item['source']}:{item['id']}" for item in (board_context or {}).get("existing_stories", [])
    } | {item["reference"] for item in review_context.get("board_matches", [])}
    criteria = {"Independent", "Negotiable", "Valuable", "Estimable", "Small", "Testable"}

    def flag(review, reference, reason):
        issue = f"Scope conflict with {reference}: {reason}"
        if not any(item.criterion == "Independent" and item.issue == issue for item in review.flags):
            review.flags.append(InvestFlag(criterion="Independent", issue=issue))
        failures = {item.criterion for item in review.flags} & criteria
        review.invest_score = min(review.invest_score, 6 - len(failures))
        review.confidence = min(review.confidence, 0.84)

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
