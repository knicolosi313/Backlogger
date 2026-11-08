# Automatic story imports

Auto-import is optional and off by default. Enable **Auto-import confident
stories** on the extension's Record tab before submitting a meeting. In Settings,
choose the minimum confidence (50%-100%, default 85%) and a Trello list or Jira
issue type. Jira's additional required fields must also be filled in.

The extension sends those settings with the processing request. The backend
validates the connected account and destination before starting, then saves a
snapshot for this meeting. Later changes to the slider, toggle, selected board,
or destination affect future submissions rather than a running meeting. If a
failed processing attempt is submitted again with auto-import off, the old unused
configuration is removed.

## Which stories qualify

An automatically imported story must:

- Be an untouched draft from this meeting, explicitly discussed rather than
  inferred or proposed, with no assumptions or clarification needed.
- Meet the configured confidence threshold in both the stored score and review.
- Score 6/6 on INVEST, have no review flags, and have no recommendation to split
  or reject. Confirmed scope overlap/dependencies also prevent auto-import.
- Include nonempty acceptance criteria and a nonempty source quote/speaker with
  finite, ordered, nonnegative evidence timestamps.

Everything else remains for manual review. Exploratory AI proposals never bypass
manual review merely because the confidence slider is set low. A reviewer can
still approve and export those stories using the normal manual workflow.

The worker rechecks eligibility and current story state immediately before
exporting. It records `story_auto_accepted`, then calls the same Trello/Jira
export service used by manual imports, with the existing labels, checklists,
evidence attachments, and idempotency keys. Evidence links point to the backend's
meeting audio endpoint, not an unimplemented web dashboard.

## Progress and retries

Processing and automatic exports run on the backend, so closing the extension
does not cancel them. The completed-meeting panel shows imported, left-for-review,
and failed counts. An export failure does not change a successfully processed
meeting to failed. Other eligible stories are still attempted.

Use **Retry auto-import** after checking the connection/provider. Retries use the
original settings and account; they reuse completed imports and saved external
IDs. Uncertain creation requests follow the existing export reconciliation rules
and do not blindly create a duplicate. If a destination was removed, reconcile
the failed stories manually; changing extension Settings does not retarget an
existing run.

No tokens are copied into run settings. The worker reads encrypted credentials
at export time, refreshes Jira tokens when needed, and verifies the original
external account identity. Disconnecting or switching accounts prevents pending
exports from using a different account. Run status/retry requests require the
same saved browser connection and account that enabled the run. Reauthorizing
that existing connection may fix expiry; deleting it and creating a different
connection does not transfer access to the previous run.

This uses the application's existing in-process background tasks, not a durable
queue. A server restart can interrupt processing/imports. For a completed meeting,
a pending import can be retried; a run with no progress for 15 minutes can also
be retried. Active runs reject duplicate retries. Audio processing interrupted
by a restart retains the existing meeting-processing recovery limitations.

## API

```http
POST /api/meetings/{meeting_id}/process
```

Example Trello body:

```json
{
  "creativity": "balanced",
  "auto_import": {
    "confidence_threshold": 0.85,
    "list_id": "TRELLO_LIST_ID",
    "evidence_base_url": "https://backlogger-production.up.railway.app"
  }
}
```

For Jira, replace `list_id` with `issue_type_id` and optional `additional_fields`.
Choose exactly one destination type. Protected core fields cannot be replaced.
Use the connected browser cookie, as with normal exports. Omitting `auto_import`
or setting it to null leaves auto-import off. Existing processing requests work.

- `GET /api/meetings/{meeting_id}/auto-import` returns `meeting_id`, status
  (`pending`, `running`, `completed`, `failed`), imported/skipped/failed counts,
  a sanitized error, and `updated_at`. It returns 404 when no run was enabled and
  403 for a different account/connection. It never returns credential references.
- `POST /api/meetings/{meeting_id}/auto-import/retry` returns 202 with the saved
  status and schedules a retry when allowed. A completed run is a no-op; active
  runs and unfinished meetings return 409.

## Database and deployment

Apply the additive migration to each database used by a deployment:

```powershell
./infra/db/setup.ps1 -Action Apply -Migration 005_auto_import.sql
```

`auto_import_runs` stores the meeting's configuration, original connection/account
identity, selected story IDs, progress counts, and errors. The original database
records are preserved. Restart/redeploy the backend and rebuild/reload the
extension after updating. Existing completed meetings are not retroactively
auto-imported. Application records still use the existing shared workspace;
browser-scoped export authorization does not introduce general tenant isolation.
