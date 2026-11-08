# TigerData database setup

Existing development service: `hack-coms-web-extension` (`jlzyf8e3yy`), us-east-1.
The database name is `tsdb`; the application uses the `backlogger` schema inside it.

## Connect

1. Copy `.env.example` to `.env` in the repository root if you do not already have one.
2. In Tiger Cloud, open the service's connection details. Put the PostgreSQL URI,
   including your database password and `sslmode=require`, in `.env` as
   `DATABASE_URL="postgresql://..."`. Percent-encode special characters in the
   password when embedding it in a URI. Do not paste credentials into chat or commit them.
3. From the repository root, run:

   ```powershell
   ./infra/db/setup.ps1 -Action Check
   ./infra/db/setup.ps1 -Action Apply
   ./infra/db/setup.ps1 -Action Apply -Migration 002_trello_connections.sql
   ./infra/db/setup.ps1 -Action Apply -Migration 003_jira_connections.sql
   ./infra/db/setup.ps1 -Action Check
   ```

   If Windows blocks script execution, run the same command with a policy override
   for that process only (no persistent policy change):

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./infra/db/setup.ps1 -Action Check
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./infra/db/setup.ps1 -Action Apply
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./infra/db/setup.ps1 -Action Apply -Migration 002_trello_connections.sql
   ```

The script requires `psql` on PATH (PostgreSQL 17 is installed on this computer).
It reads `DATABASE_URL` from the process environment first, otherwise from `.env`.
It does not execute `.env` as code. `Check` is read-only; `Apply` creates the
initial tables and TimescaleDB hypertable in one transaction and stops on errors.
Rerunning `Apply` preserves existing data; it does not upgrade existing table definitions.
Future changes should use new, numbered migrations.
`-Migration` selects a specific migration; its default remains `001_initial.sql`.

## Initial storage

- `boards`, `meetings`: board identity, media location, processing status.
- `pipeline_outputs`: original `Transcript`, `ExtractionResult`, and `CritiqueResult`
  JSON for each run. Each stage should be saved immediately after completion.
- `stories`: current `DraftStory` JSON, review status, separate confidence and INVEST score.
- `story_revisions`: versioned snapshots; save the original as revision 1 and
  write later edits and the current story together in a transaction.
- `imports`: one reservation per story with a unique idempotency key.
- `analytics_events`: time-partitioned events for future trends and analytics.
- `schema_migrations`: records initial setup.

The schema follows the current worker models. JSONB preserves their nested fields
without a second set of Python data models. Media stays in local/object storage.
No sample meetings, users, or credentials are inserted. This is a single-workspace
MVP schema; account authentication, tenant isolation, and continuous aggregates
are subsequent work. The API materializes story drafts, supports manual review,
and exports accepted stories. Migration 002 adds `trello_connections` to store
encrypted browser-specific Trello tokens and short-lived authorization state;
see [Connect Trello](../../docs/TRELLO_CONNECT.md).
Migration 003 adds encrypted Jira OAuth access/refresh tokens and browser-bound
state; see [Jira Cloud](../../docs/JIRA_CLOUD.md).
SQLModel updates `stories.updated_at` on ORM edits; direct SQL writers must update it.

## Python access with SQLModel

Install dependencies and start the API from the repository root:

```powershell
uv venv --python 3.13 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
./.venv/Scripts/python.exe -m uvicorn services.api.server:app --reload
```

`GET /health/db` checks connectivity. The API uses a new SQLModel session per
request via `services.db.get_session`. The engine's connection pool is shared
within each process and is created lazily from `DATABASE_URL`. Existing environment
variables take precedence over the project-root `.env`. Restart the process after
changing credentials. The plain `postgres://` or `postgresql://` URI works for both
the PowerShell setup script and Python; Python selects the Psycopg 3 driver.

Models live in `services/models.py` and map all eight existing tables, including
JSONB and the analytics composite primary key. SQL migrations remain the source
of truth for table definitions, constraints, indexes, and hypertables. Do not call
`SQLModel.metadata.create_all()` at startup.

For a worker or script:

```python
from sqlmodel import Session, select

from services.db import get_engine
from services.models import Board, Meeting

with Session(get_engine()) as session:
    board = Board(provider="trello", external_id="your-board-id", name="Demo")
    session.add(board)
    session.flush()
    meeting = Meeting(board_id=board.id, title="Sprint planning")
    session.add(meeting)
    session.commit()
    session.refresh(meeting)
    meeting_id = meeting.id

with Session(get_engine()) as session:
    meetings = session.exec(
        select(Meeting).where(Meeting.status == "uploaded")
    ).all()
```

Call `session.commit()` explicitly for writes, or use `with session.begin():`.
Closing an uncommitted session rolls back pending changes. Replace JSON dictionary
fields when editing them (`story.draft = new_draft`); nested in-place edits are
not automatically tracked.

Gemini's Pydantic models remain in `services/worker/objects.py`. To checkpoint an
existing meeting's pipeline stages:

```python
from services.worker.pipeline import run_pipeline

transcript, extraction, critique = run_pipeline("meeting.wav", meeting_id=meeting_id)
```

The meeting must already exist. Each stage's raw JSON is committed independently
to `pipeline_outputs` under a shared run UUID. A later failure leaves earlier
outputs available and marks the meeting failed; success marks it completed.
Run only one pipeline at a time per meeting. Calls without `meeting_id` retain
the standalone behavior and do not connect to the database. This does not yet
populate `stories`, revisions, imports, or analytics events automatically when
called directly. The API's processing job wraps this pipeline and also creates
reviewable stories, initial revisions, and analytics events before completion.
See [the API guide](../../services/api/README.md).

Run tests with:

```powershell
./.venv/Scripts/python.exe -m pytest tests/test_database.py -q
# Opt in to tests against the migrated database; synthetic rows are rolled back.
$env:RUN_DATABASE_TESTS = "1"
./.venv/Scripts/python.exe -m pytest tests/test_database.py -q
```

If the Tiger connector reports password authentication failure, its saved database
credential needs to be refreshed through its connection configuration. A working
local `.env` connection is independent of the connector. Do not reset the service
password unless you intend to update every client that uses it.

Hypertable syntax: [TigerData create_hypertable reference](https://www.tigerdata.com/docs/reference/timescaledb/hypertables/create_hypertable).
