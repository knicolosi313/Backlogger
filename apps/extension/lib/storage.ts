import type { RecorderState, Settings } from './types';

// 'local:' is kept on disk, 'session:' is cleared when the browser closes

export const DEFAULT_SETTINGS: Settings = {
  apiUrl: 'https://backlogger-production.up.railway.app',
  apiToken: '',
  webAppUrl: 'http://localhost:5173',

  creativity: 'balanced',
  model: 'gemini-2.5-flash',

  autoImport: false,
  autoImportThreshold: 0.85,
  targetList: 'Triage',

  consentReminder: true,
};

export const settingsItem = storage.defineItem<Settings>('local:settings', {
  fallback: DEFAULT_SETTINGS,
});

export const recorderState = storage.defineItem<RecorderState>('session:recorder', {
  fallback: { status: 'idle' },
});
