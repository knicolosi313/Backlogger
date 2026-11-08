import { beforeEach, expect, it, vi } from 'vitest';
import { importStories } from '@/lib/api';
import { request } from '@/lib/http';
import { buildAdditionalFields, getJiraConnection, listJiraRequiredFields } from '@/lib/jira';
import type { Settings, Story } from '@/lib/types';

vi.mock('@/lib/http', () => ({ request: vi.fn() }));
vi.mock('@/lib/jira', () => ({
  buildAdditionalFields: vi.fn(), getJiraConnection: vi.fn(), listJiraRequiredFields: vi.fn(),
  jiraFieldContext: () => 'context',
  parseJiraBoardExternalId: () => ({ siteId: 'site', projectId: '10001' }),
}));
const settings: Settings = {
  apiUrl: 'https://api.example.test', apiToken: '', webAppUrl: '', creativity: 'balanced', model: '',
  autoImport: false, autoImportThreshold: 0.85, targetList: '', consentReminder: true,
  selectedBoard: { id: 'board', externalId: 'site:10001', name: 'Demo', provider: 'jira', siteUrl: 'https://demo.atlassian.net' },
  jiraIssueType: { id: '10002', name: 'Story' },
};
const stories = Array.from({ length: 5 }, (_, index) => ({ id: `story-${index}`, status: 'accepted' }) as Story);

beforeEach(() => {
  vi.mocked(request).mockReset();
  vi.mocked(listJiraRequiredFields).mockReset().mockResolvedValue([]);
  vi.mocked(getJiraConnection).mockReset().mockResolvedValue({ connected: true, accountId: 'account' });
  vi.mocked(buildAdditionalFields).mockReset().mockReturnValue({ fields: { customfield_1: 'team' }, missing: [] });
});

it('exports Jira in batches of three and validates shared required fields once', async () => {
  const finish: Array<() => void> = [];
  vi.mocked(request).mockImplementation((_cfg, _path, init) => new Promise((resolve) => {
    finish.push(() => resolve({ external_card_id: `DEMO-${(init!.json as { story_id: string }).story_id.slice(-1)}` }));
  }));
  const pending = importStories(settings, stories);
  await vi.waitFor(() => expect(finish).toHaveLength(3));
  finish.slice(0, 3).forEach((resolve) => resolve());
  await vi.waitFor(() => expect(finish).toHaveLength(5));
  finish.slice(3).forEach((resolve) => resolve());
  const results = await pending;
  expect(results.map((result) => result.storyId)).toEqual(stories.map((story) => story.id));
  expect(results[0]!.cardUrl).toBe('https://demo.atlassian.net/browse/DEMO-0');
  expect(listJiraRequiredFields).toHaveBeenCalledTimes(1);
  for (const call of vi.mocked(request).mock.calls) expect(call[2]?.json).toMatchObject({ issue_type_id: '10002', additional_fields: { customfield_1: 'team' } });
});

it('continues Jira batches after a failure and reports partial success without replaying requests', async () => {
  vi.mocked(request).mockImplementation(async (_cfg, _path, init) => {
    if ((init!.json as { story_id: string }).story_id === 'story-1') throw new Error('Jira rate limit');
    return { external_card_id: 'DEMO-1' };
  });
  await expect(importStories(settings, stories)).rejects.toThrow('Jira rate limit (4 imported, 1 failed.)');
  expect(request).toHaveBeenCalledTimes(5);
});

it('does not send any export requests when Jira required fields are missing', async () => {
  vi.mocked(buildAdditionalFields).mockReturnValue({ fields: {}, missing: ['Team'] });
  await expect(importStories(settings, stories)).rejects.toThrow('Jira needs Team');
  expect(request).not.toHaveBeenCalled();
});
