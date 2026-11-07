# Meeting-to-Backlog

> Your meeting ends, and your backlog is already drafted, checked, and linked back to the exact moment it was discussed.

A Chrome extension records a browser meeting beside a Trello board. Gemini turns it into evidence-linked user stories, checked against INVEST. A human reviews them, then approves them into Trello. A web app tracks meeting history and backlog quality.

**Status:** hackathon concept.

Full design: [docs/DESIGN.md](docs/DESIGN.md)
AI usage disclosure (hackathon requirement): [docs/AI_USAGE.md](docs/AI_USAGE.md)

Database setup: [infra/db/README.md](infra/db/README.md)

## Backend development

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
