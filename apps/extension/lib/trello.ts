import { request, type ApiConfig } from './http';
import type { TrelloBoard, TrelloConnection, TrelloList } from './types';

// Asks our backend about Trello. The backend holds the Trello token for this browser, so
// the extension never sees it. IDs here are Trello's own, not our database ids.

interface ConnectionWire {
  connected: boolean;
  username: string | null;
  expires_at: string | null;
}

function toConnection(wire: ConnectionWire): TrelloConnection {
  return { connected: wire.connected, username: wire.username, expiresAt: wire.expires_at };
}

export async function getTrelloConnection(cfg: ApiConfig): Promise<TrelloConnection> {
  return toConnection(await request<ConnectionWire>(cfg, '/api/integrations/trello/connection'));
}

// Open this in a tab. It sends the user to Trello, and the backend's callback page saves the token.
export function trelloConnectUrl(cfg: ApiConfig): string {
  return `${cfg.apiUrl.replace(/\/+$/, '')}/api/integrations/trello/connect`;
}

export async function disconnectTrello(cfg: ApiConfig): Promise<void> {
  await request(cfg, '/api/integrations/trello/connection', { method: 'DELETE' });
}

export function listTrelloBoards(cfg: ApiConfig): Promise<TrelloBoard[]> {
  return request<TrelloBoard[]>(cfg, '/api/integrations/trello/boards');
}

export function listTrelloLists(cfg: ApiConfig, externalBoardId: string): Promise<TrelloList[]> {
  return request<TrelloList[]>(cfg, `/api/integrations/trello/boards/${encodeURIComponent(externalBoardId)}/lists`);
}
