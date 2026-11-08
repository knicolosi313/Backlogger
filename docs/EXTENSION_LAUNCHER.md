# Open Backlogger from Trello and Jira

The extension adds an **Open Backlogger** button near the lower-right corner of
Trello and Jira Cloud pages. Clicking it opens Chrome's native side panel for
that tab. The button uses an isolated shadow root so site CSS does not affect it
and its styles do not change the site's UI. The adjacent × hides it until the
page is refreshed; normal single-page navigation does not bring it back.

Supported pages are `https://trello.com/*`, `https://www.trello.com/*`, and Jira
application routes on `https://*.atlassian.net/*`, including boards, issues,
projects, and service-desk pages. Trello's authorization endpoint and Confluence
`/wiki` routes are excluded. Custom Jira domains and Jira Data Center instances
are not included. The launcher reads the page URL to decide visibility; it does
not scrape board contents or send page data to the backend.

The content script sends `OPEN_BACKLOGGER` on the button click. The background
worker validates the sender's extension, top-level frame, site, and tab, then
calls `sidePanel.open` immediately. There is no tab lookup or storage wait before
opening, preserving the required user gesture. Failures show a toolbar/reload
fallback. See [Chrome's side-panel API](https://developer.chrome.com/docs/extensions/reference/api/sidePanel)
and [WXT content scripts](https://wxt.dev/guide/essentials/content-scripts).

Rebuild/reload the extension, approve site access if Chrome prompts, and refresh
existing Trello/Jira tabs to load the new content script. Opening a website does
not automatically open the panel. Opening from a page button also does not grant
the `activeTab` permission used for recording; Chrome may still require invoking
Backlogger through its toolbar icon before recording a meeting tab.

For the development build (`.output/chrome-mv3-dev`), keep `npm run dev` running
from `apps/extension`, reload the extension in Chrome if needed, then refresh the
Jira or Trello tab. Atlassian's consent pages on `api.atlassian.com` are not Jira
workspace pages and do not show the launcher. Finish authorization and open your
`https://your-team.atlassian.net/jira/...` workspace instead.

The launcher uses WXT's navigation event URL so it updates correctly even when
the event arrives before the browser's location changes.
