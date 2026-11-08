BEGIN;
SELECT pg_advisory_xact_lock(hashtext('backlogger:005_story_soft_delete'));
-- Deleting a rejected draft hides it from the app but keeps the row (and its history and analytics).
ALTER TABLE backlogger.stories ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
INSERT INTO backlogger.schema_migrations (version)
VALUES ('005_story_soft_delete') ON CONFLICT DO NOTHING;
COMMIT;
