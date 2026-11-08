# Story creativity levels

The extraction pass has four system prompts in
`services/worker/prompts/extract.py`. They share provenance, evidence, schema,
and story-splitting rules, with separate instructions for each level:

| Level | Behavior |
| --- | --- |
| `conservative` | Minimal interpretation, explicit requests only, missing roles/benefits marked "not specified" with clarification questions |
| `balanced` | Stated requests plus necessary or strongly implied workflows; distinct operations become separate stories, with assumptions flagged |
| `creative` | Add several useful adjacent story proposals, such as missing recovery flows, accessibility, or product-specific enhancements |
| `exploratory` | Maximize discovery across journeys, edge cases, latent needs, power-user flows, and future extensions; return many concrete proposals directly as stories |

Exploratory now treats a concrete meeting as a seed for a broad backlog, rather
than a closed list of agreed requirements. It actively seeks 15-30+ useful,
nonoverlapping candidates when the product supports that breadth, and performs
another discovery sweep if it stops too early. This is guidance rather than an
enforced count: empty meetings, irrelevant filler, and duplicates are excluded.
Creative aims for several relevant extensions (roughly 3-8 where useful) rather
than one cosmetic suggestion. Mode selection changes the extraction prompt;
transcription and generation temperature remain unchanged. Critique also receives
the selected level and evaluates explicit proposals as hypotheses.

## Labeling proposed scope

`DraftStory.origin` distinguishes `discussed`, `inferred`, and `proposed` scope.
`assumptions` records unconfirmed roles, behavior, or choices. Inferred/proposed
stories always require clarification, enforced by model validation. The extension
shows **Inferred story** or **AI proposal** and the assumptions in story detail.
Conservative returns discussed scope only; balanced can infer strong implications;
creative and exploratory can propose new behavior and acceptance criteria.

Proposals appear in `stories`, not only in questions. Each has a real source quote
that inspired the idea, proposed criteria/tasks, and a linked confirmation question.
The quote does not imply the team agreed to all proposed behavior. Invented quotes,
timestamps, estimates, decisions, and business facts remain disallowed.

Critique does not reject a useful proposal merely because it was not explicitly
discussed. It still checks relevance, duplication/embedding scope conflicts,
INVEST quality, and any proposal presented dishonestly as fact. Confidence is
capped at 0.84 for inferred scope and 0.59 for proposed scope; INVEST scores still
describe story quality. Existing drafts default to discussed scope with no
assumptions, so no database migration is required. Existing reviews are not rerun
automatically when prompts change.

## Acceptance checklists and solution tasks

All four modes now target 2–6 distinct acceptance criteria per story. Each item
is an independently testable outcome, preferably in Given/When/Then form. The
model must not pad the list with duplicates. Discussed scope stays faithful to
the meeting; inferred/proposed criteria describe clearly labeled target behavior.

`DraftStory` also includes `solution_tasks`, a separate list of proposed steps
for delivering the story's discussed, inferred, or proposed behavior. Criteria
describe the result; tasks describe the work. High-level implementation steps may
be proposed, but invented technology choices, architecture, or estimates must not
be presented as facts. Undecided choices remain questions/assumptions. Existing
stored stories remain readable because this
field defaults to an empty list; no database migration is required.

Trello exports create native Acceptance Criteria and Solution Tasks (proposed)
checklists. Jira descriptions show separate checkbox-style lists; these are
visual list items rather than interactive Jira checklist controls.

## Splitting large stories

All modes instruct Gemini to inspect drafts for excessive scope and attempt to
return multiple smaller stories for independently deliverable user outcomes.
Each child must have distinct behavior, acceptance criteria, and source evidence.
Related children can share an epic. Avoid duplicate parent/child stories and
mechanical splits into UI/backend/database tasks. If the transcript does not
support a useful split, keep the discussed story and ask a clarification question,
or in exploratory mode propose a sensible breakdown with explicit assumptions.
Do not copy a parent's estimate onto every child.

The critique pass also proposes concrete, transcript-supported splits when
needed, but still returns exactly one review per input story with its original
index. Its suggestions do not automatically rewrite stories after extraction.
Prompt instructions guide Gemini; actual splitting quality still requires
evaluation on representative meetings and human review.

## Select a level

Send an optional JSON body when starting a meeting:

```text
POST /api/meetings/{meeting_id}/process
```

```json
{"creativity": "creative"}
```

With no body, an empty body object, or a null creativity value, the API uses
`DEFAULT_CREATIVITY` from the environment (normally loaded from `.env`), then
falls back to `balanced`. Invalid request levels return 422; an invalid configured
default returns 503 without starting the meeting. Existing no-body calls work.

Python callers can select a level directly:

```python
run_pipeline("meeting.wav", meeting_id=meeting_id, creativity="exploratory")
```

Direct callers that omit creativity use the process environment's
`DEFAULT_CREATIVITY`, or `balanced`. For prompt-only callers, use
`get_extract_prompt(level)`. The existing `EXTRACT_SYSTEM_PROMPT` name remains
an alias for the balanced prompt.

The output envelope remains `ExtractionResult`: discovered proposals are real
drafts in `stories`, while confirmation questions go in `open_questions`. Tests
cover provenance validation, confidence limits, prompt selection, and visible
proposal labels. Representative live outputs remain useful for evaluating breadth
and relevance; no prompt guarantees identical counts across runs.
