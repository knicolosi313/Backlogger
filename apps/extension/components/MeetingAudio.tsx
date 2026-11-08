import { useCallback, useEffect, useRef, useState } from 'react';
import { getMeetingAudio, type ApiConfig } from '@/lib/api';
import { formatClock } from '@/lib/format';

interface Props {
  settings: ApiConfig;
  meetingId: string;
  moment: { seconds: number; click: number };
}

export function MeetingAudio({ settings, meetingId, moment }: Props) {
  const audio = useRef<HTMLAudioElement>(null);
  const [source, setSource] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = '';
    const element = audio.current;
    setSource('');
    setError('');
    setLoading(true);
    getMeetingAudio(settings, meetingId, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return;
        if (!blob.size) throw new Error('This meeting has no playable audio.');
        objectUrl = URL.createObjectURL(blob);
        setSource(objectUrl);
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted) {
          setError(failure instanceof Error ? failure.message : 'Could not load the recording.');
        }
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => {
      controller.abort();
      element?.pause();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [settings.apiUrl, settings.apiToken, meetingId, attempt]);

  const jumpToMoment = useCallback(async () => {
    const element = audio.current;
    if (!element || !source || element.readyState < 1) return;
    const seconds = Number.isFinite(moment.seconds) ? Math.max(0, moment.seconds) : 0;
    if (Number.isFinite(element.duration) && seconds >= element.duration) {
      element.pause();
      setError('This moment is beyond the end of the recording. You can play the recording below.');
      return;
    }
    try {
      element.currentTime = seconds;
      setError('');
      await element.play();
    } catch {
      setError('Press Play below to listen. If playback fails, try reloading the recording.');
    }
  }, [source, moment.seconds, moment.click]);

  useEffect(() => { void jumpToMoment(); }, [jumpToMoment]);

  return (
    <section className="card stack" aria-label="Meeting audio">
      <strong>Meeting audio · {formatClock(moment.seconds)}</strong>
      {loading && <p role="status">Loading recording…</p>}
      <audio
        ref={audio}
        src={source || undefined}
        controls
        preload="metadata"
        aria-label="Meeting recording"
        style={{ width: '100%' }}
        onLoadedMetadata={() => void jumpToMoment()}
        onError={() => { if (source) setError('Chrome could not play this recording. Try reloading it.'); }}
      />
      {error && <p className="status error" role="alert">{error}</p>}
      {!loading && error && <button onClick={() => setAttempt((value) => value + 1)}>Reload recording</button>}
    </section>
  );
}
