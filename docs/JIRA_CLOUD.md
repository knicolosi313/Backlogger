# Jira Cloud connection and exports

Jira Cloud is supported alongside Trello. A browser connects its own Atlassian
account, selects a site and project, and exports accepted stories as Jira issues.
The workspace's local board/meeting/story data is still shared: this feature does
not add application login or tenant isolation. Jira Data Center is not supported.

## Configure the app

Create an OAuth 2.0 (3LO) application in the Atlassian developer console. Add the
Jira API permissions `read:jira-work` and `write:jira-work`, and the User Identity
API permission `read:me`. The authorization request also includes `offline_access`
for rotating refresh tokens. Enable distribution if users other than the app
owner need to authorize it, subject to your organization's app policies.

Register this exact callback for local development:

```text
http://127.0.0.1:8000/api/integrations/jira/callback
```

Set these server-side values in `.env`:

```dotenv
JIRA_CLIENT_ID=YOUR_APP_CLIENT_ID
JIRA_CLIENT_SECRET=YOUR_APP_CLIENT_SECRET
JIRA_OAUTH_CALLBACK_URL=http://127.0.0.1:8000/api/integrations/jira/callback
TOKEN_ENCRYPTION_KEY=YOUR_EXISTING_FERNET_KEY
ALLOWED_ORIGINS=http://127.0.0.1:5173
```

Use the same encryption key as the Trello connections. Keep it stable and secret.
Production callbacks require HTTPS. If `/api` is proxied through the frontend,
configure the callback on that public origin so it receives the session cookie.
Restart the API after changing settings. No app credentials are generated or
overwritten automatically.

Migration 003 has been applied to the development database. For another database,
apply the initial migration and connection migrations in order:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./infra/db/setup.ps1 -Action Apply -Migration 003_jira_connections.sql
```

## Connect and choose a destination

Navigate to `/api/integrations/jira/connect` in a browser or popup. Atlassian asks
for consent and redirects back to the callback. The callback page removes the
authorization code from the address bar and submits it to the backend, which
exchanges it for tokens. The client secret and access/refresh tokens stay on the
backend; tokens are encrypted in `backlogger.jira_connections`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/integrations/jira/connect` | Start OAuth consent |
| GET | `/api/integrations/jira/callback` | Backend-served callback page |
| POST | `/api/integrations/jira/connection` | Complete authorization; called by callback page |
| GET | `/api/integrations/jira/connection` | Read saved connection metadata |
| DELETE | `/api/integrations/jira/connection` | Forget this browser's saved connection |
| GET | `/api/integrations/jira/sites` | List authorized Jira sites |
| GET | `/api/integrations/jira/sites/{cloud_id}/projects` | List projects, with `start_at` pagination |
| GET | `/api/integrations/jira/sites/{cloud_id}/projects/{project_id}/issue-types` | Get issue types, with `start_at` pagination |
| GET | `/api/integrations/jira/sites/{cloud_id}/projects/{project_id}/issue-types/{issue_type_id}/fields` | Get creation-field metadata, with `start_at` pagination |

Send `credentials: 'include'` on frontend API requests. Cookies are HttpOnly and
SameSite=Lax; use consistent hostnames locally or a same-site `/api` proxy. The
configured frontend origins can send credentialed CORS requests. Access tokens
are refreshed before expiry under a connection-row lock; rotated refresh tokens
are encrypted and committed before subsequent Jira calls. Invalid refresh grants
require reconnecting. Status is cached local metadata, not a live revocation check.
Disconnect forgets local credentials; it does not revoke the Atlassian grant or
cancel an issue request already in flight.

## Register a Jira project locally

The local `boards` table represents a project destination for Jira. It does not
represent a Jira Agile board or column. After selecting a site and project, call
`POST /api/boards`:

```json
{
  "provider": "jira",
  "external_id": "CLOUD_UUID:10001",
  "name": "Demo Jira project"
}
```

