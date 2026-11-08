import { useState } from 'react';
import { decideStory, deleteStory, importStories, listStories } from '@/lib/api';
import { PICK_DESTINATION_HINT, boardProvider, destinationText } from '@/lib/provider';
import type { BoardConnection, Settings, Story, StoryStatus } from '@/lib/types';
import { useAutoReload, useLoad } from '@/lib/useLoad';
import { ConfidenceBadge, QualityBadge, StatusBadge } from './Badges';
import { Icon } from './Icon';
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

type BulkAction = 'accept' | 'import' | 'reject' | 'delete';

// Which stories each bulk action applies to. A mixed selection is fine: each action only touches
// the stories it makes sense for and the rest are left alone.
const ELIGIBLE: Record<BulkAction, StoryStatus[]> = {
  accept: ['draft', 'needs_changes'],
  import: ['accepted'],
  reject: ['draft', 'needs_changes', 'accepted'],
  delete: ['rejected'],
};

const PAST_TENSE: Record<Exclude<BulkAction, 'import'>, string> = {
  accept: 'Accepted',
  reject: 'Rejected',
  delete: 'Deleted',
};

// One request per story. If some fail we still try the rest, then report how it went.
async function applyEach(targets: Story[], call: (story: Story) => Promise<unknown>) {
  let done = 0;
  let failed = 0;
  let firstError = '';
  for (const story of targets) {
    try {
      await call(story);
      done += 1;
    } catch (err) {
      failed += 1;
      firstError ||= err instanceof Error ? err.message : 'Something went wrong.';
    }
  }
  return { done, failed, firstError };
}

