import { useState } from 'react';
import { getAnalytics, getStoryImport, listBoardImports } from '@/lib/api';
import { formatDateTime, formatPercent } from '@/lib/format';
import { EXPORT_STATUS_LABELS, INVEST_LABELS, STORY_STATUS_LABELS } from '@/lib/options';
import { PICK_DESTINATION_HINT, providerLabel } from '@/lib/provider';
import type { BoardConnection, ExportRecord, ExportStatus, Settings, StoryStatus } from '@/lib/types';
import { INVEST_KEYS } from '@/lib/types';
import { useAutoReload, useLoad } from '@/lib/useLoad';
import { Icon } from './Icon';

// how often the open tab re-reads the backend
const ANALYTICS_REFRESH_MS = 10_000;

interface Props {
  settings: Settings;
  board: BoardConnection;
  onOpenStory: (storyId: string) => void;
}

const STATUS_ORDER: StoryStatus[] = ['draft', 'needs_changes', 'accepted', 'imported', 'rejected'];

export function AnalyticsTab({ settings, board, onOpenStory }: Props) {
  if (!board.connected || !board.boardId) {
    return <p className="hint">{PICK_DESTINATION_HINT} to see analytics.</p>;
  }

  // each section loads on its own, so one failing doesn't hide the other
  return (
    <div className="stack">
      <BoardStats settings={settings} boardId={board.boardId} onOpenStory={onOpenStory} />
      <ExportHistory settings={settings} boardId={board.boardId} />
    </div>
  );
}

