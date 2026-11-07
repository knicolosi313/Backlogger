-- Initial single-workspace MVP schema. Run with psql ON_ERROR_STOP enabled.
-- Keep subsequent schema changes in new, numbered migrations.
BEGIN;

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE SCHEMA IF NOT EXISTS backlogger;
SELECT pg_advisory_xact_lock(hashtext('backlogger:001_initial'));

CREATE TABLE IF NOT EXISTS backlogger.boards (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider text NOT NULL CHECK (provider IN ('trello', 'github', 'jira')),
    external_id text NOT NULL,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, external_id)
);

CREATE TABLE IF NOT EXISTS backlogger.meetings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    board_id uuid NOT NULL REFERENCES backlogger.boards(id),
    title text NOT NULL,
    -- Store a path/object key, never the audio bytes or an expiring signed URL.
    media_key text,
    status text NOT NULL DEFAULT 'uploaded'
        CHECK (status IN ('uploaded', 'processing', 'completed', 'failed')),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Preserve every attempt's original Pydantic model_dump() output as JSONB.
-- Write each stage as it finishes so later failures do not lose earlier output.
CREATE TABLE IF NOT EXISTS backlogger.pipeline_outputs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    meeting_id uuid NOT NULL REFERENCES backlogger.meetings(id),
    run_id uuid NOT NULL,
    stage text NOT NULL CHECK (stage IN ('transcribe', 'extract', 'critique')),
    model text NOT NULL,
    output jsonb NOT NULL CHECK (jsonb_typeof(output) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (meeting_id, run_id, stage)
);

CREATE TABLE IF NOT EXISTS backlogger.stories (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    meeting_id uuid NOT NULL REFERENCES backlogger.meetings(id),
    -- Matches DraftStory, including acceptance criteria and timestamped source.
    draft jsonb NOT NULL CHECK (jsonb_typeof(draft) = 'object'),
    status text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'needs_changes', 'accepted', 'imported', 'rejected')),
    confidence double precision CHECK (confidence BETWEEN 0 AND 1),
    invest_score integer CHECK (invest_score BETWEEN 0 AND 6),
    -- StoryReview output, including flags and suggestions.
    critique jsonb CHECK (jsonb_typeof(critique) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS backlogger.story_revisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    story_id uuid NOT NULL REFERENCES backlogger.stories(id),
    revision integer NOT NULL CHECK (revision > 0),
    draft jsonb NOT NULL CHECK (jsonb_typeof(draft) = 'object'),
    editor text NOT NULL CHECK (editor IN ('human', 'ai')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (story_id, revision)
);

-- Reserve an idempotency key before making an external card-creation request.
-- On an uncertain external result, reconcile with the provider before retrying.
CREATE TABLE IF NOT EXISTS backlogger.imports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    story_id uuid NOT NULL REFERENCES backlogger.stories(id),
    idempotency_key text NOT NULL UNIQUE,
    external_card_id text,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'completed', 'failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (story_id),
    CHECK (status <> 'completed' OR external_card_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS backlogger.analytics_events (
    occurred_at timestamptz NOT NULL DEFAULT now(),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    board_id uuid NOT NULL REFERENCES backlogger.boards(id),
    story_id uuid REFERENCES backlogger.stories(id),
    event_type text NOT NULL,
    confidence double precision CHECK (confidence BETWEEN 0 AND 1),
    invest_score integer CHECK (invest_score BETWEEN 0 AND 6),
    properties jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (occurred_at, id)
);

SELECT create_hypertable(
    'backlogger.analytics_events', by_range('occurred_at'), if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS meetings_board_created_idx
    ON backlogger.meetings (board_id, created_at DESC);
CREATE INDEX IF NOT EXISTS stories_meeting_status_idx
    ON backlogger.stories (meeting_id, status);
CREATE INDEX IF NOT EXISTS analytics_board_time_idx
    ON backlogger.analytics_events (board_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS backlogger.schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO backlogger.schema_migrations (version)
VALUES ('001_initial') ON CONFLICT DO NOTHING;

COMMIT;
