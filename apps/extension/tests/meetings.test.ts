import { beforeEach, expect, it, vi } from 'vitest';
import { listMeetings } from '@/lib/api';
import { request } from '@/lib/http';

vi.mock('@/lib/http', async (original) => ({ ...await original<typeof import('@/lib/http')>(), request: vi.fn() }));
beforeEach(() => vi.mocked(request).mockReset());

it('requests only five meetings from the backend and exposes audio availability', async () => {
  vi.mocked(request).mockResolvedValue([
    { id: 'newest', title: 'Newest', created_at: '2026-11-08T12:00:00Z', status: 'completed', media_key: 'supabase://media/recording.webm' },
    { id: 'older', title: 'Older', created_at: '2026-11-07T12:00:00Z', status: 'uploaded', media_key: null },
  ]);
  const settings = { apiUrl: 'https://api.example.test', apiToken: '' };
  const result = await listMeetings(settings, 'board1');
  expect(request).toHaveBeenCalledWith(settings, '/api/meetings?board_id=board1&limit=5');
  expect(result.map((meeting) => [meeting.id, meeting.hasAudio])).toEqual([['newest', true], ['older', false]]);
  expect(result[1]?.status).toBe('awaiting_audio');
});