function BoardStats({
  settings,
  boardId,
  onOpenStory,
}: {
  settings: Settings;
  boardId: string;
  onOpenStory: (storyId: string) => void;
}) {
  const analytics = useLoad(() => getAnalytics(settings, boardId), [boardId]);
  useAutoReload(analytics.reload, ANALYTICS_REFRESH_MS);

  if (analytics.error) return <p className="status error">{analytics.error}</p>;

  const data = analytics.data;
  if (!data) return <p className="hint">Loading analytics…</p>;

  // the biggest count sets the bar scale
  const maxIssues = Math.max(1, ...Object.values(data.investIssues));

  const health = data.boardHealth;
  const totalStories = STATUS_ORDER.reduce((sum, status) => sum + data.counts[status], 0);

  return (
    <>
      <section className="card stack">
        <h2 className="card-title">
          <Icon name="analytics" size={20} />
          Board health
        </h2>
        <div className="overview">
          <HealthRing value={health} />
          <div className="overview-stats">
            <div className="metric">
              <span className="stat-value">{data.acceptanceRate === null ? '–' : formatPercent(data.acceptanceRate)}</span>
              <span className="hint">Acceptance rate</span>
            </div>
            <div className="metric">
              <span className="stat-value">{totalStories}</span>
              <span className="hint">{totalStories === 1 ? 'Story drafted' : 'Stories drafted'}</span>
            </div>
          </div>
        </div>
        {health === null && <p className="hint">Board health is not calculated yet.</p>}
      </section>

      <section className="card stack">
        <h2 className="card-title">
          <Icon name="drafts" size={20} />
          Stories
        </h2>
        <div className="overview">
          <StatusDonut counts={data.counts} total={totalStories} />
          <ul className="legend">
            {STATUS_ORDER.map((status) => (
              <li key={status} className={data.counts[status] === 0 ? 'empty' : undefined}>
                <span className="swatch" style={{ background: STATUS_COLORS[status] }} aria-hidden="true" />
                <span>{STORY_STATUS_LABELS[status]}</span>
                <strong>{data.counts[status]}</strong>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="card stack">
        <h2 className="card-title">
          <Icon name="check" size={20} />
          INVEST issues
        </h2>
        <ul className="bar-list">
          {INVEST_KEYS.map((key) => {
            const count = data.investIssues[key];
            const share = count / maxIssues;
            // none is good news. Otherwise the busiest criteria run hottest
            const tone = count === 0 ? 'none' : share >= 0.67 ? 'high' : share >= 0.34 ? 'mid' : 'low';
            return (
              <li key={key}>
                <span>{INVEST_LABELS[key]}</span>
                <span className="bar" aria-hidden="true">
                  <span className={tone} style={{ width: `${share * 100}%` }} />
                </span>
                <strong>{count}</strong>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="card stack">
        <h2 className="card-title">
          <Icon name="alert" size={20} />
          Needs attention
        </h2>
        {data.needsAttention.length === 0 && <p className="hint">Nothing needs attention.</p>}
        <ul className="list">
          {data.needsAttention.map((item) => (
            <li key={item.storyId}>
              <button className="story-row" onClick={() => onOpenStory(item.storyId)}>
                <span className="attention-row">
                  <Icon name="alert" />
                  <span>
                    <span className="story-title">{item.title}</span>
                    <span className="hint">{item.reason}</span>
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}

// each story status keeps one colour everywhere: the donut, its legend and the badges in Drafts
const STATUS_COLORS: Record<StoryStatus, string> = {
  draft: 'var(--accent)',
  needs_changes: 'var(--ok)',
  accepted: 'var(--info)',
  imported: 'var(--good)',
  rejected: 'var(--low)',
};

const RING_RADIUS = 42;
const RING_LENGTH = 2 * Math.PI * RING_RADIUS;

// a 0 to 100 gauge. Same cut-offs as the quality badge: 80 and up is good, 60 and up is fair.
function HealthRing({ value }: { value: number | null }) {
  const clamped = value === null ? 0 : Math.min(100, Math.max(0, value));
  const color = value === null ? 'var(--line)' : value >= 80 ? 'var(--good)' : value >= 60 ? 'var(--ok)' : 'var(--low)';
  return (
    <svg
      className="chart"
      viewBox="0 0 100 100"
      role="img"
      aria-label={value === null ? 'Board health not calculated yet' : `Board health ${value} out of 100`}
    >
      <circle cx="50" cy="50" r={RING_RADIUS} className="chart-track" />
      <circle
        cx="50"
        cy="50"
        r={RING_RADIUS}
        className="chart-arc"
        stroke={color}
        strokeDasharray={`${(clamped / 100) * RING_LENGTH} ${RING_LENGTH}`}
        transform="rotate(-90 50 50)"
        strokeLinecap="round"
      />
      <text x="50" y="52" className="chart-number">{value ?? '–'}</text>
      <text x="50" y="66" className="chart-caption">{value === null ? '' : 'of 100'}</text>
    </svg>
  );
}

// how the drafted stories split across statuses
function StatusDonut({ counts, total }: { counts: Record<StoryStatus, number>; total: number }) {
  const gap = STATUS_ORDER.filter((status) => counts[status] > 0).length > 1 ? 1.5 : 0;
  let offset = 0;
  return (
    <svg className="chart" viewBox="0 0 100 100" role="img" aria-label={`${total} stories by status`}>
      <circle cx="50" cy="50" r={RING_RADIUS} className="chart-track" />
      {STATUS_ORDER.map((status) => {
        if (counts[status] === 0) return null;
        const length = (counts[status] / total) * RING_LENGTH;
        const arc = (
          <circle
            key={status}
            cx="50"
            cy="50"
            r={RING_RADIUS}
            className="chart-arc"
            stroke={STATUS_COLORS[status]}
            strokeDasharray={`${Math.max(0, length - gap)} ${RING_LENGTH}`}
            strokeDashoffset={-offset}
            transform="rotate(-90 50 50)"
          />
        );
        offset += length;
        return arc;
      })}
      <text x="50" y="52" className="chart-number">{total}</text>
      <text x="50" y="66" className="chart-caption">{total === 1 ? 'story' : 'stories'}</text>
    </svg>
  );
}

const EXPORT_BADGE: Record<ExportStatus, string> = { completed: 'good', pending: 'ok', failed: 'low' };

const EXPORT_HINT: Partial<Record<ExportStatus, string>> = {
  pending: 'Interrupted. Exporting this story again resumes it without making a second card.',
  failed: 'Export again to retry.',
};

// "Check again" only re-reads the saved record. It doesn't retry the export.
function ExportHistory({ settings, boardId }: { settings: Settings; boardId: string }) {
  const history = useLoad(() => listBoardImports(settings, boardId, { limit: 50 }), [boardId]);
  useAutoReload(history.reload, ANALYTICS_REFRESH_MS);
  const [checkingStoryId, setCheckingStoryId] = useState<string | null>(null);
  const [checkError, setCheckError] = useState('');

  const records = history.data ?? [];
  const counts: Record<ExportStatus, number> = { completed: 0, pending: 0, failed: 0 };
  for (const record of records) counts[record.status] += 1;

  async function checkAgain(record: ExportRecord) {
    setCheckingStoryId(record.storyId);
    setCheckError('');
    try {
      const fresh = await getStoryImport(settings, record.storyId);
      if (fresh) history.setData(records.map((r) => (r.id === fresh.id ? fresh : r)));
    } catch (err) {
      setCheckError(err instanceof Error ? err.message : 'Could not check this story.');
    } finally {
      setCheckingStoryId(null);
    }
  }

  return (
    <section className="card stack">
      <h2 className="card-title">
        <Icon name="history" size={20} />
        Export history
      </h2>
      {history.error && <p className="status error">{history.error}</p>}
      {history.loading && !history.data && <p className="hint">Loading export history…</p>}
      {history.data && records.length === 0 && <p className="hint">Nothing has been exported to {providerLabel(settings.selectedBoard)} yet.</p>}

      {records.length > 0 && (
        <>
          <p className="badges">
            <span className="badge good">{counts.completed} exported</span>
            <span className="badge ok">{counts.pending} pending</span>
            <span className="badge low">{counts.failed} failed</span>
          </p>
          <ul className="list">
            {records.map((record) => (
              <li key={record.id} className="list-row">
                <span className="split">
                  <strong>{record.storyTitle ?? 'Untitled story'}</strong>
                  <span className={`badge ${EXPORT_BADGE[record.status]}`}>{EXPORT_STATUS_LABELS[record.status]}</span>
                </span>
                <span className="hint">{formatDateTime(record.createdAt)}</span>
                {EXPORT_HINT[record.status] && <span className="hint">{EXPORT_HINT[record.status]}</span>}
                {record.status !== 'completed' && (
                  <button className="link" disabled={checkingStoryId === record.storyId} onClick={() => checkAgain(record)}>
                    {checkingStoryId === record.storyId ? 'Checking…' : 'Check again'}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {checkError && <p className="status error" role="alert">{checkError}</p>}
    </section>
  );
}
