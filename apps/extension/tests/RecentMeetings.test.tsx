import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { RecentMeetings } from '@/components/RecentMeetings';
import { getMeetingAudio, listMeetings } from '@/lib/api';
import type { BoardConnection, MeetingSummary, Settings } from '@/lib/types';

vi.mock('@/lib/api', () => ({ getMeetingAudio: vi.fn(), listMeetings: vi.fn() }));

const settings = { apiUrl: 'https://api.example.test', apiToken: '' } as Settings;
const board: BoardConnection = { connected: true, boardId: 'board1' };
let container: HTMLDivElement;
let root: Root;

function meetings(): MeetingSummary[] {
  return Array.from({ length: 7 }, (_, index) => ({
    id: `meeting${index + 1}`, title: `Meeting ${index + 1}`, startedAt: `2026-11-${String(8 - index).padStart(2, '0')}T12:00:00Z`,
    status: 'ready', hasAudio: true,
  }));
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.mocked(listMeetings).mockReset().mockResolvedValue(meetings());
  vi.mocked(getMeetingAudio).mockReset().mockResolvedValue(new Blob(['recording'], { type: 'audio/webm' }));
  vi.stubGlobal('URL', class extends URL {
    static override createObjectURL = vi.fn().mockReturnValueOnce('blob:first-meeting').mockReturnValue('blob:next-meeting');
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

async function render(currentBoard = board) {
  await act(async () => root.render(createElement(RecentMeetings, {
    key: `${settings.apiUrl}:${currentBoard.boardId}:${currentBoard.connected}`,
    settings, board: currentBoard, refreshKey: 'done',
  })));
}

it('shows only the five newest entries returned by the ordered meeting API', async () => {
  await render();
  const rows = [...container.querySelectorAll('.meeting-row')];
  expect(rows).toHaveLength(5);
  expect(rows[0]?.textContent).toContain('Meeting 1');
  expect(rows[4]?.textContent).toContain('Meeting 5');
  expect(container.textContent).not.toContain('Meeting 6');
  expect(container.textContent).not.toContain('Meeting 7');
  expect(listMeetings).toHaveBeenCalledWith(settings, 'board1');
  expect(getMeetingAudio).not.toHaveBeenCalled();
});

it('opens a meeting recording in the extension from its beginning', async () => {
  await render();
  await act(async () => container.querySelector<HTMLButtonElement>('.meeting-row')!.click());
  expect(getMeetingAudio).toHaveBeenCalledWith(settings, 'meeting1', expect.any(AbortSignal));
  expect(container.querySelector('audio')?.currentTime).toBe(0);
  expect(container.querySelector('audio')?.controls).toBe(true);
  expect(container.querySelector('.meeting-row')?.getAttribute('aria-expanded')).toBe('true');
  expect(container.querySelector('a')).toBeNull();
});

it('switches meetings without playing multiple recordings', async () => {
  await render();
  const rows = container.querySelectorAll<HTMLButtonElement>('.meeting-row');
  await act(async () => rows[0]!.click());
  const firstSignal = vi.mocked(getMeetingAudio).mock.calls[0]![2]!;
  await act(async () => rows[1]!.click());
  expect(firstSignal.aborted).toBe(true);
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:first-meeting');
  expect(getMeetingAudio).toHaveBeenLastCalledWith(settings, 'meeting2', expect.any(AbortSignal));
  expect(container.querySelectorAll('audio')).toHaveLength(1);
});

it('closes and cleans up playback when requested', async () => {
  await render();
  await act(async () => container.querySelector<HTMLButtonElement>('.meeting-row')!.click());
  const close = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Close audio')!;
  await act(async () => close.click());
  expect(container.querySelector('audio')).toBeNull();
  expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:first-meeting');
});

it('marks meetings with no uploaded audio as unavailable', async () => {
  vi.mocked(listMeetings).mockResolvedValue([{ ...meetings()[0]!, hasAudio: false, status: 'awaiting_audio' }]);
  await render();
  const row = container.querySelector<HTMLButtonElement>('.meeting-row')!;
  expect(row.disabled).toBe(true);
  expect(row.textContent).toContain('No audio uploaded');
  row.click();
  expect(getMeetingAudio).not.toHaveBeenCalled();
});

it('shows an audio error if a previously uploaded file is missing', async () => {
  vi.mocked(getMeetingAudio).mockRejectedValue(new Error('Meeting audio is unavailable'));
  await render();
  await act(async () => container.querySelector<HTMLButtonElement>('.meeting-row')!.click());
  expect(container.textContent).toContain('Meeting audio is unavailable');
});

it('stops playback when switching boards', async () => {
  await render();
  await act(async () => container.querySelector<HTMLButtonElement>('.meeting-row')!.click());
  await render({ connected: true, boardId: 'board2' });
  expect(container.querySelector('audio')).toBeNull();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:first-meeting');
});

it('does not fetch meetings for a disconnected account', async () => {
  await render({ connected: false });
  expect(listMeetings).not.toHaveBeenCalled();
  expect(container.querySelector('.meeting-row')).toBeNull();
});
