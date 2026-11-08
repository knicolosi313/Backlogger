import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AutoImportProgress } from '@/components/AutoImportProgress';
import { getAutoImportStatus, retryAutoImport } from '@/lib/api';
import type { AutoImportStatus } from '@/lib/types';

vi.mock('@/lib/api', () => ({ getAutoImportStatus: vi.fn(), retryAutoImport: vi.fn() }));
let container: HTMLDivElement;
let root: Root;
const settings = { apiUrl: 'https://api.example.test', apiToken: '' };
function completed(): AutoImportStatus {
  return { meeting_id: 'meeting1', status: 'completed', imported: 2, skipped: 3, failed: 0, error: null, updated_at: new Date().toISOString() };
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.mocked(getAutoImportStatus).mockReset().mockResolvedValue(completed());
  vi.mocked(retryAutoImport).mockReset();
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});
async function render() {
  await act(async () => root.render(createElement(AutoImportProgress, { settings, meetingId: 'meeting1' })));
}

it('shows server-recorded imported, review, and failure counts', async () => {
  await render();
  expect(container.textContent).toContain('2 imported');
  expect(container.textContent).toContain('3 left for review');
  expect(container.textContent).toContain('0 failed');
});

it('does not show an import result when the meeting did not opt in', async () => {
  vi.mocked(getAutoImportStatus).mockResolvedValue(null);
  await render();
  expect(container.textContent).toBe('');
});

it('retries a failed run and refreshes the saved result', async () => {
  vi.mocked(getAutoImportStatus).mockResolvedValueOnce({ ...completed(), status: 'failed', failed: 1, error: 'Reconnect then retry.' });
  vi.mocked(retryAutoImport).mockResolvedValue(completed());
  await render();
  expect(container.textContent).toContain('Reconnect then retry');
  await act(async () => container.querySelector('button')!.click());
  expect(retryAutoImport).toHaveBeenCalledWith(settings, 'meeting1');
  expect(container.textContent).toContain('0 failed');
  expect(container.querySelector('button')).toBeNull();
});

it('keeps the user informed when no stories qualify', async () => {
  vi.mocked(getAutoImportStatus).mockResolvedValue({ ...completed(), imported: 0 });
  await render();
  expect(container.textContent).toContain('No stories met all auto-import checks');
});
