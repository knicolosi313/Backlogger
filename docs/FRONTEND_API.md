# Frontend board selection and exports

Optional automatic exports are configured in the meeting processing request;
see [auto-import](AUTO_IMPORT.md). The extension's Record toggle and confidence
slider now send the selected destination/threshold, with progress and retries
shown after processing. Auto-import runs on the backend rather than panel polling.

Audio uploads also support a private Supabase bucket configured on the backend.
The frontend upload/process endpoints stay the same; treat `media_key` as opaque.
See [Supabase storage](SUPABASE_STORAGE.md) for configuration and processing.

The extension plays meeting evidence directly in its side panel using
`GET /api/meetings/{meeting_id}/audio`. The response is audio bytes, not JSON.
Use the usual backend credentials when fetching it, then a browser Blob URL for
the audio controls and timestamp seeking. No web dashboard is needed for this
action. The endpoint has the same shared-workspace access model as other meeting
routes; it does not provide user isolation.

Before processing audio, refresh the meeting's existing external work with
`POST /api/boards/{board_id}/context/sync` using the connected account cookie.
The extension already does this. See [board context](BOARD_CONTEXT.md) for epic
names, related-story references, semantic retrieval, and limits.

For Jira Cloud sites, projects, issue types, and exports, see
[JIRA_CLOUD.md](JIRA_CLOUD.md). The following walkthrough describes Trello.

The frontend calls FastAPI. First use [Connect Trello](TRELLO_CONNECT.md) to
authorize an account for this browser. The application API key stays in `.env`;
each browser's token is stored encrypted in the database. Send browser cookies
with `credentials: 'include'` on API calls. Application account authentication
and workspace isolation are not implemented yet.

## Pick and register a board

Call `GET /api/integrations/trello/boards`. It returns open boards accessible to
the configured account, with only `id`, `name`, `url`, and `closed` fields:

```json
[
  {"id": "EXTERNAL_TRELLO_ID", "name": "Project", "url": "https://trello.com/b/example", "closed": false}
]
```

Show these in a dropdown. Use the chosen `id` as `external_id` when registering
the board with `POST /api/boards`:

```json
{"provider": "trello", "external_id": "EXTERNAL_TRELLO_ID", "name": "Project"}
```

Keep the returned database `id` as well as its `external_id`. A duplicate
registration returns 409; use `GET /api/boards` to find the existing registration
instead. That route lists local database records, not boards fetched from Trello.

## Pick an export list

Call `GET /api/integrations/trello/boards/{external_board_id}/lists` using the
external Trello board ID. This works before registration and does not write
to the database. It returns open lists with `id` and `name`:

```json
[{"id": "EXTERNAL_TRELLO_LIST_ID", "name": "Triage"}]
```

Use the selected list's `id` as `list_id` in the import request. The export
service validates that it belongs to the target board. Label discovery is not
needed by the frontend because the service finds/creates the AI Draft label.

## Upload, process, and review

Use the database board UUID with the existing endpoints:

1. `POST /api/meetings` with `board_id` and `title`.
2. `POST /api/meetings/{id}/media` with multipart field `file`.
3. `POST /api/meetings/{id}/process`; poll its returned `status_url`.
4. `GET /api/boards/{id}/stories` to display the drafts.
5. `PATCH /api/stories/{id}` for manual edits and
   `POST /api/stories/{id}/decision` with `{"decision":"accept"}` to approve.

## Export and inspect the result

Call `POST /api/boards/{database_board_id}/imports` with `story_id`, `list_id`,
and `evidence_url`, as described in [TRELLO_EXPORTS.md](TRELLO_EXPORTS.md).

Two read-only endpoints let the UI inspect exports without creating or retrying
anything, and without requiring Trello credentials or making Trello requests:

| Route | Purpose |
| --- | --- |
| `GET /api/stories/{story_id}/import` | Get this story's saved import record; 404 if there is no attempt |
| `GET /api/boards/{board_id}/imports` | Get this registered board's import history |

Both return import IDs, story IDs, idempotency keys, saved external card IDs,
status, and creation times. History supports `status=pending|completed|failed`,
`limit` (1–100, default 50), and `offset` (default 0).

If export fails, inspect the saved status and offer a retry of the same POST.
A pending import with an unknown card ID requires marker reconciliation;
the read endpoints deliberately do not change that state. The service does not
blindly recreate a card when a previous creation result is uncertain.

Discovery errors are sanitized: missing configuration is 503, rejected credentials
or access denial is 502, inaccessible/missing Trello resources are 404, timeouts
are 504, and rate limits are 503. Export POST errors retain the behavior documented
in the export guide. No response includes the backend's key or token.

Schemas for all endpoints are available in `/docs` and `/openapi.json`.

## Restore a rejected story

Call `POST /api/stories/{story_id}/decision` with `{"decision":"restore"}`.
This returns the story to `draft`, preserving its content, scores, critique, and
revision history. The extension's **Restore to drafts** button uses this action.
Restoring an already restored draft is safe to retry without duplicate events.
Accepted, imported, or needs-changes stories return 409, as do stories with a
pending export. A successful transition records `story_restored` for analytics;
the story leaves the rejected counts and can be reviewed again.
