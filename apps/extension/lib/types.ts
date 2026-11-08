// ---- settings (kept in browser storage, see storage.ts) ----

export type Creativity = 'conservative' | 'balanced' | 'creative' | 'exploratory';

export interface AutoImportStatus {
  meeting_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  imported: number;
  skipped: number;
  failed: number;
  error: string | null;
  updated_at: string;
}

export interface Settings {
  apiUrl: string;
  apiToken: string;
  webAppUrl: string;

  creativity: Creativity;
  model: string; // one of the backend's models (GET /api/models), or '' for its default

  autoImport: boolean;
  autoImportThreshold: number; // 0 to 1, compared with story confidence

  // The board (Trello) or project (Jira) meetings belong to. Only one provider is connected
  // at a time, so there's only one of these.
  selectedBoard?: SelectedBoard;
  // the Trello list imports go to. The backend wants the id, the name is just for display
  targetList: string;
  targetListId?: string;

  // Jira only: the site being browsed, and the issue type imports are created as
  jiraSite?: JiraSite;
  jiraIssueType?: JiraIssueType;
  // What was typed into the project's required fields. `for` names the project and issue type
  // they were entered for, so values for another one are ignored (see jiraFieldContext).
  jiraFieldValues?: { for: string; values: JiraFieldValues };

  consentReminder: boolean;
}

// Where stories get exported to. Only one is connected at a time.
export type Provider = 'trello' | 'jira';

export interface SelectedBoard {
  id: string; // our database id
  externalId: string; // Trello's board id, or Jira's "siteId:projectId"
  name: string;
  // settings saved before Jira support have no provider, and those were all Trello
  provider?: Provider;
  siteUrl?: string; // Jira only, for linking to issues
}

export interface TrelloBoard {
  id: string;
  name: string;
  url?: string | null;
}

export interface TrelloList {
  id: string;
  name: string;
}

export interface TrelloConnection {
  connected: boolean;
  username?: string | null;
  expiresAt?: string | null;
}

export interface JiraConnection {
  connected: boolean;
  displayName?: string | null;
  accountId?: string | null;
  expiresAt?: string | null;
}

export interface JiraSite {
  id: string; // the site's cloud id
  name: string;
  url: string;
}

export interface JiraProject {
  id: string;
  key: string;
  name: string;
}

export interface JiraIssueType {
  id: string;
  name: string;
}

// A field Jira insists on when creating an issue, beyond the ones we fill in ourselves.
// `kind` is how we ask for it, and decides the shape of the value we send (see jira.ts).
export type JiraFieldKind =
  | 'text'
  | 'richtext'
  | 'number'
  | 'date'
  | 'datetime'
  | 'select'
  | 'user'
  | 'labels'
  | 'json'; // anything else: the person types the JSON Jira wants

export interface JiraField {
  id: string; // like "customfield_10010"
  name: string;
  kind: JiraFieldKind;
  multiple: boolean; // select only: more than one choice allowed
  options: Array<{ id: string; label: string }>; // select only
}

// What was typed for each field, by field id. Multi-select is a list, everything else text.
export type JiraFieldValues = Record<string, string | string[]>;

export interface BoardConnection {
  connected: boolean;
  boardId?: string; // our database id
  externalBoardId?: string; // Trello's id, or Jira's "siteId:projectId"
  boardName?: string;
}

// ---- recording and processing ----

// The backend only reports uploaded, processing, completed and failed. The in-between
// stages are here for when it starts reporting them.
export type ProcessingStatus =
  | 'awaiting_audio'
  | 'queued'
  | 'processing'
  | 'transcribing'
  | 'extracting'
  | 'drafting'
  | 'critiquing'
  | 'ready'
  | 'failed';

// What the Record tab shows. It lives in session storage so the panel and the
// background worker both see it. The audio itself waits in IndexedDB (pendingAudio.ts).
export type RecorderState =
  | { status: 'idle' }
  | { status: 'created'; meetingId: string; title: string; error?: string }
  | { status: 'recording'; meetingId: string; title: string; startedAt: number }
  | {
      status: 'recorded';
      meetingId: string;
      title: string;
      source: 'recording' | 'file';
      fileName: string;
      sizeBytes: number;
      durationSeconds?: number; // only known for recordings
      error?: string;
    }
  | { status: 'uploading'; meetingId: string; title: string }
  | { status: 'processing'; meetingId: string; processingStatus: ProcessingStatus }
  | { status: 'done'; meetingId: string }
  | { status: 'error'; message: string };

export interface MeetingSummary {
  id: string;
  title: string;
  startedAt: string; // ISO date
  hasAudio: boolean;
  status: ProcessingStatus;
  // the backend doesn't send these yet
  durationSeconds?: number;
  storyCount?: number;
}

// ---- stories ----

// draft -> (needs_changes) -> accepted -> imported, or rejected
export type StoryStatus = 'draft' | 'needs_changes' | 'accepted' | 'imported' | 'rejected';

export type Priority = 'high' | 'med' | 'low';

export const INVEST_KEYS = ['independent', 'negotiable', 'valuable', 'estimable', 'small', 'testable'] as const;
export type InvestKey = (typeof INVEST_KEYS)[number];

export type InvestResult = 'pass' | 'warn' | 'fail';

export interface InvestCheck {
  key: InvestKey;
  result: InvestResult;
  explanation: string;
  fix?: string;
}

// the moment in the meeting that backs up a story
export interface Evidence {
  startSeconds: number;
  endSeconds: number;
  speaker: string;
  quote: string;
}

export interface Story {
  id: string;
  meetingId: string;
  status: StoryStatus;

  title: string;
  origin?: 'discussed' | 'inferred' | 'proposed';
  assumptions?: string[];
  persona: string;
  goal: string;
  benefit: string;
  acceptanceCriteria: string[];
  priority: Priority;
  points: number | null;

  evidence: Evidence[];

  // Confidence is how well the story matches what was said. Quality is how it scores on
  // INVEST. Both are null after a manual edit because the old scores no longer apply.
  confidence: number | null; // 0 to 1
  qualityScore: number | null; // 0 to 100
  investChecks: InvestCheck[];

  critique: string;
  openQuestions: string[];
  unsupportedAssumptions: string[];

  reviewerNote?: string;
  cardUrl?: string;
}

export type StoryEdit = Partial<
  Pick<Story, 'title' | 'persona' | 'goal' | 'benefit' | 'acceptanceCriteria' | 'priority' | 'points'>
>;

// What came of asking the AI to change a draft. `newStories` is only filled when the request was to
// split it: the first part stays as `story` and the rest are new drafts on the same meeting.
export interface EditRequestResult {
  story: Story;
  newStories: Story[];
  summary: string;
}

export type StoryDecision ='accept' | 'reject' | 'restore';

export interface ImportResult {
  storyId: string;
  cardUrl: string;
  duplicate: boolean;
}

// One saved export attempt. "pending" with no card id means it was interrupted, and
// exporting again picks it back up without making a second card.
export type ExportStatus = 'pending' | 'completed' | 'failed';

export interface ExportRecord {
  id: string;
  storyId: string;
  storyTitle: string | null;
  idempotencyKey: string;
  externalCardId: string | null;
  status: ExportStatus;
  createdAt: string;
}

// ---- analytics ----

export interface BoardAnalytics {
  boardHealth: number | null; // 0 to 100
  acceptanceRate: number | null; // accepted / reviewed, null until something is reviewed
  counts: Record<StoryStatus, number>;
  investIssues: Record<InvestKey, number>;
  needsAttention: Array<{ storyId: string; title: string; reason: string }>;
}
