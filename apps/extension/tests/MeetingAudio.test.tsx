import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getMeetingAudio } from '@/lib/api';
import type { Settings, Story } from '@/lib/types';
import { MeetingAudio } from '@/components/MeetingAudio';
import { StoryDetail } from '@/components/StoryDetail';

vi.mock('@/lib/api', () => ({
  getMeetingAudio: vi.fn(), decideStory: vi.fn(), requestEdits: vi.fn(), updateStory: vi.fn(),
}));

const settings = { apiUrl: 'http://127.0.0.1:8000', apiToken: '', webAppUrl: 'http://localhost:5173' } as Settings;
let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.mocked(getMeetingAudio).mockReset().mockResolvedValue(new Blob(['recording'], { type: 'audio/webm' }));
  vi.stubGlobal('URL', class extends URL {
    static override createObjectURL = vi.fn(() => 'blob:meeting-audio');
    static override revokeObjectURL = vi.fn();
  });
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, 'readyState', 'get').mockReturnValue(1);
  vi.spyOn(HTMLMediaElement.prototype, 'duration', 'get').mockReturnValue(120);
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function renderMoment(seconds: number, click = 1) {
  await act(async () => root.render(createElement(MeetingAudio, {
    settings, meetingId: 'meeting-one', moment: { seconds, click },
  })));
}

describe('meeting evidence playback', () => {
  it('opens the player in the story instead of navigating to a web app', async () => {
    const story: Story = {
      id: 'story-one', meetingId: 'meeting-one', status: 'draft', title: 'Reset password',
      persona: 'member', goal: 'reset a password', benefit: 'access my account',
      acceptanceCriteria: ['Send a reset email'], priority: 'med', points: null,
      evidence: [{ startSeconds: 2, endSeconds: 5, speaker: 'Alex', quote: 'Send a reset email' }],
      confidence: 0.9, qualityScore: 80, investChecks: [], critique: '', openQuestions: [], unsupportedAssumptions: [],
    };
    await act(async () => root.render(createElement(StoryDetail, {
      story, settings, onChange: vi.fn(), onBack: vi.fn(),
    })));
    expect(container.querySelector('audio')).toBeNull();
    const jump = [...container.querySelectorAll('button')].find((button) => button.textContent?.includes('Jump to moment'))!;
    expect(jump.textContent).toContain('00:02');
    expect(container.querySelector('a[href*="/meetings/"]')).toBeNull();
    await act(async () => jump.click());
    expect(container.querySelector('audio')?.currentTime).toBe(2);
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalled();
    expect(getMeetingAudio).toHaveBeenCalledWith(settings, 'meeting-one', expect.any(AbortSignal));
  });

  it('seeks again without downloading the recording again', async () => {
    await renderMoment(2);
    await renderMoment(18, 2);
    expect(container.querySelector('audio')?.currentTime).toBe(18);
    await renderMoment(18, 3);
    expect(getMeetingAudio).toHaveBeenCalledTimes(1);
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(3);
  });

  it('waits for audio metadata before seeking', async () => {
    vi.mocked(Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'readyState')!.get!).mockReturnValue(0);
    await renderMoment(2);
    expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled();
    const element = container.querySelector('audio')!;
    Object.defineProperty(element, 'readyState', { value: 1 });
    await act(async () => element.dispatchEvent(new Event('loadedmetadata')));
    expect(element.currentTime).toBe(2);
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalled();
  });

  it('offers a retry when the recording is missing or unavailable', async () => {
    vi.mocked(getMeetingAudio).mockRejectedValueOnce(new Error('Meeting audio is unavailable'));
    await renderMoment(2);
    expect(container.textContent).toContain('Meeting audio is unavailable');
    await act(async () => container.querySelector('button')!.click());
    expect(getMeetingAudio).toHaveBeenCalledTimes(2);
    expect(container.querySelector('audio')?.currentTime).toBe(2);
  });

  it('keeps audio controls available when autoplay is blocked', async () => {
    vi.mocked(HTMLMediaElement.prototype.play).mockRejectedValue(new Error('Not allowed'));
    await renderMoment(2);
    expect(container.textContent).toContain('Press Play below');
    expect(container.querySelector('audio')?.controls).toBe(true);
  });

  it('pauses and releases the audio when the player is removed', async () => {
    await renderMoment(2);
    const signal = vi.mocked(getMeetingAudio).mock.calls[0]![2]!;
    await act(async () => root.render(null));
    expect(signal.aborted).toBe(true);
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:meeting-audio');
  });

  it('ignores a download that finishes after the player is removed', async () => {
    let finish!: (blob: Blob) => void;
    vi.mocked(getMeetingAudio).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    await renderMoment(2);
    await act(async () => root.render(null));
    await act(async () => finish(new Blob(['late audio'])));
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it('does not play a timestamp beyond the recording', async () => {
    await renderMoment(200);
    expect(container.textContent).toContain('beyond the end');
    expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled();
  });
});
