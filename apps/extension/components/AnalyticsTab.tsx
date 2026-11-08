import { useState } from 'react';
import { getAnalytics, getStoryImport, listBoardImports } from '@/lib/api';
import { formatDateTime, formatPercent } from '@/lib/format';
import { EXPORT_STATUS_LABELS, INVEST_LABELS, STORY_STATUS_LABELS } from '@/lib/options';
import type { BoardConnection, ExportRecord, ExportStatus, Settings, StoryStatus } from '@/lib/types';
import { INVEST_KEYS } from '@/lib/types';
import { useAutoReload, useLoad } from '@/lib/useLoad';

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
    return <p className="hint">Pick a Trello board in Settings to see analytics.</p>;
  }

  // each section loads on its own, so one failing doesn't hide the other
  return (
    <div className="stack">
      <BoardStats settings={settings} boardId={board.boardId} onOpenStory={onOpenStory} />
      <ExportHistory settings={settings} boardId={board.boardId} />
      <a
        className="button primary"
        href={`${settings.webAppUrl}/boards/${board.boardId}/analytics`}
        target="_blank"
        rel="noreferrer"
      >
        Open Full Analytics
      </a>
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

  return (
    <>
      <section className="stat-grid">
        <div className="card stat">
          <span className="stat-value">{data.boardHealth ?? '–'}</span>
          <span className="hint">Board health{data.boardHealth === null ? ' (not calculated yet)' : ' / 100'}</span>
        </div>
        <div className="card stat">
          <span className="stat-value">{data.acceptanceRate === null ? '–' : formatPercent(data.acceptanceRate)}</span>
          <span className="hint">Acceptance rate</span>
        </div>
      </section>

      <section className="stack">
        <h2>Stories</h2>
        <ul className="count-list">
          {STATUS_ORDER.map((status) => (
            <li key={status}>
              <span>{STORY_STATUS_LABELS[status]}</span>
              <strong>{data.counts[status]}</strong>
            </li>
          ))}
        </ul>
      </section>

      <section className="stack">
        <h2>INVEST issues</h2>
        <ul className="bar-list">
          {INVEST_KEYS.map((key) => (
            <li key={key}>
              <span>{INVEST_LABELS[key]}</span>
              <span className="bar" aria-hidden="true">
                <span style={{ width: `${(data.investIssues[key] / maxIssues) * 100}%` }} />
              </span>
              <strong>{data.investIssues[key]}</strong>
            </li>
          ))}
        </ul>
      </section>

      <section className="stack">
        <h2>Needs attention</h2>
        {data.needsAttention.length === 0 && <p className="hint">Nothing needs attention.</p>}
        <ul className="list">
          {data.needsAttention.map((item) => (
            <li key={item.storyId}>
              <button className="story-row" onClick={() => onOpenStory(item.storyId)}>
                <span className="story-title">{item.title}</span>
                <span className="hint">{item.reason}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>

    </>
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
    <section className="stack">
      <h2>Export history</h2>
      {history.error && <p className="status error">{history.error}</p>}
      {history.loading && !history.data && <p className="hint">Loading export history…</p>}
      {history.data && records.length === 0 && <p className="hint">Nothing has been exported to Trello yet.</p>}

      {records.length > 0 && (
        <>
          <p className="hint">
            {counts.completed} exported · {counts.pending} pending · {counts.failed} failed
          </p>
          <ul className="list">
            {records.map((record) => (
              <li key={record.id} className="list-row">
                <span className="split">
                  <strong>Story {record.storyId.slice(0, 8)}</strong>
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
