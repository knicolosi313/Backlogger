import type { Provider, SelectedBoard, Settings } from './types';

// Trello and Jira are handled side by side. The helpers here keep the rest of the
// UI from caring which one is connected.

export const PROVIDER_LABEL: Record<Provider, string> = { trello: 'Trello', jira: 'Jira' };

// what each one calls the place meetings and stories belong to
const BOARD_NOUN: Record<Provider, string> = { trello: 'Board', jira: 'Project' };

// settings saved before Jira support have no provider, and those were all Trello
export function boardProvider(board: SelectedBoard | undefined): Provider {
  return board?.provider ?? 'trello';
}

export function providerLabel(board: SelectedBoard | undefined): string {
  return PROVIDER_LABEL[boardProvider(board)];
}

export function boardNoun(board: SelectedBoard | undefined): string {
  return BOARD_NOUN[boardProvider(board)];
}

// What the Settings tab tells people to do when nothing is chosen yet
export const PICK_DESTINATION_HINT = 'Connect Trello or Jira and pick a destination in Settings';

// Where an import lands inside the board: a Trello list, or the Jira issue type to create.
export function importDestination(settings: Settings): { id?: string; name: string } {
  if (boardProvider(settings.selectedBoard) === 'jira') {
    return { id: settings.jiraIssueType?.id, name: settings.jiraIssueType?.name ?? '' };
  }
  return { id: settings.targetListId, name: settings.targetList };
}

// For messages: `"Triage"` for a Trello list, `Demo as Story` for a Jira project and issue type
export function destinationText(settings: Settings): string {
  const { name } = importDestination(settings);
  if (boardProvider(settings.selectedBoard) === 'trello') return `"${name}"`;
  return `${settings.selectedBoard?.name ?? 'Jira'}${name ? ` as ${name}` : ''}`;
}

// Trello card ids and Jira issue keys both come back as `external_card_id`
export function cardUrl(board: SelectedBoard | undefined, externalCardId: string): string | undefined {
  if (boardProvider(board) === 'trello') return `https://trello.com/c/${externalCardId}`;
  const site = board?.siteUrl?.replace(/\/+$/, '');
  return site ? `${site}/browse/${encodeURIComponent(externalCardId)}` : undefined;
}
