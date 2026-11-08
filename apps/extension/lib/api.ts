import { ApiError, request, type ApiConfig } from './http';
import type {
  AutoImportStatus,
  BoardAnalytics,
  Creativity,
  ExportRecord,
  EditRequestResult,
  ExportStatus,
  ImportResult,
  InvestCheck,
  InvestKey,
  MeetingSummary,
  ProcessingStatus,
  Provider,
  SelectedBoard,
  Settings,
  Story,
  StoryDecision,
  StoryEdit,
  StoryStatus,
} from './types';
import { INVEST_KEYS } from './types';
import {
  buildAdditionalFields,
  getJiraConnection,
  jiraFieldContext,
  listJiraRequiredFields,
  parseJiraBoardExternalId,
} from './jira';
import { boardProvider, cardUrl, importDestination } from './provider';

// Everything here talks to our FastAPI backend. Trello itself is in trello.ts.
// IDs are the backend's UUIDs unless the name says "external".

export type { ApiConfig };

// ---- boards ----

interface BoardWire {
  id: string;
  external_id: string;
  name: string;
}

function toSelectedBoard(board: BoardWire, provider: Provider): SelectedBoard {
  return { id: board.id, externalId: board.external_id, name: board.name, provider };
}

// `externalId` is Trello's board id, or Jira's "siteId:projectId" (see jira.ts)
export async function registerBoard(
  cfg: ApiConfig,
  board: { provider: Provider; externalId: string; name: string },
): Promise<SelectedBoard> {
  const { provider, externalId } = board;
  try {
    const created = await request<BoardWire>(cfg, '/api/boards', {
      method: 'POST',
      json: { provider, external_id: externalId, name: board.name },
    });
    return toSelectedBoard(created, provider);
  } catch (err) {
    // 409 means it's already registered, so go find it
    if (!(err instanceof ApiError) || err.status !== 409) throw err;
  }

  const pageSize = 100;
  for (let offset = 0; ; offset += pageSize) {
    const page = await request<Array<BoardWire & { provider: string }>>(
      cfg,
      `/api/boards?limit=${pageSize}&offset=${offset}`,
    );
    const existing = page.find((b) => b.provider === provider && b.external_id === externalId);
    if (existing) return toSelectedBoard(existing, provider);
    if (page.length < pageSize) throw new ApiError('This board is registered, but it could not be found.', 404);
  }
}

// ---- meetings ----

interface MeetingWire {
  id: string;
  board_id: string;
  title: string;
  status: 'uploaded' | 'processing' | 'completed' | 'failed';
  media_key: string | null;
  created_at: string;
}

// The backend only has four statuses. A new meeting is "uploaded" too, until it gets audio.
function meetingStatus(meeting: MeetingWire): ProcessingStatus {
  switch (meeting.status) {
    case 'uploaded':
      return meeting.media_key ? 'queued' : 'awaiting_audio';
    case 'processing':
      return 'processing';
    case 'completed':
      return 'ready';
    case 'failed':
      return 'failed';
  }
}

export async function createMeeting(
  cfg: ApiConfig,
  meta: { boardId: string; title: string },
): Promise<{ meetingId: string }> {
  const meeting = await request<MeetingWire>(cfg, '/api/meetings', {
    method: 'POST',
    json: { board_id: meta.boardId, title: meta.title },
  });
  return { meetingId: meeting.id };
}

// A meeting holds one audio file, so uploading again replaces it.
export async function uploadMedia(cfg: ApiConfig, meetingId: string, audio: Blob, fileName: string): Promise<void> {
  const form = new FormData();
  form.append('file', audio, fileName);
  await request(cfg, `/api/meetings/${encodeURIComponent(meetingId)}/media`, { method: 'POST', form });
}

