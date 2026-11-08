import { afterEach, beforeEach, expect, it, vi } from 'vitest';

let cleanup: (() => void) | undefined;
let navigate: ((event: { newUrl: URL }) => void) | undefined;
let live: boolean;

beforeEach(() => {
  live = true;
  cleanup = undefined;
  navigate = undefined;
  vi.resetModules();
  vi.stubGlobal('defineContentScript', (options: unknown) => options);
  vi.stubGlobal('browser', { runtime: { sendMessage: vi.fn().mockResolvedValue({ opened: true }) } });
});

afterEach(() => {
  live = false;
  cleanup?.();
  document.querySelectorAll('backlogger-launcher').forEach((host) => host.remove());
  vi.unstubAllGlobals();
});

async function start(url: string) {
  // Preserve the real DOM but allow navigation notifications to precede location changes.
  vi.stubGlobal('window', { location: { href: url } });
  const entry = (await import('@/entrypoints/launcher.content')).default;
  const context = {
    get isValid() { return live; },
    addEventListener: (_target: unknown, event: string, listener: typeof navigate) => {
      if (event === 'wxt:locationchange') navigate = listener;
    },
    onInvalidated: (listener: () => void) => { cleanup = listener; },
  };
  type Context = NonNullable<Parameters<NonNullable<typeof entry.main>>[0]>;
  await entry.main(context as unknown as Context);
}

it('mounts the actual content-script launcher on a Trello board', async () => {
  await start('https://trello.com/b/board/product');
  expect(document.getElementById('backlogger-extension-launcher')).not.toBeNull();
  expect(document.getElementById('backlogger-extension-launcher')!.shadowRoot!.textContent).toContain('Open Backlogger');
});

it('uses the new Jira URL even when window.location still points to Confluence', async () => {
  await start('https://team.atlassian.net/wiki/spaces/APP');
  expect(document.getElementById('backlogger-extension-launcher')).toBeNull();
  navigate!({ newUrl: new URL('https://team.atlassian.net/jira/your-work') });
  await Promise.resolve(); // The document observer must use the new URL too.
  expect(document.getElementById('backlogger-extension-launcher')).not.toBeNull();
  expect(window.location.href).toContain('/wiki/');
  navigate!({ newUrl: new URL('https://team.atlassian.net/wiki/spaces/APP') });
  await Promise.resolve();
  expect(document.getElementById('backlogger-extension-launcher')).toBeNull();
});

it('restores a launcher removed by the page and cleans up when invalidated', async () => {
  await start('https://trello.com/b/board/product');
  document.getElementById('backlogger-extension-launcher')!.remove();
  await Promise.resolve();
  expect(document.getElementById('backlogger-extension-launcher')).not.toBeNull();
  live = false;
  cleanup!();
  await Promise.resolve();
  expect(document.getElementById('backlogger-extension-launcher')).toBeNull();
});
