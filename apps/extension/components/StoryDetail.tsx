import { useState } from 'react';
import { decideStory, momentUrl, requestEdits, updateStory } from '@/lib/api';
import { formatClock } from '@/lib/format';
import { INVEST_LABELS } from '@/lib/options';
import { boardProvider } from '@/lib/provider';
import type { Evidence, InvestCheck, Settings, Story, StoryDecision, StoryEdit } from '@/lib/types';
import { ConfidenceBadge, QualityBadge, StatusBadge } from './Badges';
import { StoryEditForm } from './StoryEditForm';

interface Props {
  story: Story;
  settings: Settings;
  onChange: (story: Story) => void;
  onBack: () => void;
}

export function StoryDetail({ story, settings, onChange, onBack }: Props) {
  const [mode, setMode] = useState<'view' | 'request-edits' | 'edit'>('view');
  const [instructions, setInstructions] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // run one backend call, show any error, pass the updated story up
  async function run(call: () => Promise<Story>) {
    setBusy(true);
    setError('');
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
      const updated = await requestEdits(settings, story.id, instructions.trim());
      setInstructions('');
      return updated;
    });

  const isFinished = story.status === 'imported';
  const isRejected = story.status === 'rejected';

  return (
    <div className="stack">
      <button className="link" onClick={onBack}>
        ← All stories
      </button>

      <header className="stack">
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
        <StoryBody story={story} settings={settings} />
      )}

      {story.reviewerNote && story.status === 'needs_changes' && (
        <p className="note">
          <strong>Edits requested:</strong> {story.reviewerNote}
        </p>
      )}

      {error && <p className="status error" role="alert">{error}</p>}

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
              Send request
            </button>
            <button disabled={busy} onClick={() => setMode('view')}>
              Cancel
            </button>
          </div>
        </section>
      )}

      {mode === 'view' && (
        <div className="actions">
          {isFinished && story.cardUrl && (
            <a className="button" href={story.cardUrl} target="_blank" rel="noreferrer">
              {boardProvider(settings.selectedBoard) === 'jira' ? 'Open Jira issue' : 'Open Trello card'}
            </a>
          )}

          {isRejected && (
            <button disabled={busy} onClick={() => decide('restore')}>
              Restore to drafts
            </button>
          )}

          {!isFinished && !isRejected && (
            <>
              <button
                className="primary"
                disabled={busy || story.status === 'accepted'}
                onClick={() => decide('accept')}
              >
                {story.status === 'accepted' ? 'Accepted' : 'Accept'}
              </button>
              <button disabled={busy} onClick={() => setMode('request-edits')}>
                Request edits
              </button>
              <button disabled={busy} onClick={() => setMode('edit')}>
                Edit manually
              </button>
              <button className="danger" disabled={busy} onClick={() => decide('reject')}>
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
  return (
    <>
      <p>
        <strong>As a</strong> {story.persona}, <strong>I want</strong> {story.goal}, <strong>so that</strong>{' '}
        {story.benefit}.
      </p>

      <p className="hint">
        Priority: {story.priority} · Points: {story.points ?? 'not estimated'}
      </p>

      <section className="stack">
        <h3>Acceptance criteria</h3>
        <ul className="bullets">
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
          <EvidenceItem key={item.startSeconds} evidence={item} story={story} settings={settings} />
        ))}
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

function EvidenceItem({ evidence, story, settings }: { evidence: Evidence; story: Story; settings: Settings }) {
  // TODO: the web app's meeting page needs to read ?t= and seek to it
  const url = momentUrl(settings.webAppUrl, story.meetingId, evidence.startSeconds);

  return (
    <blockquote className="evidence">
      <p>“{evidence.quote}”</p>
      <footer>
        {evidence.speaker} ·{' '}
        <a href={url} target="_blank" rel="noreferrer">
          Jump to moment ({formatClock(evidence.startSeconds)})
        </a>
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
