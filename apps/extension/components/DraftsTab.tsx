import { useState } from 'react';
import { importStories, listStories } from '@/lib/api';
import type { BoardConnection, Settings, Story, StoryStatus } from '@/lib/types';
import { useAutoReload, useLoad } from '@/lib/useLoad';
import { ConfidenceBadge, QualityBadge, StatusBadge } from './Badges';
import { StoryDetail } from './StoryDetail';

interface Props {
  settings: Settings;
  board: BoardConnection;
  selectedStoryId: string | null;
  onSelectStory: (storyId: string | null) => void;
}

// The filter buttons above the list. "Pending" is everything still waiting on a human.
const FILTERS: Array<{ label: string; statuses: StoryStatus[] }> = [
  { label: 'Pending', statuses: ['draft', 'needs_changes'] },
  { label: 'Accepted', statuses: ['accepted'] },
  { label: 'Imported', statuses: ['imported'] },
  { label: 'Rejected', statuses: ['rejected'] },
];

export function DraftsTab({ settings, board, selectedStoryId, onSelectStory }: Props) {
  const [filterIndex, setFilterIndex] = useState(0);
  const [importMessage, setImportMessage] = useState('');
  const [importing, setImporting] = useState(false);

  const stories = useLoad(
    () => (board.boardId ? listStories(settings, board.boardId) : Promise.resolve([])),
    [board.boardId],
  );

  // Drafts show up when processing finishes, which can be after this tab opened. Don't
  // refresh while a story is open so it can't change under the reviewer.
  useAutoReload(stories.reload, 5000, Boolean(board.boardId) && !selectedStoryId);

  if (!board.connected || !board.boardId) {
    return <p className="hint">Pick a Trello board in Settings to see draft stories.</p>;
  }

  const allStories = stories.data ?? [];
  const selected = allStories.find((s) => s.id === selectedStoryId);

  // put the updated story back in the list
  function handleStoryChanged(updated: Story) {
    stories.setData(allStories.map((s) => (s.id === updated.id ? updated : s)));
  }

  if (selected) {
    return (
      <StoryDetail
        story={selected}
        settings={settings}
        onChange={handleStoryChanged}
        onBack={() => onSelectStory(null)}
      />
    );
  }

  const filter = FILTERS[filterIndex]!;
  const visible = allStories.filter((s) => filter.statuses.includes(s.status));
  const acceptedStories = allStories.filter((s) => s.status === 'accepted');

  async function handleImport() {
    if (!board.boardId) return;
    setImporting(true);
    setImportMessage('');
    try {
      const results = await importStories(settings, board.boardId, acceptedStories, settings.targetListId);
      const created = results.filter((r) => !r.duplicate).length;
      const skipped = results.length - created;
      setImportMessage(
        `Imported ${created} to "${settings.targetList}".` + (skipped ? ` ${skipped} already imported, skipped.` : ''),
      );
    } catch (err) {
      setImportMessage(err instanceof Error ? err.message : 'Import failed.');
    } finally {
      setImporting(false);
      // some may have gone through even if others failed
      stories.reload();
    }
  }

  return (
    <div className="stack">
      <div className="filters" role="group" aria-label="Filter stories">
        {FILTERS.map((f, index) => (
          <button
            key={f.label}
            className={index === filterIndex ? 'chip active' : 'chip'}
            onClick={() => setFilterIndex(index)}
          >
            {f.label} ({allStories.filter((s) => f.statuses.includes(s.status)).length})
          </button>
        ))}
      </div>

      <button className="link" onClick={stories.reload}>
        Refresh
      </button>

      {stories.error && <p className="status error">{stories.error}</p>}
      {stories.loading && !stories.data && <p className="hint">Loading stories…</p>}
      {stories.data && visible.length === 0 && <p className="hint">Nothing here.</p>}

      <ul className="list">
        {visible.map((story) => (
          <li key={story.id}>
            <button className="story-row" onClick={() => onSelectStory(story.id)}>
              <span className="story-title">{story.title}</span>
              <span className="badges">
                <StatusBadge status={story.status} />
                <ConfidenceBadge value={story.confidence} />
                <QualityBadge score={story.qualityScore} />
              </span>
            </button>
          </li>
        ))}
      </ul>

      {acceptedStories.length > 0 && (
        <section className="card stack">
          <button className="primary" disabled={importing} onClick={handleImport}>
            {importing ? 'Importing…' : `Import ${acceptedStories.length} accepted to ${settings.targetList}`}
          </button>
          <p className="hint">Cards get an "AI Draft" label and a link back to the meeting moment.</p>
        </section>
      )}
      {importMessage && <p className="status" role="status">{importMessage}</p>}
    </div>
  );
}
