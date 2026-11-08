BEGIN;
SELECT pg_advisory_xact_lock(hashtext('backlogger:005_auto_import'));
CREATE TABLE IF NOT EXISTS backlogger.auto_import_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    meeting_id uuid NOT NULL UNIQUE REFERENCES backlogger.meetings(id),
    connection_id uuid NOT NULL,
    options jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    story_ids jsonb NOT NULL,
    imported integer NOT NULL CHECK (imported >= 0),
    skipped integer NOT NULL CHECK (skipped >= 0),
    failed integer NOT NULL CHECK (failed >= 0),
    error text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO backlogger.schema_migrations (version)
VALUES ('005_auto_import') ON CONFLICT DO NOTHING;
COMMIT;