Use the site UUID and numeric project ID from discovery, separated by a colon.
Use the returned database board UUID when creating meetings and reading stories.
The existing upload, processing, manual editing, and human acceptance flows apply.

## Export an accepted story

Call the same provider-aware endpoint as Trello:

```text
POST /api/boards/{database_board_id}/imports
```

```json
{
  "story_id": "STORY_UUID",
  "issue_type_id": "10002",
  "additional_fields": {
    "customfield_10010": "A value required by your project"
  }
}
```

`additional_fields` is optional. Use creation-field metadata to provide required
custom fields, reporter, parent, or other project-specific values. Missing required
fields are rejected before an import reservation is committed. Additional fields
cannot override project, issue type, summary, description, or labels. Jira still
validates field values and the account's permissions.

The issue gets the story title, an Atlassian Document Format description with
acceptance criteria and proposed solution tasks in separate checkbox-style lists,
the source quote with the time in the recording where it begins (for example
`Source Quote: "..." (2:14)`), and the labels `ai-draft`
and `backlogger-story-STORY_UUID`. Priority and estimates are included as text;
mapping them to Jira priority/story-point fields requires the appropriate values
in `additional_fields`. Jira chooses the issue's initial workflow status; the
export does not move it into a specific Agile column.
The checkbox lists are visual ADF description content, not interactive native
Jira checklist controls. Trello exports use actual native checklists instead.

The existing import response uses the legacy field `external_card_id` to return
the Jira issue key, for example `DEMO-20`. Completion marks the story imported
and records a `story_imported` analytics event. Existing import-history and
story-import-status routes work for both providers.

## Using Jira from the browser extension

The extension's Settings tab has a Jira card next to Trello. Connect opens
`/api/integrations/jira/connect` in a tab, then the panel lets you pick a site, a
project, and the issue type stories are created as (it defaults to Story, then
Task; sub-tasks are not offered). Choosing a project registers it with
`POST /api/boards` using `provider: "jira"`.

Only one connection is used at a time: each Connect button is disabled while the
other provider is connected, and disconnecting clears that provider's saved
board/project and destination. This is enforced in the extension only. The backend
keeps separate Trello and Jira sessions and will accept both.

If the project requires fields beyond the ones the export fills in itself (a custom
field, the reporter), Settings shows a "Required by this project" form built from the
creation-field metadata. Values are saved per project and issue type and sent as
`additional_fields` on every import; the field list is re-fetched at import time, and
the import is stopped with a message if a required field is still empty. Supported
types are text, multi-line text (sent as a document), number, date, date-time,
single and multiple choice, user, and labels. A user field left empty means the
connected account (this is what covers a required reporter). Any other type is entered
as the raw JSON value Jira expects.

## Retry behavior and limitations

Each story has one local import record. Creation intent is committed before the
POST; the external issue key is saved immediately after a successful response.
Repeated completed exports return the saved result. If creation times out, the
service searches Jira's enhanced JQL endpoint for the stable marker label.
If a pending import has no matching issue, it returns 409 instead of creating
another issue. Search can lag behind creation; a missing result is not proof of
failure. Multiple matching issues require manual reconciliation. Removing marker
labels can prevent recovery. This is not an external exactly-once guarantee.

Jira's explicit issue-creation validation rejections (400/422) mark the import
failed and retryable, so field values can be corrected. Timeouts and server
errors retain the conservative pending state.

Pending exports block story edits and review changes. No automatic retry loop,
issue updates, transition workflow, multi-project export, or Data Center support
is included. Trello requests still use `list_id`; Jira requests use `issue_type_id`.
The export client is selected from the registered board's provider, not from
frontend-supplied credentials.

Tests mock OAuth and Jira calls and roll back synthetic database records. A live
consent/export smoke test still needs a configured Atlassian app and test project.

References: [Atlassian OAuth 2.0](https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/),
[Jira issue creation](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/),
[Jira enhanced search](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/).
