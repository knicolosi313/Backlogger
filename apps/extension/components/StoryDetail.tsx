import { useState } from 'react';
import { decideStory, deleteStory, requestEdits, updateStory } from '@/lib/api';
import { formatClock } from '@/lib/format';
import { INVEST_LABELS } from '@/lib/options';
import { boardProvider } from '@/lib/provider';
import type { EditRequestResult, Evidence, InvestCheck, Settings, Story, StoryDecision, StoryEdit } from '@/lib/types';
import { ConfidenceBadge, QualityBadge, StatusBadge } from './Badges';
import { Icon } from './Icon';
import { StoryEditForm } from './StoryEditForm';
import { MeetingAudio } from './MeetingAudio';

interface Props {
  story: Story;
  settings: Settings;
  onChange: (story: Story) => void;
  // a request to split this story adds new drafts that the list doesn't have yet
  onStoriesAdded?: () => void;
  // called after a rejected story is deleted, so the list can drop it
  onDeleted?: () => void;
  onBack: () => void;
}

// What to tell the person after the AI changed their draft
function describeEditResult(result: EditRequestResult): string {
  const added = result.newStories.length;
  const extra = added ? ` ${added} new ${added === 1 ? 'story was' : 'stories were'} added to your drafts.` : '';
  return `${result.summary}${extra}`;
}

