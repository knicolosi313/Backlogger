# Tests

Install the test dependencies and run from the repository root:

```powershell
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
./.venv/Scripts/python.exe -m pytest -q
```

The default run uses fake Gemini clients and fake database sessions. It skips
live PostgreSQL tests unless `RUN_DATABASE_TESTS=1` is set. No tests call Gemini
or Trello, and no real meeting audio is needed. Synthetic structured outputs
are built in `conftest.py`.

| File | Coverage |
| --- | --- |
| `test_pipeline.py` | Three-pass inputs and outputs, checkpoint ordering, upload polling, upload failures, model failures, save failures, standalone runs |
| `test_worker_objects.py` | Nested JSON round trips, required evidence, priorities, INVEST criteria, confidence and score bounds |
| `test_db_configuration.py` | URL handling, engine reuse, missing configuration, session cleanup |
| `test_database.py` | API health responses, schema mappings, JSONB round trips, persisted pipeline checkpoints and failure status |
| `test_database_constraints.py` | Import uniqueness, completed-card requirement, foreign keys, valid status, unique stages, hypertable metadata, missing meetings |
| `test_setup_script.py` | Environment and .env configuration, quoted values, password handling, Check/Apply arguments, command errors |

The setup script tests require Windows PowerShell and skip if it is unavailable.
They copy the script to a temporary project and replace psql with a fake command,
so even their `Apply` test never connects or runs migrations.

To include tests against the existing, migrated development database:

```powershell
$env:RUN_DATABASE_TESTS = "1"
./.venv/Scripts/python.exe -m pytest -q
Remove-Item Env:RUN_DATABASE_TESTS
```

Integration tests use `DATABASE_URL` from the environment or project-root `.env`.
Synthetic writes happen inside outer transactions that roll back, including when
the pipeline commits internally. They do not apply migrations or change existing
application records.

These tests check the implemented code and schema. They do not evaluate Gemini's
actual story quality or cover the extension, web app, or board adapters, which
are not implemented yet.
