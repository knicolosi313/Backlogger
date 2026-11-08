import type { Provider, RecorderState, Settings } from './types';

// 'local:' is kept on disk, 'session:' is cleared when the browser closes

export const DEFAULT_SETTINGS: Settings = {
  apiUrl: 'https://backlogger-production.up.railway.app',
  apiToken: '',
  webAppUrl: 'http://localhost:5173',

  creativity: 'balanced',
  model: '', // empty means the server's own default model

  autoImport: false,
  autoImportThreshold: 0.85,
  targetList: 'Triage',

  consentReminder: true,
};

export const settingsItem = storage.defineItem<Settings>('local:settings', {
  fallback: DEFAULT_SETTINGS,
});

// The saved board, list and so on belong to one provider's account. Once that provider is
// disconnected they can't be used, and they'd be wrong for whichever one is connected next.
export async function forgetProvider(provider: Provider): Promise<void> {
  const latest = await settingsItem.getValue();
  const owned = (latest.selectedBoard?.provider ?? 'trello') === provider;
  if (!owned && provider === 'trello') return;

  const cleared: Partial<Settings> = owned
    ? { selectedBoard: undefined, targetList: DEFAULT_SETTINGS.targetList, targetListId: undefined }
    : {};
  if (provider === 'jira') {
    Object.assign(cleared, { jiraSite: undefined, jiraIssueType: undefined, jiraFieldValues: undefined });
  }
  await settingsItem.setValue({ ...latest, ...cleared });
}

// The tab each Connect button opened, so it can be closed once the connection is made.
// Session storage because the side panel can be closed and reopened while the person is
// still approving access.
export const connectTabs = storage.defineItem<Partial<Record<Provider, number>>>('session:connectTabs', {
  fallback: {},
});

export const recorderState = storage.defineItem<RecorderState>('session:recorder', {
  fallback: { status: 'idle' },
});
