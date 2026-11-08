import { STORY_STATUS_LABELS } from '@/lib/options';
import { formatPercent } from '@/lib/format';
import type { StoryStatus } from '@/lib/types';

// Small colored labels. Confidence and quality are separate badges on purpose:
// confidence = how well the story is grounded in the meeting,
// quality = how well it scores against INVEST.

type Level = 'good' | 'ok' | 'low';

function level(value: number, good: number, ok: number): Level {
  if (value >= good) return 'good';
  if (value >= ok) return 'ok';
  return 'low';
}

export function ConfidenceBadge({ value }: { value: number | null }) {
  if (value === null) {
    return <span className="badge low" title="Edited after the AI review, so not scored">Confidence –</span>;
  }
  return (
    <span
      className={`badge ${level(value, 0.8, 0.6)}`}
      title="Confidence: how well this story is grounded in what was said in the meeting"
    >
      Confidence {formatPercent(value)}
    </span>
  );
}

export function QualityBadge({ score }: { score: number | null }) {
  if (score === null) {
    return <span className="badge low" title="Edited after the AI review, so not scored">Quality –</span>;
  }
  return (
    <span className={`badge ${level(score, 80, 60)}`} title="Quality: how the story scores against INVEST">
      Quality {score}/100
    </span>
  );
}

export function StatusBadge({ status }: { status: StoryStatus }) {
  return <span className={`badge status-${status}`}>{STORY_STATUS_LABELS[status]}</span>;
}
