# Backlogger

> Your meeting ends, and your backlog is already drafted, checked, and linked back to the exact moment it was discussed.

A Chrome extension records a browser meeting beside a Trello board. Gemini turns it into evidence-linked user stories, checked against INVEST. A human reviews them, then approves them into Trello. A web app tracks meeting history and backlog quality.

**Status:** hackathon concept.

Full design: [docs/DESIGN.md](docs/DESIGN.md)
AI usage disclosure (hackathon requirement): [docs/AI_USAGE.md](docs/AI_USAGE.md)

Policy drafts: [Privacy Policy](docs/legal/PRIVACY_POLICY.md) and
[Terms of Service](docs/legal/TERMS_OF_SERVICE.md).
See [publishing and operator review](docs/legal/README.md) before sharing them.

Database setup: [infra/db/README.md](infra/db/README.md)

## Mockups

Design concepts for the companion dashboard and Chrome extension. Screen content
and metrics are illustrative.

### Analytics dashboard

![Companion dashboard with board overview, story quality trends, draft outcomes, and recent meetings](docs/mockups/analytics-dashboard.png)

### Meeting recording

![Chrome extension recording panel with meeting platform selection and consent reminder](docs/mockups/extension-recording.png)

### Draft review

![Chrome extension draft review with user stories, acceptance criteria, and review actions](docs/mockups/extension-draft-review.png)

### Extension analytics

![Chrome extension analytics panel showing board quality and stories needing attention](docs/mockups/extension-analytics.png)

### Extension settings

![Chrome extension settings for AI configuration, import preferences, recording, and integrations](docs/mockups/extension-settings.png)

## Backend development

Connect a Trello account: [docs/TRELLO_CONNECT.md](docs/TRELLO_CONNECT.md).
Connect Jira Cloud and export stories: [docs/JIRA_CLOUD.md](docs/JIRA_CLOUD.md).

The Python backend uses SQLModel with Psycopg 3 to access TigerData. Copy
`.env.example` to `.env` and set `DATABASE_URL` and `GEMINI_API_KEY`, then run:

```powershell
uv venv --python 3.13 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
./.venv/Scripts/python.exe -m uvicorn services.api.server:app --reload
```

Check database connectivity at `http://127.0.0.1:8000/health/db`. SQLModel table
mappings are in `services/models.py`; session handling is in `services/db.py`.
Apply the SQL migrations before using the API. See the database setup guide for
queries, pipeline checkpoints, and test commands.

API routes and an upload-to-review walkthrough: [services/api/README.md](services/api/README.md).
Interactive API documentation: `http://127.0.0.1:8000/docs`.

Trello export configuration, request examples, and retry behavior:
[docs/TRELLO_EXPORTS.md](docs/TRELLO_EXPORTS.md).

Frontend board/list selection and export-status endpoints:
[docs/FRONTEND_API.md](docs/FRONTEND_API.md).

Story-generation creativity levels and splitting rules:
[docs/STORY_CREATIVITY.md](docs/STORY_CREATIVITY.md).

## Draft outcome analytics

The board analytics endpoint (`GET /api/boards/{id}/analytics`) includes
`draft_outcome_counts` and `draft_outcome_percentages` for `accepted`, `edited`,
and `rejected`. Each story belongs to one outcome: rejected takes precedence,
then a human revision makes it edited, otherwise accepted/imported means accepted
as-is. Untouched draft/needs-changes stories are excluded. Edited stories awaiting
a final decision count as edited. Multiple edits count once. Percentages use the
sum of these three counts, range from 0 to 100, and are rounded to two decimals;
all are zero when no stories have an outcome. Rounding may produce a total of
99.99 or 100.01. These are all-time board metrics, independent of the event-history
`days` filter. The existing `acceptance_rate` retains its separate definition,
which includes edited stories that have been accepted.

## Layout

```
apps/extension         Chrome extension (WXT + React + TS)
apps/web               Companion web app (React + shadcn/ui)
services/api           FastAPI backend
services/worker        Gemini processing pipeline
packages/shared-types  Shared schemas
adapters/trello        Trello adapter (MVP)
adapters/github        GitHub Issues adapter (stretch)
infra/docker           Docker Compose
tests/                 Tests and sample meeting fixtures
docs/                  Design doc
```

## Responsible use

Only record meetings where everyone knows and agrees. The extension never records without an explicit click.

## License

MIT, see [LICENSE](LICENSE).
