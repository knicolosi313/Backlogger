import { afterEach, expect, it, vi } from 'vitest';
import { createPageLauncher, isLauncherPage, openPanelFromPage } from '@/lib/pageLauncher';

afterEach(() => document.querySelectorAll('backlogger-launcher').forEach((host) => host.remove()));

it.each([
  'https://trello.com/b/board/product', 'https://trello.com/c/card/story', 'https://www.trello.com/',
  'https://team.atlassian.net/', 'https://team.atlassian.net/jira/software/projects/APP/boards/1',
  'https://team.atlassian.net/browse/APP-1', 'https://team.atlassian.net/issues/',
  'https://team.atlassian.net/plugins/servlet/project-config/APP/details',
])('supports the Trello/Jira page %s', (url) => expect(isLauncherPage(url)).toBe(true));

it.each([
  'https://team.atlassian.net/wiki/spaces/APP', 'https://trello.com/1/authorize',
  'https://trello.com.evil.test/b/board', 'https://team.atlassian.net.evil.test/jira/',
  'https://evil.test/?next=https://trello.com', 'https://atlassian.net/',
  'http://team.atlassian.net/jira/', 'not a URL',
])('does not appear on the unrelated page %s', (url) => expect(isLauncherPage(url)).toBe(false));

function launcherRoot() {
  return document.getElementById('backlogger-extension-launcher')!.shadowRoot!;
}

it('mounts one isolated launcher across repeated SPA updates', () => {
  const launcher = createPageLauncher(vi.fn());
  launcher.update('https://trello.com/b/one');
  launcher.update('https://trello.com/b/two');
  expect(document.querySelectorAll('backlogger-launcher')).toHaveLength(1);
  expect(launcherRoot().querySelector('.open')?.textContent).toBe('Open Backlogger');
  expect(document.querySelector('.open')).toBeNull(); // Styles and controls stay inside the shadow root.
  launcher.update('https://team.atlassian.net/wiki/spaces/APP');
  expect(document.querySelector('backlogger-launcher')).toBeNull();
  launcher.update('https://team.atlassian.net/jira/your-work');
  expect(document.querySelectorAll('backlogger-launcher')).toHaveLength(1);
  launcher.remove();
  expect(document.querySelector('backlogger-launcher')).toBeNull();
});

it('starts opening directly on click and prevents duplicate pending requests', async () => {
  let finish!: (value: { opened: boolean }) => void;
  const open = vi.fn(() => new Promise<{ opened: boolean }>((resolve) => { finish = resolve; }));
  const launcher = createPageLauncher(open);
  launcher.update('https://trello.com/b/one');
  const button = launcherRoot().querySelector<HTMLButtonElement>('.open')!;
  expect(open).not.toHaveBeenCalled();
  button.click();
  expect(open).toHaveBeenCalledTimes(1); // No async hop before messaging.
  expect(button.disabled).toBe(true);
  button.click();
  expect(open).toHaveBeenCalledTimes(1);
  finish({ opened: true });
  await vi.waitFor(() => expect(button.disabled).toBe(false));
  expect(launcherRoot().querySelector<HTMLParagraphElement>('p')!.hidden).toBe(true);
});

it('shows a useful fallback when the extension has been reloaded', async () => {
  const launcher = createPageLauncher(vi.fn().mockRejectedValue(new Error('Internal secret detail')));
  launcher.update('https://trello.com/b/one');
  launcherRoot().querySelector<HTMLButtonElement>('.open')!.click();
  const notice = launcherRoot().querySelector<HTMLParagraphElement>('p')!;
  await vi.waitFor(() => expect(notice.hidden).toBe(false));
  expect(notice.textContent).toContain('Chrome toolbar icon');
  expect(notice.textContent).not.toContain('Internal secret');
});

it('lets users dismiss the button for the current page session', () => {
  const launcher = createPageLauncher(vi.fn());
  launcher.update('https://trello.com/b/one');
  launcherRoot().querySelector<HTMLButtonElement>('.hide')!.click();
  launcher.update('https://trello.com/b/two');
  expect(document.querySelector('backlogger-launcher')).toBeNull();
});

const sender = { id: 'extension-id', frameId: 0, url: 'https://team.atlassian.net/jira/your-work', tab: { id: 42 } };

it('opens the sender tab immediately, preserving the user gesture', async () => {
  const open = vi.fn().mockResolvedValue(undefined);
  const reply = openPanelFromPage(sender, 'extension-id', open);
  expect(open).toHaveBeenCalledWith({ tabId: 42 });
  await expect(reply).resolves.toEqual({ opened: true });
});

it.each([
  { ...sender, id: 'another-extension' }, { ...sender, frameId: 1 },
  { ...sender, tab: {} }, { ...sender, url: 'https://unrelated.example.test/' },
])('rejects an invalid launcher sender', async (invalid) => {
  const open = vi.fn();
  await expect(openPanelFromPage(invalid, 'extension-id', open)).resolves.toMatchObject({ opened: false });
  expect(open).not.toHaveBeenCalled();
});

it('returns a failure instead of losing the message reply when Chrome refuses to open', async () => {
  await expect(openPanelFromPage(sender, 'extension-id', vi.fn().mockRejectedValue(new Error('Gesture expired'))))
    .resolves.toMatchObject({ opened: false });
});
