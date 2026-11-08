import type { AnyMessage } from '@/lib/messages';
import { recorderState } from '@/lib/storage';

// Background worker coordinates audio recording 

const STOP_TIMEOUT_MS = 20_000;

export default defineBackground(() => {
  // We open the panel ourselves instead of using openPanelOnActionClick, because clicking
  // the icon is also what grants activeTab, which tab capture needs.
  browser.action.onClicked.addListener((tab) => {
    if (tab.id !== undefined) void browser.sidePanel.open({ tabId: tab.id });
  });

  browser.runtime.onMessage.addListener((message: AnyMessage) => {
    switch (message.type) {
      case 'START_RECORDING':
        void startRecording();
        break;
      case 'STOP_RECORDING':
        void stopRecording();
        break;
      case 'REC_READY':
        void onRecorded(message.durationSeconds, message.sizeBytes);
        break;
      case 'REC_ERROR':
        void fail(message.message);
        break;
    }
  });
});

async function startRecording() {
  try {
    const current = await recorderState.getValue();
    if (current.status !== 'created') throw new Error('Create a meeting first, then start recording.');

    // a service worker has no current window, so use the last focused one
    const [tab] = await browser.tabs.query({ active: true, lastFocusedWindow: true });
    if (!tab?.id) throw new Error('Open the meeting tab first, then click the extension icon.');

    const streamId = await browser.tabCapture.getMediaStreamId({ targetTabId: tab.id });

    await ensureOffscreenDocument();
    await browser.runtime.sendMessage({ type: 'OFFSCREEN_START', streamId });

    await recorderState.setValue({
      status: 'recording',
      meetingId: current.meetingId,
      title: current.title,
      startedAt: Date.now(),
    });
    await browser.action.setBadgeText({ text: 'REC' });
    await browser.action.setBadgeBackgroundColor({ color: '#c62828' });
  } catch (err) {
    await fail(err instanceof Error ? err.message : 'Could not start recording.');
  }
}

async function stopRecording() {
  const current = await recorderState.getValue();
  if (current.status !== 'recording') return;

  // If the offscreen page is gone (extension reloaded, browser killed it) nothing is
  // recording, so reset instead of leaving the panel stuck on "Recording".
  const existing = await browser.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] });
  if (existing.length === 0) {
    await fail('The recorder was no longer running, so nothing was saved. Start recording again.');
    return;
  }

  try {
    const reply = (await browser.runtime.sendMessage({ type: 'OFFSCREEN_STOP' })) as
      | { recording?: boolean }
      | undefined;
    if (reply?.recording === false) {
      await fail('The recorder was not recording, so nothing was saved. Start recording again.');
      return;
    }
  } catch (err) {
    console.warn('could not reach the recorder to stop it', err);
  }

  // The offscreen page should answer with REC_READY or REC_ERROR. If it never does,
  // don't leave the Stop button dead.
  await new Promise((resolve) => setTimeout(resolve, STOP_TIMEOUT_MS));
  if ((await recorderState.getValue()).status === 'recording') {
    await fail('Stopping the recording timed out. Try recording again.');
  }
}

async function onRecorded(durationSeconds: number, sizeBytes: number) {
  await clearBadge();
  await closeOffscreenDocument();

  const current = await recorderState.getValue();
  if (current.status !== 'recording') return;
  await recorderState.setValue({
    status: 'recorded',
    meetingId: current.meetingId,
    title: current.title,
    source: 'recording',
    fileName: 'recording.webm',
    sizeBytes,
    durationSeconds,
  });
}

// The meeting already exists on the server, so go back to "created" and let the user retry.
async function fail(message: string) {
  await clearBadge();
  await closeOffscreenDocument();

  const current = await recorderState.getValue();
  if (current.status === 'created' || current.status === 'recording') {
    await recorderState.setValue({ status: 'created', meetingId: current.meetingId, title: current.title, error: message });
  } else {
    await recorderState.setValue({ status: 'error', message });
  }
}

async function ensureOffscreenDocument() {
  const existing = await browser.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] });
  if (existing.length > 0) return;

  await browser.offscreen.createDocument({
    url: 'offscreen.html',
    reasons: ['USER_MEDIA'],
    justification: 'Record meeting audio from the tab and the microphone.',
  });
}

async function closeOffscreenDocument() {
  const existing = await browser.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] });
  if (existing.length > 0) await browser.offscreen.closeDocument();
}

function clearBadge() {
  return browser.action.setBadgeText({ text: '' });
}
