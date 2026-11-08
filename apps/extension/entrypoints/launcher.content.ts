import { createPageLauncher } from '@/lib/pageLauncher';
import type { LauncherReply, PageToBackground } from '@/lib/messages';

export default defineContentScript({
  matches: ['https://trello.com/*', 'https://www.trello.com/*', 'https://*.atlassian.net/*'],
  runAt: 'document_idle',
  main(ctx) {
    const launcher = createPageLauncher(() => browser.runtime.sendMessage<PageToBackground, LauncherReply>({ type: 'OPEN_BACKLOGGER' }));
    const update = () => { if (ctx.isValid) launcher.update(window.location.href); };
    update();
    ctx.addEventListener(window, 'wxt:locationchange', update);
    // Watch only document-level changes, not every Trello/Jira render.
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { childList: true });
    ctx.onInvalidated(() => { observer.disconnect(); launcher.remove(); });
  },
});
