// ---- settings (kept in browser storage, see storage.ts) ----

export type Creativity = 'conservative' | 'balanced' | 'creative' | 'exploratory';

export interface Settings {
  apiUrl: string;
  apiToken: string;
  webAppUrl: string;

  creativity: Creativity;
  model: string;

  autoImport: boolean;
  autoImportThreshold: number; // 0 to 1, compared with story confidence

  selectedBoard?: SelectedBoard;
  // the Trello list imports go to. The backend wants the id, the name is just for display
  targetList: string;
  targetListId?: string;

  consentReminder: boolean;
}

export interface SelectedBoard {
  id: string; // our database id
  externalId: string; // Trello's id
  name: string;
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

export interface BoardConnection {
  connected: boolean;
  boardId?: string; // our database id
  externalBoardId?: string; // Trello's id
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

export type StoryDecision = 'accept' | 'reject' | 'restore';

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
