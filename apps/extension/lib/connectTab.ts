import { connectTabs } from './storage';
import type { Provider } from './types';

// Connecting happens in a separate tab: the backend sends the person to Trello or Atlassian, and its
// callback page saves the connection. The tab has nothing left to show after that, so we close it.

export async function openConnectTab(provider: Provider, url: string): Promise<void> {
  const tab = await browser.tabs.create({ url });
  if (tab.id === undefined) return;
  await connectTabs.setValue({ ...(await connectTabs.getValue()), [provider]: tab.id });
}

// Call this once the provider shows as connected. Does nothing if we didn't open a tab for it,
// for example when it was already connected before the panel opened.
export async function closeConnectTab(provider: Provider): Promise<void> {
  const tabs = await connectTabs.getValue();
  const tabId = tabs[provider];
  if (tabId === undefined) return;

  // forget it first, so it's only ever closed once
  const { [provider]: _closing, ...rest } = tabs;
  await connectTabs.setValue(rest);
  // the person may have closed it already
  await browser.tabs.remove(tabId).catch(() => {});
}
