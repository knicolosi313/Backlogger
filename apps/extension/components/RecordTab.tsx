import { useEffect, useRef, useState } from 'react';
import { countMeetingStories, createMeeting, getProcessingStatus, listMeetings, startProcessing, uploadMedia } from '@/lib/api';
import { formatClock, formatPercent, formatShortDate, formatSize } from '@/lib/format';
import type { PanelToBackground } from '@/lib/messages';
import { AUDIO_FILE_ACCEPT, PROCESSING_LABELS, audioTypeFor } from '@/lib/options';
import { clearPendingAudio, loadPendingAudio, savePendingAudio } from '@/lib/pendingAudio';
import { recorderState, settingsItem } from '@/lib/storage';
import type { BoardConnection, RecorderState, Settings } from '@/lib/types';
import { useLoad } from '@/lib/useLoad';
import { useStorageItem } from '@/lib/useStorageItem';

interface Props {
  settings: Settings;
  board: BoardConnection;
  onReviewStories: () => void;
}

type State<S extends RecorderState['status']> = Extract<RecorderState, { status: S }>;

const RECORDING_FILE_NAME = 'recording.webm';

function sendToBackground(message: PanelToBackground) {
  browser.runtime.sendMessage(message).catch(() => {});
}

// Mic permission can only be asked for on a visible page, so send them to the permissions page
async function ensureMicPermission(): Promise<boolean> {
  const status = await navigator.permissions.query({ name: 'microphone' as PermissionName });
  if (status.state === 'granted') return true;
  await browser.tabs.create({ url: browser.runtime.getURL('/permissions.html') });
  return false;
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

// Steps: create a meeting, record it (or pick a file), then submit. Submit uploads the
// audio and starts processing on the server.
export function RecordTab({ settings, board, onReviewStories }: Props) {
  const state = useStorageItem(recorderState);
  const [now, setNow] = useState(Date.now());

  // tick the timer
  const isRecording = state?.status === 'recording';
  const [stopping, setStopping] = useState(false);
  useEffect(() => {
    if (!isRecording) {
      setStopping(false);
      return;
    }
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [isRecording]);

  // poll the backend while it works. This only runs while the Record tab is open
  const processingMeetingId = state?.status === 'processing' ? state.meetingId : null;
  useEffect(() => {
    if (!processingMeetingId) return;
    const meetingId = processingMeetingId;
    let cancelled = false;

    async function checkProgress() {
      try {
        const processingStatus = await getProcessingStatus(settings, meetingId);
        const current = await recorderState.getValue();
        if (cancelled || current.status !== 'processing') return;

        if (processingStatus === 'ready') {
          await recorderState.setValue({ status: 'done', meetingId: current.meetingId });
        } else if (processingStatus === 'failed') {
          await recorderState.setValue({ status: 'error', message: 'The backend could not process this recording.' });
        } else {
          await recorderState.setValue({ ...current, processingStatus });
        }
      } catch {
        // network blip, we'll try again in 2s
      }
    }

    void checkProgress();
    const id = setInterval(checkProgress, 2000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [processingMeetingId, settings]);

  // The upload runs in this panel, so closing it mid-upload leaves the saved state stuck on
  // "uploading". If we find that and we didn't start it, let them submit again.
  const submittingHere = useRef(false);
  const status = state?.status;
  useEffect(() => {
    if (status !== 'uploading' || submittingHere.current) return;
    void (async () => {
      const current = await recorderState.getValue();
      if (current.status !== 'uploading') return;
      await restoreAfterFailedSubmit(current, 'The upload was interrupted. Submit again.');
    })();
  }, [status]);

  // reload the meeting list when a meeting is created or finishes
  const meetings = useLoad(
    () => (board.boardId ? listMeetings(settings, board.boardId) : Promise.resolve([])),
    [board.boardId, state?.status === 'created', state?.status === 'done'],
  );

  if (!state) return null;

  return (
    <div className="stack">
      {state.status === 'idle' && <CreateMeetingCard settings={settings} board={board} />}
      {state.status === 'created' && <CreatedCard settings={settings} state={state} />}

      {state.status === 'recording' && (
        <section className="card stack">
          <p className="status recording">
            <span className="dot" aria-hidden="true" />
            Recording <strong>{formatClock((now - state.startedAt) / 1000)}</strong>
          </p>
          <p className="hint">{state.title}</p>
          <button
            className="primary"
            disabled={stopping}
            onClick={() => {
              setStopping(true);
              sendToBackground({ type: 'STOP_RECORDING' });
            }}
          >
            {stopping ? 'Saving recording…' : 'Stop recording'}
          </button>
        </section>
      )}

      {state.status === 'recorded' && (
        <RecordedCard
          settings={settings}
          state={state}
          onSubmitStart={() => (submittingHere.current = true)}
          onSubmitEnd={() => (submittingHere.current = false)}
        />
      )}

      {state.status === 'uploading' && (
        <section className="card stack">
          <p className="status">Uploading the audio…</p>
          <p className="hint">Keep this panel open until the upload finishes.</p>
        </section>
      )}

      {state.status === 'processing' && (
        <section className="card stack">
          <p className="status">{PROCESSING_LABELS[state.processingStatus]}…</p>
          <p className="hint">You can switch tabs. Processing continues on the server.</p>
        </section>
      )}

      {state.status === 'done' && (
        <DoneCard settings={settings} board={board} meetingId={state.meetingId} onReviewStories={onReviewStories} />
      )}

      {state.status === 'error' && (
        <section className="card stack">
          <p className="status error" role="alert">
            {state.message}
          </p>
          <button onClick={() => recorderState.setValue({ status: 'idle' })}>Try again</button>
        </section>
      )}

      <AutoImportToggle settings={settings} />

      <section className="stack">
        <h2>Recent meetings</h2>
        {!board.connected && <p className="hint">Pick a Trello board in Settings to see your meetings.</p>}
        {board.connected && meetings.error && <p className="status error">{meetings.error}</p>}
        {board.connected && meetings.data?.length === 0 && <p className="hint">No meetings yet.</p>}
        <ul className="list">
          {meetings.data?.map((meeting) => (
            <li key={meeting.id} className="list-row">
              <strong>{meeting.title}</strong>
              <span className="hint">
                {[
                  formatShortDate(meeting.startedAt),
                  PROCESSING_LABELS[meeting.status],
                  meeting.durationSeconds !== undefined && `${Math.round(meeting.durationSeconds / 60)} min`,
                  meeting.storyCount !== undefined && `${meeting.storyCount} stories`,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function DoneCard({
  settings,
  board,
  meetingId,
  onReviewStories,
}: {
  settings: Settings;
  board: BoardConnection;
  meetingId: string;
  onReviewStories: () => void;
}) {
  const count = useLoad(
    () => (board.boardId ? countMeetingStories(settings, board.boardId, meetingId) : Promise.resolve(undefined)),
    [board.boardId, meetingId],
  );
  const reset = () => recorderState.setValue({ status: 'idle' });
  const stories = count.data;

  return (
    <section className="card stack">
      {stories === undefined && !count.error && <p className="status">Checking for drafts…</p>}
      {count.error && <p className="status error">{count.error}</p>}
      {stories === 0 && (
        <>
          <p className="status">Processing finished, but no stories were found in this recording.</p>
          <p className="hint">
            Stories are only drafted for work the team talked about doing, like a feature, a fix or a task.
            Try a longer recording with some decisions or requests in it.
          </p>
        </>
      )}
      {stories !== undefined && stories > 0 && (
        <p className="status">{stories === 1 ? '1 draft story is ready.' : `${stories} draft stories are ready.`}</p>
      )}
      {(stories === undefined ? count.error : stories > 0) && (
        <button
          className="primary"
          onClick={() => {
            void reset();
            onReviewStories();
          }}
        >
          Review drafts
        </button>
      )}
      <button onClick={reset}>Record another meeting</button>
    </section>
  );
}

function CreateMeetingCard({ settings, board }: { settings: Settings; board: BoardConnection }) {
  const [title, setTitle] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // Suggest the tab's title. Chrome only shares it if the icon was clicked on that tab.
  useEffect(() => {
    browser.tabs
      .query({ active: true, currentWindow: true })
      .then(([tab]) => {
        if (tab?.title) setTitle((current) => current || tab.title!.slice(0, 255));
      })
      .catch(() => {});
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!board.boardId) return;
    setBusy(true);
    setError('');
    try {
      const trimmed = title.trim();
      const { meetingId } = await createMeeting(settings, { boardId: board.boardId, title: trimmed });
      await clearPendingAudio().catch(() => {}); // don't reuse audio from an earlier meeting
      await recorderState.setValue({ status: 'created', meetingId, title: trimmed });
    } catch (err) {
      setError(errorText(err, 'Could not create the meeting.'));
    } finally {
      setBusy(false);
    }
  }

  if (!board.connected) {
    return (
      <section className="card stack">
        <p className="hint">Pick a Trello board in Settings first. Every meeting belongs to a board.</p>
      </section>
    );
  }

  return (
    <form className="card stack" onSubmit={handleCreate}>
      <label className="field">
        Meeting title
        <input
          value={title}
          maxLength={255}
          placeholder="e.g. Sprint planning"
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <p className="hint">Create the meeting first, then record it or choose an audio file.</p>
      <button className="primary" type="submit" disabled={busy || !title.trim()}>
        {busy ? 'Creating…' : 'Create meeting'}
      </button>
      {error && <p className="status error" role="alert">{error}</p>}
    </form>
  );
}

function CreatedCard({ settings, state }: { settings: Settings; state: State<'created'> }) {
  const [consentChecked, setConsentChecked] = useState(false);
  const [fileError, setFileError] = useState('');
  const canStart = !settings.consentReminder || consentChecked;

  async function handleStart() {
    if (!(await ensureMicPermission())) return;
    sendToBackground({ type: 'START_RECORDING' });
  }

  async function handleDiscard() {
    await clearPendingAudio().catch(() => {});
    await recorderState.setValue({ status: 'idle' });
  }

  return (
    <section className="card stack">
      <p className="status">
        Meeting created: <strong>{state.title}</strong>
      </p>
      <p className="hint">
        Open your meeting tab, then record. Audio from the tab and your microphone is captured. Or choose an audio
        file you already have.
      </p>
      {settings.consentReminder && (
        <label className="checkbox">
          <input type="checkbox" checked={consentChecked} onChange={(e) => setConsentChecked(e.target.checked)} />
          Everyone in this meeting knows it is being recorded.
        </label>
      )}
      <button className="primary" disabled={!canStart} onClick={handleStart}>
        Start recording
      </button>

      <AudioFilePicker
        label="Or choose an audio file"
        onPicked={async (file) => {
          await saveChosenFile(state, file);
        }}
        onError={setFileError}
      />

      {(state.error || fileError) && (
        <p className="status error" role="alert">
          {fileError || state.error}
        </p>
      )}
      <button className="link" onClick={handleDiscard}>
        Discard this meeting
      </button>
    </section>
  );
}

function RecordedCard({
  settings,
  state,
  onSubmitStart,
  onSubmitEnd,
}: {
  settings: Settings;
  state: State<'recorded'>;
  onSubmitStart: () => void;
  onSubmitEnd: () => void;
}) {
  const [fileError, setFileError] = useState('');

  // upload the audio, then start processing
  async function handleSubmit() {
    onSubmitStart();
    setFileError('');
    try {
      await recorderState.setValue({ status: 'uploading', meetingId: state.meetingId, title: state.title });

      const audio = await loadPendingAudio();
      if (!audio) {
        await recorderState.setValue({
          status: 'created',
          meetingId: state.meetingId,
          title: state.title,
          error: 'The audio is no longer saved. Record again or choose a file.',
        });
        return;
      }

      await uploadMedia(settings, state.meetingId, audio.blob, audio.fileName);
      await startProcessing(settings, state.meetingId);
      await clearPendingAudio().catch(() => {});
      await recorderState.setValue({ status: 'processing', meetingId: state.meetingId, processingStatus: 'queued' });
    } catch (err) {
      // the audio is still saved, so they can just submit again
      await recorderState.setValue({ ...state, error: errorText(err, 'Could not submit the meeting.') });
    } finally {
      onSubmitEnd();
    }
  }

  async function handleDiscardAudio() {
    await clearPendingAudio().catch(() => {});
    await recorderState.setValue({ status: 'created', meetingId: state.meetingId, title: state.title });
  }

  const summary =
    state.source === 'recording'
      ? `Recording · ${formatClock(state.durationSeconds ?? 0)} · ${formatSize(state.sizeBytes)}`
      : `${state.fileName} · ${formatSize(state.sizeBytes)}`;

  return (
    <section className="card stack">
      <p className="status">
        Ready to submit: <strong>{state.title}</strong>
      </p>
      <p>{summary}</p>

      <AudioFilePicker
        label="Replace with an audio file"
        onPicked={async (file) => {
          await saveChosenFile(state, file);
        }}
        onError={setFileError}
      />
      <p className="hint">A meeting holds one audio file. Choosing a file replaces the one above.</p>

      {(state.error || fileError) && (
        <p className="status error" role="alert">
          {fileError || state.error}
        </p>
      )}

      <button className="primary" onClick={handleSubmit}>
        Submit for processing
      </button>
      <button className="link" onClick={handleDiscardAudio}>
        Discard this audio
      </button>
    </section>
  );
}

async function saveChosenFile(state: State<'created'> | State<'recorded'>, file: File) {
  await savePendingAudio({ blob: file, fileName: file.name });
  await recorderState.setValue({
    status: 'recorded',
    meetingId: state.meetingId,
    title: state.title,
    source: 'file',
    fileName: file.name,
    sizeBytes: file.size,
  });
}

// after an interrupted upload, go back to the submit step if the audio is still there
async function restoreAfterFailedSubmit(state: State<'uploading'>, message: string) {
  const audio = await loadPendingAudio().catch(() => undefined);
  if (!audio) {
    await recorderState.setValue({ status: 'created', meetingId: state.meetingId, title: state.title, error: message });
    return;
  }
  await recorderState.setValue({
    status: 'recorded',
    meetingId: state.meetingId,
    title: state.title,
    source: audio.fileName === RECORDING_FILE_NAME ? 'recording' : 'file',
    fileName: audio.fileName,
    sizeBytes: audio.blob.size,
    error: message,
  });
}

function AudioFilePicker({
  label,
  onPicked,
  onError,
}: {
  label: string;
  onPicked: (file: File) => Promise<void>;
  onError: (message: string) => void;
}) {
  async function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const input = e.target;
    const file = input.files?.[0];
    input.value = ''; // so picking the same file again still fires onChange
    if (!file) return;

    onError('');
    if (file.size === 0) return onError('That file is empty.');
    const type = audioTypeFor(file);
    if (!type) {
      return onError('That file type is not supported. Use WebM, WAV, MP3, M4A, OGG or FLAC audio.');
    }
    try {
      // relabel it so the upload has the audio type the backend expects
      await onPicked(type === file.type ? file : new File([file], file.name, { type, lastModified: file.lastModified }));
    } catch (err) {
      onError(errorText(err, 'Could not use that file.'));
    }
  }

  return (
    <label className="field">
      {label}
      <input type="file" accept={AUDIO_FILE_ACCEPT} onChange={handleChange} />
    </label>
  );
}

function AutoImportToggle({ settings }: { settings: Settings }) {
  return (
    <section className="card stack">
      <label className="checkbox">
        <input
          type="checkbox"
          checked={settings.autoImport}
          onChange={(e) => settingsItem.setValue({ ...settings, autoImport: e.target.checked })}
        />
        Auto-import confident stories
      </label>
      <p className="hint">
        {settings.autoImport
          ? `Stories at ${formatPercent(settings.autoImportThreshold)} confidence or higher, with evidence from the meeting, go straight to "${settings.targetList}" with an AI Draft label. Everything else waits for your review.`
          : 'Off. You approve every story before it reaches Trello.'}
      </p>
    </section>
  );
}