export function StoryDetail({ story, settings, onChange, onStoriesAdded, onDeleted, onBack }: Props) {
  const [mode, setMode] = useState<'view' | 'request-edits' | 'edit'>('view');
  const [instructions, setInstructions] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  // run one backend call, show any error, pass the updated story up
  async function run(call: () => Promise<Story>) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      onChange(await call());
      setMode('view');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong.');
    } finally {
      setBusy(false);
    }
  }

  const decide = (decision: StoryDecision) => run(() => decideStory(settings, story.id, decision));
  const saveEdit = (edit: StoryEdit) => run(() => updateStory(settings, story.id, edit));
  const sendEditRequest = () =>
    run(async () => {
      const result = await requestEdits(settings, story.id, instructions.trim(), settings.model);
      setInstructions('');
      setNotice(describeEditResult(result));
      if (result.newStories.length > 0) onStoriesAdded?.();
      return result.story;
    });

  // Deleting only hides the draft in the app. It stays in the database.
  async function removeStory() {
    if (!window.confirm('Delete this rejected draft? It will disappear from the app but stay in the database.')) return;
    setBusy(true);
    setError('');
    try {
      await deleteStory(settings, story.id);
      onDeleted?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete this story.');
      setBusy(false);
    }
  }

  const isFinished = story.status === 'imported';
  const isRejected = story.status === 'rejected';

  return (
    <div className="stack">
      <button className="link back-link" onClick={onBack}>
        <Icon name="back" size={15} />
        All stories
      </button>

      <header className="story-title-bar">
        <h2>{story.title}</h2>
        <div className="badges">
          <StatusBadge status={story.status} />
          <ConfidenceBadge value={story.confidence} />
          <QualityBadge score={story.qualityScore} />
        </div>
      </header>

      {mode === 'edit' ? (
        <StoryEditForm story={story} busy={busy} onSave={saveEdit} onCancel={() => setMode('view')} />
      ) : (
        <StoryBody key={story.id} story={story} settings={settings} />
      )}

      {story.reviewerNote && story.status === 'needs_changes' && (
        <p className="note">
          <strong>Edits requested:</strong> {story.reviewerNote}
        </p>
      )}

      {error && <p className="status error" role="alert">{error}</p>}
      {notice && mode === 'view' && (
        <p className="note" role="status">
          <strong>AI revised this story.</strong> {notice}
        </p>
      )}

      {mode === 'request-edits' && (
        <section className="card stack">
          <label className="field">
            What should the AI change?
            <textarea
              rows={3}
              value={instructions}
              placeholder="e.g. Split this into two stories, one for each payment method."
              onChange={(e) => setInstructions(e.target.value)}
            />
          </label>
          <div className="row">
            <button className="primary" disabled={busy || !instructions.trim()} onClick={sendEditRequest}>
              {busy ? 'Revising…' : 'Send request'}
            </button>
            <button disabled={busy} onClick={() => setMode('view')}>
              Cancel
            </button>
          </div>
          {busy && (
            <p className="hint" role="status">
              The AI is rewriting the story and then checking it again. This can take up to a minute.
            </p>
          )}
        </section>
      )}

      {mode === 'view' && (
        <div className="actions">
          {isFinished && story.cardUrl && (
            <a className="button primary" href={story.cardUrl} target="_blank" rel="noreferrer">
              <Icon name="external" />
              {boardProvider(settings.selectedBoard) === 'jira' ? 'Open Jira issue' : 'Open Trello card'}
            </a>
          )}

          {isRejected && (
            <>
              <button disabled={busy} onClick={() => decide('restore')}>
                Restore to drafts
              </button>
              <button className="danger" disabled={busy} onClick={removeStory}>
                Delete
              </button>
            </>
          )}

          {!isFinished && !isRejected && (
            <>
              <button
                className="accept"
                disabled={busy || story.status === 'accepted'}
                onClick={() => decide('accept')}
              >
                <Icon name="check" />
                {story.status === 'accepted' ? 'Accepted' : 'Accept'}
              </button>
              <button disabled={busy} onClick={() => setMode('request-edits')}>
                <Icon name="comment" size={16} />
                Request edits
              </button>
              <button disabled={busy} onClick={() => setMode('edit')}>
                <Icon name="edit" size={16} />
                Edit manually
              </button>
              <button className="danger" disabled={busy} onClick={() => decide('reject')}>
                <Icon name="close" size={16} />
                Reject
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function StoryBody({ story, settings }: { story: Story; settings: Settings }) {
  const [moment, setMoment] = useState<{ seconds: number; click: number } | null>(null);
  return (
    <>
      <section className="stack">
        <h3>User story</h3>
        <p className="story-text">
          <strong>As a</strong> {story.persona}, <strong>I want</strong> {story.goal}, <strong>so that</strong>{' '}
          {story.benefit}.
        </p>
      </section>

      <p className="hint">
        Priority: {story.priority} · Points: {story.points ?? 'not estimated'}
      </p>

      {(story.origin === 'proposed' || story.origin === 'inferred') && (
        <aside className="note warn">
          <strong>{story.origin === 'proposed' ? 'AI proposal' : 'Inferred story'}</strong>
          <p>{story.origin === 'proposed'
            ? 'An idea inspired by the meeting, not an agreed requirement. Review it with the team.'
            : 'This story includes inferred details that need confirmation.'}</p>
          {!!story.assumptions?.length && (
            <ul className="bullets">{story.assumptions.map((assumption) => <li key={assumption}>{assumption}</li>)}</ul>
          )}
        </aside>
      )}

      <section className="stack">
        <h3>Acceptance criteria</h3>
        <ul className="criteria">
          {story.acceptanceCriteria.map((criterion) => (
            <li key={criterion}>{criterion}</li>
          ))}
        </ul>
      </section>

      <section className="stack">
        <h3>Evidence from the meeting</h3>
        {story.evidence.length === 0 && (
          <p className="note warn">
            No moment in the meeting supports this story. Check it carefully before accepting. It will never be
            auto-imported.
          </p>
        )}
        {story.evidence.map((item) => (
          <EvidenceItem key={item.startSeconds} evidence={item} onJump={() => setMoment((previous) => ({
            seconds: item.startSeconds, click: (previous?.click ?? 0) + 1,
          }))} />
        ))}
        {moment && <MeetingAudio key={`${settings.apiUrl}:${story.meetingId}`} settings={settings} meetingId={story.meetingId} moment={moment} />}
      </section>

      <section className="stack">
        <h3>INVEST check</h3>
        <ul className="list">
          {story.investChecks.map((check) => (
            <InvestRow key={check.key} check={check} />
          ))}
        </ul>
      </section>

      <section className="stack">
        <h3>AI critique</h3>
        <p>{story.critique}</p>
      </section>

      {story.openQuestions.length > 0 && (
        <section className="stack">
          <h3>Open questions</h3>
          <ul className="bullets">
            {story.openQuestions.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        </section>
      )}

      {story.unsupportedAssumptions.length > 0 && (
        <section className="stack">
          <h3>Unsupported assumptions</h3>
          <ul className="bullets">
            {story.unsupportedAssumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function EvidenceItem({ evidence, onJump }: { evidence: Evidence; onJump: () => void }) {
  return (
    <blockquote className="evidence">
      <p>“{evidence.quote}”</p>
      <footer>
        {evidence.speaker} ·{' '}
        <button className="link" onClick={onJump}>
          Jump to moment ({formatClock(evidence.startSeconds)})
        </button>
      </footer>
    </blockquote>
  );
}

const INVEST_ICONS = { pass: '✓', warn: '!', fail: '✗' } as const;

function InvestRow({ check }: { check: InvestCheck }) {
  return (
    <li className={`invest ${check.result}`}>
      <span className="invest-icon" aria-label={check.result}>
        {INVEST_ICONS[check.result]}
      </span>
      <div>
        <strong>{INVEST_LABELS[check.key]}</strong>
        {check.result !== 'pass' && (
          <>
            <p className="hint">{check.explanation}</p>
            {check.fix && (
              <p className="hint">
                <strong>Fix:</strong> {check.fix}
              </p>
            )}
          </>
        )}
      </div>
    </li>
  );
}
