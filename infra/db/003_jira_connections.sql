BEGIN;
SELECT pg_advisory_xact_lock(hashtext('backlogger:003_jira_connections'));
CREATE TABLE IF NOT EXISTS backlogger.jira_connections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    session_hash text NOT NULL UNIQUE,
    encrypted_token text,
    encrypted_refresh_token text,
    account_id text,
    display_name text,
    expires_at timestamptz,
    state_hash text,
    state_expires_at timestamptz
);
INSERT INTO backlogger.schema_migrations (version)
VALUES ('003_jira_connections') ON CONFLICT DO NOTHING;
COMMIT;
