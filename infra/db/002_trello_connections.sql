-- Browser-scoped Trello connections for the shared-workspace MVP.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('backlogger:002_trello_connections'));

CREATE TABLE IF NOT EXISTS backlogger.trello_connections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    session_hash text NOT NULL UNIQUE,
    encrypted_token text,
    member_id text,
    username text,
    connected_at timestamptz,
    expires_at timestamptz,
    state_hash text,
    state_expires_at timestamptz
);

INSERT INTO backlogger.schema_migrations (version)
VALUES ('002_trello_connections') ON CONFLICT DO NOTHING;
COMMIT;
