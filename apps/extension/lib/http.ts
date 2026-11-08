import type { Settings } from './types';

// Shared fetch wrapper for api.ts and trello.ts.

export type ApiConfig = Pick<Settings, 'apiUrl' | 'apiToken'>;

// A non-2xx answer from the backend. The message is safe to show to the user.
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

// FastAPI's `detail` is a string for errors we raise (404, 409...) and a list of
// { loc, msg } objects for validation errors (422).
function describeError(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item as { msg?: unknown } | null)?.msg)
      .filter((msg): msg is string => typeof msg === 'string');
    if (messages.length) return messages.join('; ');
  }
  return `The server returned an error (${status}).`;
}

// Pass `form` for uploads and leave Content-Type alone so the browser adds the boundary.
export async function request<T>(
  cfg: ApiConfig,
  path: string,
  init: { method?: string; json?: unknown; form?: FormData } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (cfg.apiToken) headers.Authorization = `Bearer ${cfg.apiToken}`;

  let body: BodyInit | undefined;
  if (init.form) {
    body = init.form;
  } else if (init.json !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(init.json);
  }

  let response: Response;
  try {
    // credentials: 'include' sends the cookie that says which Trello account this browser connected
    const url = `${cfg.apiUrl.replace(/\/+$/, '')}${path}`;
    response = await fetch(url, { method: init.method ?? 'GET', headers, body, credentials: 'include' });
  } catch {
    throw new ApiError(`Could not reach the server at ${cfg.apiUrl}. Is the API running?`, 0);
  }

  const text = await response.text();
  let parsed: unknown;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = undefined; // not JSON, maybe a proxy error page
    }
  }

  if (!response.ok) throw new ApiError(describeError(parsed, response.status), response.status);
  return parsed as T;
}
