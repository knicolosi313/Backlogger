import { useId, useState } from 'react';
import { listMeetings } from '@/lib/api';
import { formatShortDate } from '@/lib/format';
import { PROCESSING_LABELS } from '@/lib/options';
import { PICK_DESTINATION_HINT } from '@/lib/provider';
import type { BoardConnection, Settings } from '@/lib/types';
import { useLoad } from '@/lib/useLoad';
import { MeetingAudio } from './MeetingAudio';

interface Props {
  settings: Settings;
  board: BoardConnection;
  refreshKey: string;
}

export function RecentMeetings({ settings, board, refreshKey }: Props) {
  const [selected, setSelected] = useState<string | null>(null);
  const playerId = useId();
  const meetings = useLoad(
    () => board.connected && board.boardId ? listMeetings(settings, board.boardId) : Promise.resolve([]),
    [settings.apiUrl, settings.apiToken, board.boardId, board.connected, refreshKey],
  );
  const recent = meetings.data?.slice(0, 5) ?? [];

  return (
    <section className="stack">
      <h2>Recent meetings</h2>
      {!board.connected && <p className="hint">{PICK_DESTINATION_HINT} to see your meetings.</p>}
      {board.connected && <>
        {meetings.error && <p className="status error" role="alert">{meetings.error}</p>}
        {meetings.loading && !meetings.data && <p className="hint">Loading meetings…</p>}
        {meetings.data?.length === 0 && <p className="hint">No meetings yet.</p>}
        <ul className="list">
          {recent.map((meeting) => (
            <li key={meeting.id}>
              <button
                className="story-row meeting-row"
                disabled={!meeting.hasAudio}
                aria-expanded={selected === meeting.id}
                aria-controls={selected === meeting.id ? playerId : undefined}
                onClick={() => setSelected((current) => current === meeting.id ? null : meeting.id)}
              >
                <strong>{meeting.title}</strong>
                <span className="hint">{formatShortDate(meeting.startedAt)} · {PROCESSING_LABELS[meeting.status]}</span>
                <span className="hint">{!meeting.hasAudio ? 'No audio uploaded' : selected === meeting.id ? 'Hide audio' : 'Play audio'}</span>
              </button>
              {selected === meeting.id && meeting.hasAudio && (
                <div id={playerId} className="stack">
                  <MeetingAudio key={meeting.id} settings={settings} meetingId={meeting.id} moment={{ seconds: 0, click: 1 }} />
                  <button className="link" onClick={() => setSelected(null)}>Close audio</button>
                </div>
              )}
            </li>
          ))}
        </ul>
      </>}
    </section>
  );
}
