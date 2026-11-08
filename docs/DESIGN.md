# Backlogger: Design

HACK.COMS hackathon project, Nov 2026.

## Idea

A Chrome extension records a browser meeting next to a Trello board. A backend uses Gemini to transcribe it, pull out candidate user stories, and critique them against INVEST. A human reviews each draft (linked to the exact moment in the meeting) and approves it into Trello. A web app shows meeting history and board quality trends.

**Problem:** meeting decisions rarely become good, traceable backlog items. Writing stories by hand is slow, and it's hard to trust AI-written ones.

**Key ideas:** every story cites a timestamp, quality is checked before import, a human approves everything, and trends are tracked across meetings.

## Flow

1. Connect Trello/Jira/GitHub (OAuth).
2. Start recording from the extension (explicit click, consent reminder, visible indicator).
3. Stop. The extension uploads tab and mic audio to the API.
4. A worker runs: transcribe, extract candidates, draft stories, INVEST critique.
5. Review in the extension: Accept / Request Edits / Edit Manually / Reject.
6. Import accepted stories to a Trello Triage list (AI Draft label, source link, no duplicates).
7. View history and analytics in the web app.

**Story states:** Draft → (Needs changes) → Accepted → Imported, or Rejected (restorable).

**Confidence vs quality** are separate. Confidence is how well the story is grounded in the meeting and the AI worker's confidence in its own accuracy. Quality is how the story scores against INVEST. Store and show them separately.

## Architecture


| Piece        | Tech                                  |
| -------------- | --------------------------------------- |
| Extension    | WXT + React + TypeScript              |
| Web app      | React + shadcn/ui                     |
| API + worker | FastAPI (Python)                      |
| Model        | Gemini API                            |
| DB           | PostgreSQL + Tiger Data / TimescaleDB |
| Boards       | Trello (MVP), GitHub/Jira (stretch)   |
| Deploy       | Docker Compose                        |

```
extension → API → media storage → worker → Gemini → Postgres → review API → board adapter → Trello
                                                       web app reads the same API
```

Rules: media lives in object storage or a local volume (not Postgres). Processing is async with job IDs and status. Keep the original AI output and every edit. Imports use an idempotency key so retries never duplicate cards.

## UI

**Extension tabs:**

- **Record:** start/stop, consent note, recent meetings, auto-import toggle.
- **Drafts:** story list with confidence badges. Detail shows the story, acceptance criteria, priority, points, INVEST check, "jump to moment", AI critique, open questions, and the four actions.
- **Analytics:** board health, counts, stories needing attention, and an "Open Full Analytics" button.
- **Settings:** creativity level, model, auto-import threshold and target list, consent reminder, Trello connection.

**Web app pages:** Boards (overview, history, analytics, drafts, settings), Meetings/History (transcript + playback), Integrations & Settings.

## AI pipeline

Five stages: **Transcribe → Extract → Draft → Critique → Human review.** Save each stage's output so a failure later (e.g. critique) doesn't lose earlier work.

- INVEST: score each of the six checks pass/warn/fail with an explanation and a fix.
- Story output: title, persona, goal, benefit, acceptance criteria, priority, points, evidence segments, confidence, INVEST checks, quality score, open questions, unsupported assumptions.
- Creativity levels: Conservative, **Balanced (default)**, Creative (extras flagged), Exploratory (alternatives in a separate section). Creativity never means inventing facts.
- Guardrails: treat transcripts as untrusted (ignore instructions in them), require timestamp evidence for claims, and send missing evidence to review.

## Data

Tables: users/workspaces, boards/integrations, meetings, transcript_segments, meeting_frames, stories, story_revisions, story_evidence, quality_checks/open_questions, imports, analytics_events.

Metrics:

- **Acceptance rate** = accepted ÷ reviewed (pending excluded).
- **Quality trend** = time-bucketed average quality.
- **INVEST issue counts** by dimension.
- **Draft outcome mix:** accepted / edited / rejected / merged-split.
- **Board health** = aggregate quality (formula TBD).

TimescaleDB hypertables hold `analytics_events`; continuous aggregates drive the trend charts.

## API (rough)

```
POST /api/meetings                       create meeting / upload session
POST /api/meetings/{id}/media            upload media
POST /api/meetings/{id}/process          start processing
GET  /api/meetings/{id}                  status
GET  /api/meetings/{id}/transcript       transcript
GET  /api/boards/{id}/stories            drafts
PATCH /api/stories/{id}                  manual edit
POST /api/stories/{id}/request-edits     AI revision
POST /api/stories/{id}/decision          accept / reject
POST /api/boards/{id}/imports            idempotent import
GET  /api/boards/{id}/analytics          health + trends
```

## Board adapters

Interface: `connect`, `list_boards`, `list_columns`, `create_card(story, target, idempotency_key)`, `find_import_by_key`, `add_labels`, `attach_evidence_link`, optional `sync_card`.

Import is manual by default. Auto-import is opt-in, goes only to Triage with an AI Draft label above a confidence threshold, and checks for duplicates first. Reprocessing a meeting never overwrites a card a human edited.

## Privacy and safety

- No covert recording: explicit start, visible indicator, consent reminder. We can't guarantee legal consent, so use only synthetic or consenting meetings for the demo.
- Tokens are encrypted at rest and stay server-side. TLS. Upload size and type limits. Configurable retention.
- Never auto-publish low-evidence stories.
- Secrets live in `.env` (ignored); only `.env.example` is committed.

## MVP plan

1. **Foundation:** extension shell, FastAPI, Postgres schema, Docker Compose, Trello connect.
2. **Recording:** mic capture, upload, status.
3. **AI slice:** one meeting → transcript → 1 or more grounded stories → INVEST critiques.
4. **Review + import:** the four actions, then import stories to Trello with duplicate protection.
5. **Web app:** meeting history, one board overview, simple metrics.

**Fallback:** if live capture is flaky, upload a pre-recorded meeting instead.

**Stretch:** live draft previews, GitHub/Jira adapters, semantic duplicate detection, slide/frame analysis.

## 90-second demo

1. Show Trello with the extension and a connected board.
2. Play a pre-recorded meeting, show processing status.
3. Open a story: evidence timestamp, criteria, INVEST warning.
4. Request or make an edit, accept, import to Triage.
5. Refresh Trello: card, label, source link, no duplicate on re-import.
6. Open Full Analytics in the web app.

**Prize targets:** Gemini (multi-stage grounded pipeline), Tiger Data (event pipeline, trends, heatmap), Open Source (Docker Compose, adapter interface, sample data).

## Definition of done

- [ ]  Capture or upload a meeting with clear status
- [ ]  At least one story with criteria, critique, and timestamps
- [ ]  Review actions persist (one revision and one rejection)
- [ ]  An accepted story imports to Trello once, with no duplicate on retry
- [ ]  Web app shows history and metrics from stored data
- [ ]  A test shows an INVEST issue and its reviewer fix
- [ ]  `docker compose up` works; README covers env setup
- [ ]  Clear what's working vs. illustrative

## Open questions

- How is board health calculated, and what's the INVEST quality rubric?
- How is confidence produced (model self-report or an evidence-coverage heuristic)?
- Does "AI Time Saved" stay? It has no calculation yet.
- Which single meeting platform for the MVP?
- Auth: real accounts or single-user hackathon mode?
- Queue and object store: DB-backed + local volume, or Redis + MinIO?
- Demo Trello board and API keys.

**Main risk:** Chrome tab+mic capture reliability. Prototype it first.