// Refresh the meeting's board, which may differ from the board currently selected
// in Settings. A failed refresh stays visible so we don't silently use stale work.
// Processing snapshots auto-import settings; the backend keeps working if the panel closes.
// An empty `model` means the person didn't pick one, so the backend uses its own default.
export async function startProcessing(
  cfg: Settings,
  meetingId: string,
  creativity: Creativity,
  model = '',
): Promise<void> {
  const meeting = await request<MeetingWire>(cfg, `/api/meetings/${encodeURIComponent(meetingId)}`);
  let autoImport: Record<string, unknown> | undefined;
  if (cfg.autoImport) {
    const board = cfg.selectedBoard;
    if (!board || board.id !== meeting.board_id) throw new Error('Select this meeting’s board in Settings before enabling auto-import.');
    if (!Number.isFinite(cfg.autoImportThreshold) || cfg.autoImportThreshold < 0.5 || cfg.autoImportThreshold > 1) {
      throw new Error('Auto-import confidence must be between 50% and 100%.');
    }
    const destination = importDestination(cfg).id;
    if (!destination) throw new Error(boardProvider(board) === 'jira' ? 'Pick a Jira issue type in Settings.' : 'Pick a Trello list in Settings.');
    autoImport = {
      confidence_threshold: cfg.autoImportThreshold,
      evidence_base_url: cfg.apiUrl,
      ...(boardProvider(board) === 'jira'
        ? { issue_type_id: destination, additional_fields: await jiraFieldsFor(cfg, board, destination) }
        : { list_id: destination }),
    };
  }
  await request(cfg, `/api/boards/${encodeURIComponent(meeting.board_id)}/context/sync`, { method: 'POST' });
  await request(cfg, `/api/meetings/${encodeURIComponent(meetingId)}/process`, {
    method: 'POST',
    json: { creativity, ...(model ? { model } : {}), ...(autoImport ? { auto_import: autoImport } : {}) },
  });
}

export function getMeetingAudio(cfg: ApiConfig, meetingId: string, signal?: AbortSignal): Promise<Blob> {
  return request<Blob>(cfg, `/api/meetings/${encodeURIComponent(meetingId)}/audio`, {
    responseType: 'blob', signal,
  });
}

export async function getModels(cfg: ApiConfig): Promise<{ default: string; models: string[] }> {
  return request<{ default: string; models: string[] }>(cfg, '/api/models');
}

export async function getProcessingStatus(cfg: ApiConfig, meetingId: string): Promise<ProcessingStatus> {
  const meeting = await request<MeetingWire>(cfg, `/api/meetings/${encodeURIComponent(meetingId)}`);
  return meetingStatus(meeting);
}

