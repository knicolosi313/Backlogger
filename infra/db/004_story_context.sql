BEGIN;
SELECT pg_advisory_xact_lock(hashtext('backlogger:004_story_context'));
CREATE TABLE IF NOT EXISTS backlogger.story_context (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    board_id uuid NOT NULL REFERENCES backlogger.boards(id),
    source text NOT NULL,
    external_id text NOT NULL,
    payload jsonb NOT NULL,
    embedding jsonb,
    embedding_model text,
    content_hash text,
    UNIQUE (board_id, source, external_id)
);
INSERT INTO backlogger.schema_migrations (version)
VALUES ('004_story_context') ON CONFLICT DO NOTHING;
COMMIT;
