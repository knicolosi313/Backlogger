import type { Creativity, ExportStatus, InvestKey, ProcessingStatus, StoryStatus } from './types';

export const CREATIVITY_OPTIONS: Array<{ value: Creativity; label: string; help: string }> = [
  { value: 'conservative', label: 'Conservative', help: 'Only what was clearly said in the meeting.' },
  { value: 'balanced', label: 'Balanced (default)', help: 'Stated requests plus strongly implied needs and useful story splits.' },
  { value: 'creative', label: 'Creative', help: 'Adds useful adjacent ideas as labeled proposals.' },
  { value: 'exploratory', label: 'Exploratory', help: 'Reaches widely for many story ideas, edge cases, and extensions. Proposals need confirmation.' },
];

export const PROCESSING_LABELS: Record<ProcessingStatus, string> = {
  awaiting_audio: 'No audio yet',
  queued: 'Waiting in line',
  processing: 'Processing the meeting',
  transcribing: 'Transcribing the audio',
  extracting: 'Finding candidate stories',
  drafting: 'Writing user stories',
  critiquing: 'Checking story quality (INVEST)',
  ready: 'Ready to review',
  failed: 'Processing failed',
};

// Audio types the backend accepts (services/api/media.py)
export const SUPPORTED_AUDIO_TYPES = [
  'audio/webm',
  'audio/wav',
  'audio/x-wav',
  'audio/mpeg',
  'audio/mp3',
  'audio/mp4',
  'audio/ogg',
  'audio/flac',
  'audio/x-flac',
];

const AUDIO_TYPE_BY_EXTENSION: Record<string, string> = {
  webm: 'audio/webm',
  wav: 'audio/wav',
  mp3: 'audio/mpeg',
  m4a: 'audio/mp4',
  mp4: 'audio/mp4',
  ogg: 'audio/ogg',
  oga: 'audio/ogg',
  flac: 'audio/flac',
};

// Extensions are listed too because the file dialog goes by the type the OS reports, and
// that's video/webm for audio-only .webm files, which would be greyed out.
export const AUDIO_FILE_ACCEPT = [
  ...SUPPORTED_AUDIO_TYPES,
  ...Object.keys(AUDIO_TYPE_BY_EXTENSION).map((ext) => `.${ext}`),
].join(',');

// Browsers call audio-only .webm and .mp4 files video/webm and video/mp4 (or leave the type
// empty), but the backend only takes audio types. So if the browser's type isn't one we know,
// go by the extension. Returns null for unsupported files.
export function audioTypeFor(file: File): string | null {
  const reported = file.type.split(';', 1)[0]!.trim().toLowerCase();
  if (SUPPORTED_AUDIO_TYPES.includes(reported)) return reported;
  const extension = file.name.split('.').pop()?.toLowerCase() ?? '';
  return AUDIO_TYPE_BY_EXTENSION[extension] ?? null;
}

export const EXPORT_STATUS_LABELS: Record<ExportStatus, string> = {
  pending: 'Pending',
  completed: 'Exported',
  failed: 'Failed',
};

export const STORY_STATUS_LABELS: Record<StoryStatus, string> = {
  draft: 'Draft',
  needs_changes: 'Needs changes',
  accepted: 'Accepted',
  imported: 'Imported',
  rejected: 'Rejected',
};

export const INVEST_LABELS: Record<InvestKey, string> = {
  independent: 'Independent',
  negotiable: 'Negotiable',
  valuable: 'Valuable',
  estimable: 'Estimable',
  small: 'Small',
  testable: 'Testable',
};