export async function getAutoImportStatus(cfg: ApiConfig, meetingId: string): Promise<AutoImportStatus | null> {
  try {
    return await request<AutoImportStatus>(cfg, `/api/meetings/${encodeURIComponent(meetingId)}/auto-import`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export function retryAutoImport(cfg: ApiConfig, meetingId: string): Promise<AutoImportStatus> {
  return request<AutoImportStatus>(cfg, `/api/meetings/${encodeURIComponent(meetingId)}/auto-import/retry`, { method: 'POST' });
}

export async function countStoriesForMeeting(cfg: ApiConfig, meetingId: string): Promise<number> {
  const meeting = await request<MeetingWire>(cfg, `/api/meetings/${encodeURIComponent(meetingId)}`);
  return countMeetingStories(cfg, meeting.board_id, meetingId);
}

export async function listMeetings(cfg: ApiConfig, boardId: string): Promise<MeetingSummary[]> {
  const meetings = await request<MeetingWire[]>(cfg, `/api/meetings?board_id=${encodeURIComponent(boardId)}&limit=5`);
  return meetings.map((m) => ({
    id: m.id,
    title: m.title,
    startedAt: m.created_at,
    hasAudio: Boolean(m.media_key),
    status: meetingStatus(m),
  }));
}

// ---- stories ----

interface DraftWire {
  title: string;
  origin?: Story['origin'];
  assumptions?: string[];
  as_a: string;
  i_want: string;
  so_that: string;
  acceptance_criteria: string[];
  epic?: string | null;
  related_story_ids?: string[];
  priority: Story['priority'];
  estimate?: number | null;
  needs_clarification: boolean;
  source: { quote: string; start_s: number; end_s: number; speaker: string };
}

interface ReviewWire {
  invest_score: number;
  confidence: number;
  flags: Array<{ criterion: string; issue: string; quote?: string | null }>;
  suggested_title?: string | null;
  suggested_acceptance_criteria?: string[] | null;
  recommend_split: boolean;
  split_suggestion?: string | null;
  recommend_reject: boolean;
}

interface StoryWire {
  id: string;
  meeting_id: string;
  status: StoryStatus;
  draft: DraftWire;
  confidence: number | null;
  invest_score: number | null; // 0 to 6
  critique: ReviewWire | null; // null once a human edits the story
}

const INVEST_BY_CRITERION: Record<string, InvestKey> = {
  Independent: 'independent',
  Negotiable: 'negotiable',
  Valuable: 'valuable',
  Estimable: 'estimable',
  Small: 'small',
  Testable: 'testable',
};

function toStory(wire: StoryWire): Story {
  const { draft, critique } = wire;
  const flags = critique?.flags ?? [];

  const investChecks: InvestCheck[] = critique
    ? INVEST_KEYS.map((key) => {
        const issues = flags.filter((f) => INVEST_BY_CRITERION[f.criterion] === key).map((f) => f.issue);
        if (issues.length === 0) return { key, result: 'pass', explanation: '' };
        return {
          key,
          result: 'warn',
          explanation: issues.join(' '),
          fix: key === 'small' && critique.split_suggestion ? critique.split_suggestion : undefined,
        };
      })
    : [];

  let critiqueText: string;
  if (!critique) {
    critiqueText = 'This story was edited after the AI review, so it has not been scored.';
  } else {
    const lines = flags.map((f) => `${f.criterion}: ${f.issue}`);
    if (critique.recommend_reject) lines.push('The AI recommends rejecting this story.');
    if (critique.recommend_split && critique.split_suggestion) lines.push(`Suggested split: ${critique.split_suggestion}`);
    if (critique.suggested_title) lines.push(`Suggested title: ${critique.suggested_title}`);
    critiqueText = lines.length ? lines.join('\n') : 'The AI found no issues.';
  }

  return {
    id: wire.id,
    meetingId: wire.meeting_id,
    status: wire.status,
    title: draft.title,
    origin: draft.origin ?? 'discussed',
    assumptions: draft.assumptions ?? [],
    persona: draft.as_a,
    goal: draft.i_want,
    benefit: draft.so_that,
    acceptanceCriteria: draft.acceptance_criteria,
    priority: draft.priority,
    points: draft.estimate ?? null,
    evidence: draft.source.quote.trim()
      ? [{
          startSeconds: draft.source.start_s,
          endSeconds: draft.source.end_s,
          speaker: draft.source.speaker,
          quote: draft.source.quote,
        }]
      : [],
    confidence: wire.confidence,
    qualityScore: wire.invest_score === null ? null : Math.round((wire.invest_score / 6) * 100),
    investChecks,
    critique: draft.related_story_ids?.length
      ? `${critiqueText}\nRelated existing work (check for overlap): ${draft.related_story_ids.join(', ')}`
      : critiqueText,
    openQuestions: draft.needs_clarification ? ['The AI marked this story as needing clarification.'] : [],
    unsupportedAssumptions: flags.filter((f) => f.criterion === 'Grounded').map((f) => f.issue),
  };
}

export async function listStories(cfg: ApiConfig, selected: SelectedBoard): Promise<Story[]> {
  const board = `/api/boards/${encodeURIComponent(selected.id)}`;
  const [stories, imports] = await Promise.all([
    request<StoryWire[]>(cfg, `${board}/stories?limit=100`),
    // only used for card links, so don't let a failure here hide the stories
    request<ImportWire[]>(cfg, `${board}/imports?status=completed&limit=100`).catch(() => [] as ImportWire[]),
  ]);
  const cards = new Map(imports.map((i) => [i.story_id, i.external_card_id]));
  return stories.map((wire) => {
    const story = toStory(wire);
    const cardId = cards.get(wire.id);
    const url = cardId ? cardUrl(selected, cardId) : undefined;
    return url ? { ...story, cardUrl: url } : story;
  });
}

export async function countMeetingStories(cfg: ApiConfig, boardId: string, meetingId: string): Promise<number> {
  const query = `meeting_id=${encodeURIComponent(meetingId)}&limit=100`;
  const stories = await request<StoryWire[]>(cfg, `/api/boards/${encodeURIComponent(boardId)}/stories?${query}`);
  return stories.length;
}

// The endpoint wants the whole draft, so fetch the current one and merge the edit into it.
export async function updateStory(cfg: ApiConfig, storyId: string, edit: StoryEdit): Promise<Story> {
  const path = `/api/stories/${encodeURIComponent(storyId)}`;
  const { draft } = await request<StoryWire>(cfg, path);
  const merged: DraftWire = {
    ...draft,
    title: edit.title ?? draft.title,
    as_a: edit.persona ?? draft.as_a,
    i_want: edit.goal ?? draft.i_want,
    so_that: edit.benefit ?? draft.so_that,
    acceptance_criteria: edit.acceptanceCriteria ?? draft.acceptance_criteria,
    priority: edit.priority ?? draft.priority,
    estimate: 'points' in edit ? edit.points : draft.estimate,
  };
  return toStory(await request<StoryWire>(cfg, path, { method: 'PATCH', json: { draft: merged } }));
}

interface EditRequestWire {
  story: StoryWire;
  new_stories: StoryWire[];
  summary: string;
}

// Asks the AI to rewrite the draft, then review the result again. That's several AI calls on the
// server, so it can take a while. An empty `model` means the server's default.
export async function requestEdits(
  cfg: ApiConfig,
  storyId: string,
  instructions: string,
  model: string,
): Promise<EditRequestResult> {
  const wire = await request<EditRequestWire>(cfg, `/api/stories/${encodeURIComponent(storyId)}/request-edits`, {
    method: 'POST',
    json: { instructions, ...(model ? { model } : {}) },
  });
  return { story: toStory(wire.story), newStories: wire.new_stories.map(toStory), summary: wire.summary };
}

// Only rejected drafts can be deleted. They disappear from the app but stay in the database.
export async function deleteStory(cfg: ApiConfig, storyId: string): Promise<void> {
  await request(cfg, `/api/stories/${encodeURIComponent(storyId)}`, { method: 'DELETE' });
}

export async function decideStory(cfg: ApiConfig, storyId: string, decision: StoryDecision): Promise<Story> {
  const story = await request<StoryWire>(cfg, `/api/stories/${encodeURIComponent(storyId)}/decision`, {
    method: 'POST',
    json: { decision },
  });
  return toStory(story);
}

// ---- importing to Trello ----

// The values for the project's required fields, built from what was saved in Settings. The
// field list is fetched fresh so a change on the Jira side is noticed before any story is sent.
async function jiraFieldsFor(
  settings: Settings,
  board: SelectedBoard,
  issueTypeId: string,
): Promise<Record<string, unknown>> {
  const ids = parseJiraBoardExternalId(board.externalId);
  if (!ids) throw new Error('This Jira project is not set up correctly. Pick it again in Settings.');

  const [required, connection] = await Promise.all([
    listJiraRequiredFields(settings, ids.siteId, ids.projectId, issueTypeId),
    getJiraConnection(settings),
  ]);
  const saved = settings.jiraFieldValues;
  const values = saved?.for === jiraFieldContext(board.externalId, issueTypeId) ? saved.values : {};

  const { fields, missing } = buildAdditionalFields(required, values, connection.accountId);
  if (missing.length) {
    throw new Error(`Jira needs ${missing.join(', ')} before it will create issues. Fill them in under Jira in Settings.`);
  }
  return fields;
}

// One request per story. If some fail we still try the rest, then report how it went.
// The backend picks Trello or Jira from the board, but the destination field differs:
// Trello wants a list_id and Jira wants an issue_type_id.
export async function importStories(settings: Settings, stories: Story[]): Promise<ImportResult[]> {
  const board = settings.selectedBoard;
  if (!board) throw new Error('Pick a destination in Settings first.');

  const isJira = boardProvider(board) === 'jira';
  const destinationId = importDestination(settings).id;
  if (!destinationId) {
    throw new Error(isJira ? 'Pick a Jira issue type to import as in Settings.' : 'Pick a Trello list to import into in Settings.');
  }
  const destination = isJira
    ? { issue_type_id: destinationId, additional_fields: await jiraFieldsFor(settings, board, destinationId) }
    : { list_id: destinationId };

  const results: ImportResult[] = [];
  let firstError = '';
  let failed = 0;
  for (const story of stories) {
    try {
      const saved = await request<ImportWire>(settings, `/api/boards/${encodeURIComponent(board.id)}/imports`, {
        method: 'POST',
        json: {
          story_id: story.id,
          ...destination,
        },
      });
      results.push({
        storyId: story.id,
        cardUrl: (saved.external_card_id && cardUrl(board, saved.external_card_id)) || '',
        duplicate: story.status === 'imported',
      });
    } catch (err) {
      failed += 1;
      firstError ||= err instanceof Error ? err.message : 'Import failed.';
    }
  }
  if (failed) throw new Error(`${firstError} (${results.length} imported, ${failed} failed.)`);
  return results;
}

// ---- analytics ----

interface AnalyticsWire {
  story_counts: Record<string, number>;
  acceptance_rate: number | null;
  average_invest_score: number | null; // 0 to 6
  invest_issues: Record<string, number>;
  needs_attention: Array<{ story_id: string; title: string; reason: string }>;
}

// TODO: nobody's decided how board health is calculated. For now it's the average INVEST
// score as a percentage.
export async function getAnalytics(cfg: ApiConfig, boardId: string): Promise<BoardAnalytics> {
  const wire = await request<AnalyticsWire>(cfg, `/api/boards/${encodeURIComponent(boardId)}/analytics`);

  const counts = { draft: 0, needs_changes: 0, accepted: 0, imported: 0, rejected: 0, ...wire.story_counts };
  const investIssues = Object.fromEntries(
    INVEST_KEYS.map((key) => [key, wire.invest_issues[key] ?? 0]),
  ) as BoardAnalytics['investIssues'];

  return {
    boardHealth: wire.average_invest_score === null ? null : Math.round((wire.average_invest_score / 6) * 100),
    acceptanceRate: wire.acceptance_rate,
    counts,
    investIssues,
    needsAttention: wire.needs_attention.map((item) => ({
      storyId: item.story_id,
      title: item.title,
      reason: item.reason,
    })),
  };
}

// ---- export history (read only, never calls Trello) ----

interface ImportWire {
  id: string;
  story_id: string;
  idempotency_key: string;
  external_card_id: string | null;
  status: ExportStatus;
  created_at: string;
  story_title?: string | null;
}

function toExportRecord(record: ImportWire): ExportRecord {
  return {
    id: record.id,
    storyId: record.story_id,
    storyTitle: record.story_title ?? null,
    idempotencyKey: record.idempotency_key,
    externalCardId: record.external_card_id,
    status: record.status,
    createdAt: record.created_at,
  };
}

export async function listBoardImports(
  cfg: ApiConfig,
  boardId: string,
  options: { status?: ExportStatus; limit?: number; offset?: number } = {},
): Promise<ExportRecord[]> {
  const query = new URLSearchParams();
  if (options.status) query.set('status', options.status);
  if (options.limit !== undefined) query.set('limit', String(options.limit));
  if (options.offset !== undefined) query.set('offset', String(options.offset));
  const suffix = query.size ? `?${query}` : '';

  const records = await request<ImportWire[]>(cfg, `/api/boards/${encodeURIComponent(boardId)}/imports${suffix}`);
  return records.map(toExportRecord);
}

// The server answers 404 when a story has never been exported
export async function getStoryImport(cfg: ApiConfig, storyId: string): Promise<ExportRecord | null> {
  try {
    return toExportRecord(await request<ImportWire>(cfg, `/api/stories/${encodeURIComponent(storyId)}/import`));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}
