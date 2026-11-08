import { createPageLauncher } from '@/lib/pageLauncher';
import type { LauncherReply, PageToBackground } from '@/lib/messages';

export default defineContentScript({
  matches: ['https://trello.com/*', 'https://www.trello.com/*', 'https://*.atlassian.net/*'],
  runAt: 'document_idle',
  main(ctx) {
    const launcher = createPageLauncher(() => browser.runtime.sendMessage<PageToBackground, LauncherReply>({ type: 'OPEN_BACKLOGGER' }));
    let currentUrl = window.location.href;
    const update = () => { if (ctx.isValid) launcher.update(currentUrl); };
    update();
    ctx.addEventListener(window, 'wxt:locationchange', ({ newUrl }) => {
      // WXT's Navigation API event can precede window.location being updated.
      currentUrl = newUrl.href;
      update();
    });
    // Watch only document-level changes, not every Trello/Jira render.
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { childList: true });
    ctx.onInvalidated(() => { observer.disconnect(); launcher.remove(); });
  },
});
