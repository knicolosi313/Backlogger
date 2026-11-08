# Connect a Trello account

Users can now click Connect Trello, approve Backlogger, and return with a stored
connection. Backlogger uses one application API key and a different encrypted
Trello token for each browser session. The backend no longer reads a shared
`TRELLO_TOKEN` from `.env`.

This uses Trello's documented `1/authorize` redirect/fragment flow, not OAuth 1.0.
`TRELLO_API_SECRET` is not required. The token briefly arrives in the callback
page's URL fragment; that page removes it from the URL and submits it to the
same-origin backend. It is not placed in a query string or localStorage.

## Backend setup

The connection table migration has been applied to the development database.
For another environment, apply the initial schema followed by:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./infra/db/setup.ps1 -Action Apply -Migration 002_trello_connections.sql
```

Install dependencies and set the following in `.env`:

```dotenv
TRELLO_API_KEY=YOUR_APPLICATION_KEY
TOKEN_ENCRYPTION_KEY=YOUR_FERNET_KEY
TRELLO_OAUTH_CALLBACK_URL=http://127.0.0.1:8000/api/integrations/trello/callback
ALLOWED_ORIGINS=http://127.0.0.1:5173
```

Generate a Fernet key locally, then copy it into `TOKEN_ENCRYPTION_KEY`:

```powershell
./.venv/Scripts/python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep that key stable and secret. Changing it makes existing stored connections
unreadable; affected browsers will need to reconnect. Do not put it in frontend
code. No encryption key or Trello app key is generated/overwritten automatically.

In your Trello application's settings, add `http://127.0.0.1:8000` as an allowed
origin for the API key. For deployment, use an HTTPS callback and allow its origin
in Trello. Keep the callback on the public origin serving `/api` and use the
callback route above. If the frontend proxies `/api`, use that frontend's public
origin for the callback too, so the browser sends the same session cookie.
Restart the API after changing configuration.

## Frontend calls

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/integrations/trello/connect` | Navigate here to start authorization; redirects to Trello |
| GET | `/api/integrations/trello/callback` | Backend-served callback page; Trello redirects here |
| POST | `/api/integrations/trello/connection` | Callback page validates and stores the returned token |
| GET | `/api/integrations/trello/connection` | Read connection status and account name; no token returned |
| DELETE | `/api/integrations/trello/connection` | Forget this browser's saved connection and clear its cookie |

Open the connect URL in a popup from a user click, rather than fetching it with
AJAX. For example, with the API proxied under the frontend's `/api`:

```javascript
window.open('/api/integrations/trello/connect', 'trello-connect', 'width=700,height=800');
```

The callback shows a success message. Poll connection status from the original
window (stop when connected or when the popup closes):

```javascript
const response = await fetch(`${API_BASE}/api/integrations/trello/connection`, {
  credentials: 'include'
});
const connection = await response.json();
```

Use `credentials: 'include'` on all subsequent API fetches, including board
discovery, list discovery, and export. The HttpOnly cookie identifies which
encrypted token to use. The client never needs to read the cookie or token.

For local frontend/API ports, use the same hostname consistently (for example,
`127.0.0.1` for both). Configure the frontend's explicit origin in
`ALLOWED_ORIGINS`, or proxy `/api` through the frontend. Cookies use SameSite=Lax;
unrelated cross-site frontend/API domains are not supported by this setup.

Once connected, continue with [FRONTEND_API.md](FRONTEND_API.md) to select a board,
process a meeting, and export an accepted story. Missing/expired connections
return 401 from Trello-dependent endpoints; the UI should offer Connect Trello.
Connection status is local metadata, not a live revocation check. If a user
revokes access in Trello, subsequent requests fail and the UI should reconnect.

## Scope and protections

Connections are scoped to browser sessions, not application user accounts.
Board/meeting/story data is still a shared workspace. This feature does not add
account login, tenant isolation, or secure access control for that shared data.
Before hosting for unrelated users, add those separately.

The session cookie is random and HttpOnly; only its SHA-256 hash is stored. HTTPS
callbacks use Secure cookies. Authorization challenges are browser-bound,
single-use, and expire in 10 minutes. Trello access is requested for read/write
for 30 days, and tokens are encrypted with Fernet in `trello_connections`.
Tokens and ciphertext are omitted from API responses, including validation errors.

Disconnect deletes local credentials. It does not revoke the token at Trello or
cancel exports already running. Users can revoke the application's permission
from Trello's account settings. Expired abandoned challenge rows are not
automatically purged yet.

Tests use mocked Trello responses and rolled-back database records; no real
account is authorized by the test suite. The browser consent screen still needs
a manual end-to-end check with a configured Trello application.

Reference: [Trello authorization guide](https://developer.atlassian.com/cloud/trello/guides/rest-api/authorization/).