export function DraftsTab({ settings, board, selectedStoryId, onSelectStory }: Props) {
  const [filterIndex, setFilterIndex] = useState(0);
  const [importMessage, setImportMessage] = useState('');
  const [importing, setImporting] = useState(false);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState<BulkAction | null>(null);
  const [bulkMessage, setBulkMessage] = useState('');

  const selectedBoard = settings.selectedBoard;
  const stories = useLoad(
    () => (selectedBoard ? listStories(settings, selectedBoard) : Promise.resolve([])),
    [selectedBoard?.id, selectedBoard?.provider, selectedBoard?.siteUrl],
  );
  const isJira = boardProvider(selectedBoard) === 'jira';

  // Drafts show up when processing finishes, which can be after this tab opened. Don't
  // refresh while a story is open so it can't change under the reviewer.
  useAutoReload(stories.reload, 5000, Boolean(board.boardId) && !selectedStoryId);

  if (!board.connected || !board.boardId) {
    return <p className="hint">{PICK_DESTINATION_HINT} to see draft stories.</p>;
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
        onStoriesAdded={stories.reload}
        onDeleted={() => {
          onSelectStory(null);
          stories.reload();
        }}
        onBack={() => onSelectStory(null)}
      />
    );
  }

  const filter = FILTERS[filterIndex]!;
  const visible = allStories.filter((s) => filter.statuses.includes(s.status));
  const acceptedStories = allStories.filter((s) => s.status === 'accepted');

  // Only stories still in the list count, so one deleted or moved by someone else drops out by itself.
  const chosen = allStories.filter((s) => checkedIds.has(s.id));
  const targetsFor = (action: BulkAction) => chosen.filter((s) => ELIGIBLE[action].includes(s.status));
  const visibleChosen = visible.filter((s) => checkedIds.has(s.id));
  const allVisibleChosen = visible.length > 0 && visibleChosen.length === visible.length;

  function toggle(storyId: string) {
    setCheckedIds((current) => {
      const next = new Set(current);
      if (next.has(storyId)) next.delete(storyId);
      else next.add(storyId);
      return next;
    });
  }

  function toggleAllVisible() {
    setCheckedIds((current) => {
      const next = new Set(current);
      for (const story of visible) {
        if (allVisibleChosen) next.delete(story.id);
        else next.add(story.id);
      }
      return next;
    });
  }

  async function runBulk(action: BulkAction) {
    const targets = targetsFor(action);
    if (targets.length === 0) return;
    if (
      action === 'delete' &&
      !window.confirm(
        `Delete ${targets.length} rejected ${targets.length === 1 ? 'draft' : 'drafts'}? ` +
          'They will disappear from the app but stay in the database.',
      )
    ) {
      return;
    }

    setBulkBusy(action);
    setBulkMessage('');
    const skipped = chosen.length - targets.length;
    const skippedNote = skipped ? ` ${skipped} skipped: ${action === 'import' ? 'only accepted stories can be imported' : 'not eligible'}.` : '';
    try {
      if (action === 'import') {
        const results = await importStories(settings, targets);
        const created = results.filter((r) => !r.duplicate).length;
        const already = results.length - created;
        setBulkMessage(
          `Imported ${created} to ${destinationText(settings)}.` + (already ? ` ${already} already imported.` : '') + skippedNote,
        );
      } else {
        const { done, failed, firstError } = await applyEach(targets, (story) =>
          action === 'delete' ? deleteStory(settings, story.id) : decideStory(settings, story.id, action),
        );
        setBulkMessage(
          `${PAST_TENSE[action]} ${done}.` + (failed ? ` ${failed} failed: ${firstError}` : '') + skippedNote,
        );
      }
    } catch (err) {
      setBulkMessage(err instanceof Error ? err.message : 'Something went wrong.');
    } finally {
      setBulkBusy(null);
      setCheckedIds(new Set());
      // some may have gone through even if others failed
      stories.reload();
    }
  }

  async function handleImport() {
    if (!board.boardId) return;
    setImporting(true);
    setImportMessage('');
    try {
      const results = await importStories(settings, acceptedStories);
      const created = results.filter((r) => !r.duplicate).length;
      const skipped = results.length - created;
      setImportMessage(
        `Imported ${created} to ${destinationText(settings)}.` + (skipped ? ` ${skipped} already imported, skipped.` : ''),
      );
    } catch (err) {
      setImportMessage(err instanceof Error ? err.message : 'Import failed.');
    } finally {
      setImporting(false);
      // some may have gone through even if others failed
      stories.reload();
    }
  }

  const counts: Record<BulkAction, number> = {
    accept: targetsFor('accept').length,
    import: targetsFor('import').length,
    reject: targetsFor('reject').length,
    delete: targetsFor('delete').length,
  };
  const anyBusy = importing || bulkBusy !== null;

  return (
    <div>
      <div className="stack">
        <h2 className="card-title">
          <Icon name="drafts" size={20} />
          Meeting drafts
        </h2>
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

        <div className="split">
          {visible.length > 0 ? (
            <label className="checkbox">
              <input
                type="checkbox"
                checked={allVisibleChosen}
                ref={(box) => {
                  if (box) box.indeterminate = visibleChosen.length > 0 && !allVisibleChosen;
                }}
                onChange={toggleAllVisible}
              />
              Select all ({visible.length})
            </label>
          ) : (
            <span />
          )}
          <button className="link" onClick={stories.reload}>
            <Icon name="refresh" size={14} />
            Refresh
          </button>
        </div>

        {stories.error && <p className="status error">{stories.error}</p>}
        {stories.loading && !stories.data && <p className="hint">Loading stories…</p>}
        {stories.data && visible.length === 0 && <p className="hint">Nothing here.</p>}

        <ul className="list">
          {visible.map((story) => (
            <li key={story.id} className="select-row">
              <input
                type="checkbox"
                checked={checkedIds.has(story.id)}
                onChange={() => toggle(story.id)}
                aria-label={`Select ${story.title}`}
              />
              <button className="story-row" onClick={() => onSelectStory(story.id)}>
                <span className="story-head">
                  <span className="story-title">{story.title}</span>
                  <ConfidenceBadge value={story.confidence} />
                </span>
                <span className="badges">
                  <StatusBadge status={story.status} />
                  <QualityBadge score={story.qualityScore} />
                </span>
              </button>
            </li>
          ))}
        </ul>

        {acceptedStories.length > 0 && (
          <section className="card stack">
            <button className="primary" disabled={anyBusy} onClick={handleImport}>
              <Icon name="upload" />
              {importing ? 'Importing…' : `Import ${acceptedStories.length} accepted to ${destinationText(settings)}`}
            </button>
            <p className="hint">
              {isJira ? 'Issues get an "ai-draft" label.' : 'Cards get an "AI Draft" label.'}
            </p>
          </section>
        )}
        {importMessage && <p className="status" role="status">{importMessage}</p>}
        {bulkMessage && <p className="status" role="status">{bulkMessage}</p>}
      </div>

      {chosen.length > 0 && (
        // outside the grid above so it can stick to the bottom of the panel while scrolling
        <div className="sticky-bar stack" role="group" aria-label="Actions for selected stories">
          <div className="split">
            <strong>{chosen.length} selected</strong>
            <button className="link" disabled={anyBusy} onClick={() => setCheckedIds(new Set())}>
              Clear
            </button>
          </div>
          <div className="bulk-actions">
            <button
              className="primary"
              disabled={anyBusy || counts.accept === 0}
              title="Accept the selected drafts"
              onClick={() => runBulk('accept')}
            >
              {bulkBusy === 'accept' ? 'Accepting…' : `Accept (${counts.accept})`}
            </button>
            <button
              disabled={anyBusy || counts.import === 0}
              title={counts.import === 0 ? 'Only accepted stories can be imported' : `Import to ${destinationText(settings)}`}
              onClick={() => runBulk('import')}
            >
              {bulkBusy === 'import' ? 'Importing…' : `Import (${counts.import})`}
            </button>
            <button
              className="danger"
              disabled={anyBusy || counts.reject === 0}
              title="Reject the selected stories. You can restore them from the Rejected filter."
              onClick={() => runBulk('reject')}
            >
              {bulkBusy === 'reject' ? 'Rejecting…' : `Reject (${counts.reject})`}
            </button>
            {counts.delete > 0 && (
              <button
                className="danger"
                disabled={anyBusy}
                title="Hide rejected drafts from the app. They stay in the database."
                onClick={() => runBulk('delete')}
              >
                {bulkBusy === 'delete' ? 'Deleting…' : `Delete (${counts.delete})`}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
