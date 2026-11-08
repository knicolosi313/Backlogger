// Screenshot-only fixtures. No real backend, account, credentials, or recordings.
const scenario = new URLSearchParams(location.search).get('view') ?? 'record';
const settings = {
  apiUrl: 'https://demo.backlogger.invalid', apiToken: '', webAppUrl: '',
  creativity: 'balanced', model: 'gemini-3.1-flash-lite', autoImport: false,
  autoImportThreshold: 0.85, targetList: 'Triage', targetListId: 'triage',
  consentReminder: true,
  selectedBoard: { id: 'demo-board', externalId: 'demo-external', name: 'Product Development', provider: 'trello' },
};
const values: Record<string, unknown> = { 'local:settings': settings, 'session:recorder': { status: 'idle' } };
const listeners = new Map<string, Set<(value: unknown, old: unknown) => void>>();
Object.assign(globalThis, {
  storage: {
    defineItem(key: string, options: { fallback: unknown }) {
      if (!(key in values)) values[key] = options.fallback;
      return {
        getValue: async () => values[key],
        setValue: async (value: unknown) => {
          const previous = values[key]; values[key] = value;
          listeners.get(key)?.forEach((listener) => listener(value, previous));
        },
        watch(listener: (value: unknown, old: unknown) => void) {
          if (!listeners.has(key)) listeners.set(key, new Set());
          listeners.get(key)!.add(listener);
          return () => listeners.get(key)!.delete(listener);
        },
      };
    },
  },
  browser: { runtime: { sendMessage: async () => ({}) }, tabs: { query: async () => [{ title: 'Sprint planning' }], remove: async () => {}, create: async () => ({}) } },
});

const titles = ['Email notifications', 'Search and filter stories', 'Export meeting summaries', 'Manage notification preferences', 'Keyboard shortcuts'];
const stories = titles.map((title, index) => ({
  id: `story-${index}`, meeting_id: 'meeting-0', status: 'draft', confidence: [0.94, 0.91, 0.88, 0.82, 0.78][index],
  invest_score: index < 3 ? 6 : 5,
  draft: {
    title, origin: 'discussed', assumptions: [], as_a: 'team member',
    i_want: index === 0 ? 'receive an email when a story is assigned to me' : title.toLowerCase(),
    so_that: index === 0 ? 'I can follow up on my work without constantly checking the board' : 'I can keep my team aligned on the next steps',
    acceptance_criteria: ['Send an email when a story is assigned to me.', 'Include the story title and a link to the board.', 'Do not send a second email when an assignment is unchanged.'],
    solution_tasks: ['Handle story assignment events.', 'Add and test the notification email template.'],
    priority: index < 2 ? 'high' : 'medium', estimate: index < 2 ? 3 : 5,
    needs_clarification: index === 4,
    source: { quote: 'When someone assigns me a story, send me an email with its title and a link. We should avoid duplicate emails.', start_s: 122, end_s: 133, speaker: 'Speaker 1' },
  },
  critique: {
    invest_score: index < 3 ? 6 : 5, confidence: [0.94, 0.91, 0.88, 0.82, 0.78][index],
    flags: index < 3 ? [] : [{ criterion: 'Testable', issue: 'Confirm the expected behavior with the team.' }],
    recommend_split: false, recommend_reject: false,
  },
}));
const meetings = ['Sprint planning', 'Product discovery', 'Backlog refinement', 'Team retrospective', 'Roadmap review'].map((title, index) => ({
  id: `meeting-${index}`, board_id: 'demo-board', title, status: 'completed', media_key: `demo-${index}.webm`,
  created_at: `2026-11-0${8 - index}T14:00:00Z`,
}));
// Keep the list filters and analytics totals consistent across screenshots.
for (const [status, count] of Object.entries({ accepted: 6, imported: 10, rejected: 2, needs_changes: 2 })) {
  for (let index = 0; index < count; index++) stories.push({
    ...stories[index % 5], id: `${status}-${index}`, status,
    draft: { ...stories[index % 5].draft, title: `${titles[index % 5]} follow-up ${index + 1}` },
  });
}

globalThis.fetch = async (input) => {
  const url = new URL(String(input));
  const path = url.pathname;
  let body: unknown;
  if (url.hostname !== 'demo.backlogger.invalid') throw new Error('Screenshot harness blocks external requests.');
  if (path === '/api/models') body = { default: settings.model, models: [settings.model, 'gemini-2.5-flash'] };
  else if (path === '/api/integrations/trello/connection') body = { connected: true, username: 'demo-team', expires_at: null };
  else if (path === '/api/integrations/jira/connection') body = { connected: false };
  else if (path === '/api/integrations/trello/boards') body = [{ id: 'demo-external', name: 'Product Development' }];
  else if (path.endsWith('/lists')) body = [{ id: 'triage', name: 'Triage' }, { id: 'backlog', name: 'Backlog' }];
  else if (path === '/api/meetings') body = meetings;
  else if (path.endsWith('/stories')) body = stories;
  else if (path.endsWith('/imports')) body = [];
  else if (path.endsWith('/analytics')) body = {
    story_counts: { draft: 5, needs_changes: 2, accepted: 6, imported: 10, rejected: 2 },
    acceptance_rate: 0.8, average_invest_score: 5.1,
    invest_issues: { independent: 1, negotiable: 0, valuable: 0, estimable: 1, small: 2, testable: 3 },
    needs_attention: [{ story_id: 'story-4', title: 'Keyboard shortcuts', reason: 'Confirm the expected behavior with the team.' }],
  };
  else throw new Error(`Missing screenshot fixture: ${path}`);
  return new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
};

export { scenario };
