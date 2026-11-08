import type { AnyMessage, OffscreenToBackground } from '@/lib/messages';
import { savePendingAudio } from '@/lib/pendingAudio';

// Hidden page that does the actual recording. When it stops, the audio goes into
// IndexedDB and the user submits it from the Record tab. Nothing is uploaded from here.

let recorder: MediaRecorder | null = null;
let audioContext: AudioContext | null = null;
let sourceStreams: MediaStream[] = [];
let chunks: Blob[] = [];
let startedAt: number | null = null;
let finishing = false; // from the recorder stopping until REC_READY or REC_ERROR is sent

browser.runtime.onMessage.addListener((message: AnyMessage, _sender, sendResponse) => {
  if (message.type === 'OFFSCREEN_START') void start(message);
  if (message.type === 'OFFSCREEN_STOP') {
    const active = finishing || (recorder !== null && recorder.state !== 'inactive');
    // tell the background right away whether anything was actually recording
    sendResponse({ recording: active });
    stop();
    // streams can still be open if a start never finished
    if (!active) releaseResources();
  }
});

async function start(msg: Extract<AnyMessage, { type: 'OFFSCREEN_START' }>) {
  try {
    // the meeting tab's audio (what everyone else says). TypeScript's DOM types don't
    // know about "mandatory", hence the cast
    const tabStream = await navigator.mediaDevices.getUserMedia({
      audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: msg.streamId } },
      video: false,
    } as unknown as MediaStreamConstraints);

    // keep track of each stream as soon as we have it so a failure below still stops it
    sourceStreams = [tabStream];
    const micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    sourceStreams = [tabStream, micStream];

    // mix both into one track. Tab capture mutes the tab, so also play it back to the speakers
    audioContext = new AudioContext();
    const mixed = audioContext.createMediaStreamDestination();
    const tabSource = audioContext.createMediaStreamSource(tabStream);
    tabSource.connect(mixed);
    tabSource.connect(audioContext.destination);
    audioContext.createMediaStreamSource(micStream).connect(mixed);

    // low bitrate is plenty for speech, about 14 MB an hour
    chunks = [];
    recorder = new MediaRecorder(mixed.stream, {
      mimeType: 'audio/webm;codecs=opus',
      audioBitsPerSecond: 32_000,
    });
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };
    recorder.onstop = () => void finish();
    recorder.onerror = (e) => console.error('MediaRecorder error', e);
    recorder.start(10_000); // a chunk every 10s keeps memory steady

    startedAt = Date.now();

    // if the meeting tab gets closed, stop and keep what we have
    tabStream.getAudioTracks()[0]?.addEventListener('ended', stop);
  } catch (err) {
    console.error('could not start recording', err);
    releaseResources();
    send({ type: 'REC_ERROR', message: describeStartError(err) });
  }
}

function describeStartError(err: unknown): string {
  if (!(err instanceof Error)) return 'Could not start recording.';
  if (err.name === 'NotAllowedError') {
    return 'Microphone access is blocked. Allow it on the permissions page, then try again.';
  }
  return err.message;
}

function stop() {
  if (recorder && recorder.state !== 'inactive') recorder.stop(); // onstop calls finish()
}

async function finish() {
  finishing = true;
  try {
    const began = startedAt;
    const audio = new Blob(chunks, { type: 'audio/webm' });
    releaseResources();
    if (began === null) return;
    if (audio.size === 0) {
      send({ type: 'REC_ERROR', message: 'Nothing was recorded. Try again.' });
      return;
    }

    try {
      await savePendingAudio({ blob: audio, fileName: 'recording.webm' });
      send({
        type: 'REC_READY',
        durationSeconds: Math.round((Date.now() - began) / 1000),
        sizeBytes: audio.size,
      });
    } catch (err) {
      console.error('could not save the recording', err);
      send({ type: 'REC_ERROR', message: err instanceof Error ? err.message : 'Could not save the recording.' });
    }
  } finally {
    finishing = false;
  }
}

function releaseResources() {
  sourceStreams.forEach((s) => s.getTracks().forEach((t) => t.stop()));
  sourceStreams = [];
  void audioContext?.close();
  audioContext = null;
  recorder = null;
  chunks = [];
  startedAt = null;
}

function send(message: OffscreenToBackground) {
  browser.runtime.sendMessage(message).catch(() => {});
}
