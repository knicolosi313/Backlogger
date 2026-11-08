# Story creativity levels

The extraction pass has four system prompts in
`services/worker/prompts/extract.py`. They share grounding, evidence, schema,
and story-splitting rules, with separate instructions for each level:

| Level | Behavior |
| --- | --- |
| `conservative` | Minimal interpretation, explicit requests only, missing roles/benefits marked "not specified" with clarification questions |
| `balanced` | Clear practical stories, sensible grouping and splitting, strongly implied roles/benefits flagged for confirmation |
| `creative` | More polished phrasing and alternative grouping of discussed work; uncertain interpretations and possible enhancements stay flagged or in open questions |
| `exploratory` | Consider different decompositions, return one coherent grounded story set, put unconfirmed alternatives in separately labeled open questions |

Creativity never permits invented requirements, estimates, timestamps, quotes,
or meeting decisions. Only the extraction prompt changes; transcription and
INVEST critique keep their existing prompts and generation temperature.

## Acceptance checklists and solution tasks

All four modes now target 2–6 distinct acceptance criteria per story. Each item
is an independently testable outcome, preferably in Given/When/Then form. The
model must not pad the list with duplicate or unsupported behavior: insufficient
meeting detail should result in clarification questions instead.

`DraftStory` also includes `solution_tasks`, a separate list of proposed steps
for delivering the agreed behavior. Criteria describe the result; tasks describe
the work. Tasks may derive high-level implementation steps, but cannot invent
technologies, architecture, estimates, or additional scope. Undecided choices
remain open questions. Existing stored stories remain readable because this
field defaults to an empty list; no database migration is required.

Trello exports create native Acceptance Criteria and Solution Tasks (proposed)
checklists. Jira descriptions show separate checkbox-style lists; these are
visual list items rather than interactive Jira checklist controls.

## Splitting large stories

All modes instruct Gemini to inspect drafts for excessive scope and attempt to
return multiple smaller stories for independently deliverable user outcomes.
Each child must have supported behavior, acceptance criteria, and source evidence.
Related children can share an epic. Avoid duplicate parent/child stories and
mechanical splits into UI/backend/database tasks. If the transcript does not
support a useful split, keep the grounded story and ask a clarification question.
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

The output schema remains `ExtractionResult`: exploratory alternatives go into
`open_questions`, not an unsupported extra JSON field. Selection tests use mocked
Gemini calls; they verify which prompt is sent, not the quality of live outputs.
