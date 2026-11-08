import type { LauncherReply } from './messages';

export function isLauncherPage(url: string): boolean {
  try {
    const page = new URL(url);
    if (page.protocol !== 'https:') return false;
    if (page.hostname === 'trello.com' || page.hostname === 'www.trello.com') return !page.pathname.startsWith('/1/');
    return page.hostname.endsWith('.atlassian.net') && (
      page.pathname === '/' || /^\/(jira|browse|issues|projects|secure|servicedesk)(\/|$)/.test(page.pathname)
      || page.pathname.startsWith('/plugins/servlet/project-config/')
    );
  } catch {
    return false;
  }
}

const HELP = 'Could not open Backlogger. Click its Chrome toolbar icon, or refresh this page after reloading the extension.';

export function createPageLauncher(openPanel: () => Promise<LauncherReply>) {
  const host = document.createElement('backlogger-launcher');
  host.id = 'backlogger-extension-launcher';
  const root = host.attachShadow({ mode: 'open' });
  const style = document.createElement('style');
  style.textContent = `
    :host { all: initial; position: fixed !important; right: 20px !important; bottom: 72px !important;
      z-index: 2147483647 !important; color-scheme: dark; font: 13px/1.4 system-ui, sans-serif; }
    .buttons { display: flex; gap: 2px; align-items: stretch; border-radius: 24px; background: #7040cd;
      box-shadow: 0 4px 18px #0005; border: 1px solid #b69bea; overflow: hidden; }
    button { all: unset; box-sizing: border-box; cursor: pointer; color: #fff; font: inherit; }
    .open { padding: 11px 14px; font-weight: 650; }
    .hide { padding: 8px 12px; border-left: 1px solid #ffffff35; font-size: 18px; }
    button:hover { background: #ffffff20; }
    button:focus-visible { outline: 3px solid #fff; outline-offset: -4px; border-radius: 20px; }
    button:disabled { cursor: wait; opacity: .7; }
    p { margin: 8px 0 0; padding: 10px; max-width: 240px; background: #20172f; color: #fff;
      border: 1px solid #b69bea; border-radius: 8px; font: inherit; }
    [hidden] { display: none; }
    @media (max-width: 400px) { :host { right: 10px !important; bottom: 64px !important; } }
  `;
  const buttons = document.createElement('div');
  buttons.className = 'buttons';
  const open = document.createElement('button');
  open.className = 'open';
  open.type = 'button';
  open.textContent = 'Open Backlogger';
  const hide = document.createElement('button');
  hide.className = 'hide';
  hide.type = 'button';
  hide.textContent = '×';
  hide.setAttribute('aria-label', 'Hide Backlogger button until this page is refreshed');
  const notice = document.createElement('p');
  notice.setAttribute('role', 'status');
  notice.hidden = true;
  let dismissed = false;

  open.addEventListener('click', () => {
    notice.hidden = true;
    open.disabled = true;
    // Start messaging directly in the click handler to preserve Chrome's user gesture.
    try {
      openPanel().then((reply) => {
        if (!reply?.opened) { notice.textContent = HELP; notice.hidden = false; }
      }).catch(() => { notice.textContent = HELP; notice.hidden = false; })
        .finally(() => { open.disabled = false; });
    } catch {
      notice.textContent = HELP;
      notice.hidden = false;
      open.disabled = false;
    }
  });
  hide.addEventListener('click', () => { dismissed = true; host.remove(); });
  buttons.append(open, hide);
  root.append(style, buttons, notice);

  return {
    update(url: string) {
      if (!dismissed && isLauncherPage(url)) {
        if (!host.isConnected) document.documentElement.append(host);
      } else host.remove();
    },
    remove() { host.remove(); },
  };
}

interface PageSender {
  id?: string;
  url?: string;
  frameId?: number;
  tab?: { id?: number };
}

export function openPanelFromPage(
  sender: PageSender, extensionId: string,
  open: (options: { tabId: number }) => Promise<void>,
): Promise<LauncherReply> {
  if (sender.id !== extensionId || sender.frameId !== 0 || sender.tab?.id === undefined || !sender.url || !isLauncherPage(sender.url)) {
    return Promise.resolve({ opened: false, error: 'Open Backlogger from a Trello or Jira page.' });
  }
  try {
    // Do not query tabs or await anything before this API call: Chrome requires a user gesture.
    return open({ tabId: sender.tab.id }).then(() => ({ opened: true })).catch(() => ({ opened: false, error: HELP }));
  } catch {
    return Promise.resolve({ opened: false, error: HELP });
  }
}
