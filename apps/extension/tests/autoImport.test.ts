import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiError, request } from '@/lib/http';
import { getAutoImportStatus, startProcessing } from '@/lib/api';
import { buildAdditionalFields, getJiraConnection, listJiraRequiredFields } from '@/lib/jira';
import type { Settings } from '@/lib/types';

vi.mock('@/lib/http', async (original) => ({ ...await original<typeof import('@/lib/http')>(), request: vi.fn() }));
vi.mock('@/lib/jira', () => ({
  buildAdditionalFields: vi.fn(), getJiraConnection: vi.fn(), listJiraRequiredFields: vi.fn(),
  jiraFieldContext: vi.fn(() => 'jira-context'),
  parseJiraBoardExternalId: vi.fn(() => ({ siteId: 'site-id', projectId: '10001' })),
}));

function settings(): Settings {
  return {
    apiUrl: 'https://api.example.test', apiToken: '', webAppUrl: 'http://unused.example.test',
    creativity: 'balanced', model: 'test-model', autoImport: true, autoImportThreshold: 0.85,
    targetList: 'Triage', targetListId: 'list1', consentReminder: true,
    selectedBoard: { id: 'board1', externalId: 'external1', name: 'Product', provider: 'trello' },
  };
}

beforeEach(() => {
  vi.mocked(request).mockReset().mockImplementation(async (_config, path) => {
    if (path === '/api/meetings/meeting1') return { id: 'meeting1', board_id: 'board1' };
    return {};
  });
  vi.mocked(getJiraConnection).mockReset().mockResolvedValue({ connected: true, accountId: 'user1' });
  vi.mocked(listJiraRequiredFields).mockReset().mockResolvedValue([]);
  vi.mocked(buildAdditionalFields).mockReset().mockReturnValue({ fields: { customfield_123: 'chosen-value' }, missing: [] });
});
afterEach(() => vi.clearAllMocks());

it('sends the opted-in threshold and Trello destination to the backend', async () => {
  const config = settings();
  await startProcessing(config, 'meeting1', 'balanced');
  expect(request).toHaveBeenLastCalledWith(config, '/api/meetings/meeting1/process', {
    method: 'POST', json: { creativity: 'balanced', auto_import: {
      confidence_threshold: 0.85, list_id: 'list1', evidence_base_url: 'https://api.example.test',
    } },
  });
});

it('does not enable automatic exports when the toggle is off', async () => {
  const config = { ...settings(), autoImport: false };
  await startProcessing(config, 'meeting1', 'exploratory');
  expect(request).toHaveBeenLastCalledWith(config, '/api/meetings/meeting1/process', {
    method: 'POST', json: { creativity: 'exploratory' },
  });
});

it('resolves Jira fields before submitting automatic processing', async () => {
  const config = settings();
  config.selectedBoard = { ...config.selectedBoard!, provider: 'jira', externalId: 'site-id:10001' };
  config.jiraIssueType = { id: '10002', name: 'Story' };
  await startProcessing(config, 'meeting1', 'creative');
  expect(request).toHaveBeenLastCalledWith(config, '/api/meetings/meeting1/process', {
    method: 'POST', json: { creativity: 'creative', auto_import: {
      confidence_threshold: 0.85, issue_type_id: '10002', additional_fields: { customfield_123: 'chosen-value' }, evidence_base_url: 'https://api.example.test',
    } },
  });
});

it('stops before processing if Jira required fields are missing', async () => {
  const config = settings();
  config.selectedBoard = { ...config.selectedBoard!, provider: 'jira', externalId: 'site-id:10001' };
  config.jiraIssueType = { id: '10002', name: 'Story' };
  vi.mocked(buildAdditionalFields).mockReturnValue({ fields: {}, missing: ['Team'] });
  await expect(startProcessing(config, 'meeting1', 'balanced')).rejects.toThrow('Jira needs Team');
  expect(vi.mocked(request).mock.calls.some((call) => call[1].endsWith('/process'))).toBe(false);
});

it('does not import a meeting into a newly selected different board', async () => {
  const config = settings();
  config.selectedBoard!.id = 'other-board';
  await expect(startProcessing(config, 'meeting1', 'balanced')).rejects.toThrow('this meeting');
  expect(request).toHaveBeenCalledTimes(1);
});

it('does not start processing until a destination is selected', async () => {
  const config = settings();
  delete config.targetListId;
  await expect(startProcessing(config, 'meeting1', 'balanced')).rejects.toThrow('Pick a Trello list');
  expect(request).toHaveBeenCalledTimes(1);
});

it('treats a missing auto-import run as disabled, but preserves account errors', async () => {
  vi.mocked(request).mockRejectedValueOnce(new ApiError('Not enabled', 404));
  await expect(getAutoImportStatus(settings(), 'meeting1')).resolves.toBeNull();
  vi.mocked(request).mockRejectedValueOnce(new ApiError('Wrong account', 403));
  await expect(getAutoImportStatus(settings(), 'meeting1')).rejects.toThrow('Wrong account');
});
