import { afterEach, expect, it, vi } from 'vitest';
import { ApiError, request } from '@/lib/http';

afterEach(() => vi.unstubAllGlobals());

it('downloads audio bytes with the same credentials as other backend calls', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(new Uint8Array([1, 2, 3]), {
    headers: { 'Content-Type': 'audio/webm' },
  }));
  vi.stubGlobal('fetch', fetch);
  const signal = new AbortController().signal;
  const blob = await request<Blob>({ apiUrl: 'https://api.example.test/', apiToken: 'test-token' }, '/audio', {
    responseType: 'blob', signal,
  });
  expect(blob.size).toBe(3);
  expect(blob.type).toBe('audio/webm');
  expect(fetch).toHaveBeenCalledWith('https://api.example.test/audio', expect.objectContaining({
    credentials: 'include', signal, headers: { Accept: 'audio/*', Authorization: 'Bearer test-token' },
  }));
});

it('still reads JSON errors when an audio download fails', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Meeting audio is unavailable' }), {
    status: 404, headers: { 'Content-Type': 'application/json' },
  })));
  await expect(request({ apiUrl: 'https://api.example.test', apiToken: '' }, '/audio', {
    responseType: 'blob',
  })).rejects.toThrow('Meeting audio is unavailable');
});

it('preserves cancellation so unmounted players can ignore it', async () => {
  const controller = new AbortController();
  controller.abort();
  const error = new DOMException('Download cancelled', 'AbortError');
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(error));
  await expect(request({ apiUrl: 'https://api.example.test', apiToken: '' }, '/audio', {
    responseType: 'blob', signal: controller.signal,
  })).rejects.toBe(error);
});

it('keeps normal JSON requests working', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"status":"ok"}')));
  const result = await request({ apiUrl: 'https://api.example.test', apiToken: '' }, '/health/db');
  expect(result).toEqual({ status: 'ok' });
  expect(result).not.toBeInstanceOf(ApiError);
});
