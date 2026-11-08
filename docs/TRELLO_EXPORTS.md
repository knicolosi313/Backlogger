# Exporting a story to Trello

The export service is `services/api/exports.py`. It coordinates database records
with `adapters/trello/client.py`; the client itself never accesses the database.
No schema migration is needed for this feature.

## Configuration and request

Configure the application API key and encryption key, then use
[Connect Trello](TRELLO_CONNECT.md) to authorize a browser-specific account that
can write to the board. Exports use that browser's encrypted token, not a shared
`TRELLO_TOKEN` in `.env`. Include cookies in frontend requests. The workspace's
board/meeting/story data is still shared; account and tenant isolation are not
implemented.

Register the target board with its actual Trello board ID, process a meeting,
and accept a story. Use the local database board UUID in this request path:

```text
POST /api/boards/{board_id}/imports
```

```json
{
  "story_id": "YOUR_STORY_UUID",
  "list_id": "YOUR_TRELLO_LIST_ID"
}
```

Cards get no meeting link or attachment. The description ends with the quote and the
time in the recording where it begins, for example
`Source Quote: "like a settings page, our profile page where you can set your location on." (2:14)`.
`evidence_url` is still accepted so older clients keep working, but it is ignored.

`list_id` must be an open list on that board; choose your Triage list. Supply a
real meeting or review-page URL. The backend does not yet serve a playback UI.
The response contains the database import ID, story ID, stable idempotency key,
external card ID, status, and creation time. Inspect the request schema in `/docs`.

## What the service does

1. Locks the story row and checks that it belongs to the target Trello board and
   has been accepted. Completed exports return their saved result immediately.
2. Looks for a card it already created for this story: same title and exactly the
   description it generates. Archived cards are included, and cards already linked
   to another story's import are skipped. A recovered card is reused rather than
   overwritten. Nothing is added to cards to make them findable; the description
   holds only the story. Cards made before this change ended with a
   `Backlogger-Import-Key:` line, which is still recognized.
3. Validates the target list when a new card is needed, and finds or creates the
   `AI Draft` label.
4. Commits a pending import reservation before attempting card creation. The key
   stays stable for the story. Row locks serialize this service's exports of the
   same story; review routes block changes while an import is pending.
5. Creates the card if appropriate, then immediately saves its ID. If adding a
   label fails, the ID is retained for the next request.
6. Creates native `Acceptance Criteria` and `Solution Tasks (proposed)`
   checklists, then adds the label. On retry, existing
   checklists are matched by name and items by exact text. Existing checked
   states are preserved; missing items are added unchecked.
7. Marks the import completed, marks the story imported, and records one
   `story_imported` analytics event together in a database transaction.

## Retries and errors

Checklist creation applies to new exports and resumed pending exports. Completed
exports return their saved result; this change does not retrofit cards that were
already exported. Checklist state is held in Trello and is not synchronized back
to the story JSON. Reading before adding avoids ordinary retry duplicates, but
remote checklist/item requests still have no external exactly-once guarantee.

Repeat the same request to reconcile an interrupted export. A completed export
returns the original card ID without another Trello call or analytics event,
even if the repeated request supplies a different list. Updating
an already-exported card is a separate feature.

If a creation request times out, its result is unknown: the card could still
appear later. The reservation stays pending. A retry searches for the card;
if it finds it, it resumes the export. If it cannot find the card, it
returns 409 and does not create another one. A crash after reservation but before
the POST has the same conservative behavior. Such an unresolved reservation
requires manual reconciliation; there is no automatic reset/recreation endpoint.
Do not interpret a missing search result alone as proof that creation failed.

The database cannot atomically commit with Trello, so this is a reconciliation
strategy rather than an external exactly-once guarantee. Renaming a card or
editing its description before a pending export is retried can prevent recovery.

Missing configuration returns 503; Trello request failures return a sanitized
502 without exposing credentials. Unaccepted stories, non-Trello boards,
invalid target lists, and unresolved creation attempts return 409. A story on
another board returns 404. Preflight failures before card creation do not leave
a new reservation committed.

## Tests

```powershell
$env:RUN_DATABASE_TESTS = "1"
./.venv/Scripts/python.exe -m pytest tests/test_exports.py tests/test_trello_client.py -q
Remove-Item Env:RUN_DATABASE_TESTS
```

All Trello calls are mocked. Database integration tests use synthetic data and
roll back their outer transactions, including internally committed import steps.
No live Trello cards or labels are created during tests.
