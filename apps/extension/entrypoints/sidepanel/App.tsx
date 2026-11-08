import { useEffect, useState } from 'react';
import { AnalyticsTab } from '@/components/AnalyticsTab';
import { DraftsTab } from '@/components/DraftsTab';
import { Icon, type IconName } from '@/components/Icon';
import { RecordTab } from '@/components/RecordTab';
import { SettingsTab } from '@/components/SettingsTab';
import {
  getJiraConnection,
  jiraFieldContext,
  listJiraIssueTypes,
  listJiraProjects,
  listJiraRequiredFields,
  listJiraSites,
  parseJiraBoardExternalId,
  pickDefaultIssueType,
} from '@/lib/jira';
import { boardNoun, boardProvider } from '@/lib/provider';
import { getModels } from '@/lib/api';
import { closeConnectTab } from '@/lib/connectTab';
import { forgetProvider, settingsItem } from '@/lib/storage';
import { getTrelloConnection, listTrelloBoards, listTrelloLists } from '@/lib/trello';
import type { BoardConnection, TrelloList } from '@/lib/types';
import { useAutoReload, useLoad } from '@/lib/useLoad';
import { useStorageItem } from '@/lib/useStorageItem';

const TABS = [
  { id: 'record', label: 'Record', icon: 'record' },
  { id: 'drafts', label: 'Drafts', icon: 'drafts' },
  { id: 'analytics', label: 'Analytics', icon: 'analytics' },
  { id: 'settings', label: 'Settings', icon: 'settings' },
] as const satisfies ReadonlyArray<{ id: string; label: string; icon: IconName }>;

type TabId = (typeof TABS)[number]['id'];

export default function App() {
  const settings = useStorageItem(settingsItem);
  const [tab, setTab] = useState<TabId>('record');

  // the story open on the Drafts tab. Kept here so other tabs can jump to one
  const [selectedStoryId, setSelectedStoryId] = useState<string | null>(null);

  const apiUrl = settings?.apiUrl;
  const apiToken = settings?.apiToken;
  const selectedBoard = settings?.selectedBoard;
  const externalBoardId = selectedBoard?.externalId;

  // is this browser connected to a Trello account? Boards and lists need it.
  const connection = useLoad(
    () => (apiUrl === undefined ? Promise.resolve(undefined) : getTrelloConnection({ apiUrl, apiToken: apiToken ?? '' })),
    [apiUrl, apiToken],
  );
  const connected = connection.data?.connected === true;

  // ...or to a Jira account? Only one of the two is connected at a time.
  const jiraConnection = useLoad(
    () => (apiUrl === undefined ? Promise.resolve(undefined) : getJiraConnection({ apiUrl, apiToken: apiToken ?? '' })),
    [apiUrl, apiToken],
  );
  const jiraConnected = jiraConnection.data?.connected === true;

  // after clicking Connect the user finishes in another tab, so keep checking until it works.
  // Once either one is connected the other can't be, so there's nothing to wait for.
  useAutoReload(connection.reload, 3000, !connected && !jiraConnected);
  useAutoReload(jiraConnection.reload, 3000, !connected && !jiraConnected);

  // The models the server offers. A saved model it no longer offers (an old default, say) would
  // be rejected when processing starts, so it goes back to the server's default.
  const models = useLoad(
    () => (apiUrl === undefined ? Promise.resolve(undefined) : getModels({ apiUrl, apiToken: apiToken ?? '' })),
    [apiUrl, apiToken],
  );
  const savedModel = settings?.model;
  const offeredModels = models.data?.models;
  useEffect(() => {
    if (!savedModel || !offeredModels || offeredModels.includes(savedModel)) return;
    void settingsItem.getValue().then((latest) => {
      if (latest.model === savedModel) return settingsItem.setValue({ ...latest, model: '' });
    });
  }, [savedModel, offeredModels]);

  // the tab Connect opened has nothing left to show once the connection is saved
  useEffect(() => {
    if (connected) void closeConnectTab('trello');
  }, [connected]);
  useEffect(() => {
    if (jiraConnected) void closeConnectTab('jira');
  }, [jiraConnected]);

  // Trello boards for the board picker
  const boards = useLoad(
    () => (apiUrl === undefined || !connected ? Promise.resolve([]) : listTrelloBoards({ apiUrl, apiToken: apiToken ?? '' })),
    [apiUrl, apiToken, connected],
  );

  // lists on the selected board, for the import destination. They remember which board they
  // came from because useLoad keeps the old data while the next board's lists load
  const listsLoad = useLoad(async () => {
    if (apiUrl === undefined || !connected || !externalBoardId) return undefined;
    const items = await listTrelloLists({ apiUrl, apiToken: apiToken ?? '' }, externalBoardId);
    return { boardId: externalBoardId, items };
  }, [apiUrl, apiToken, connected, externalBoardId]);
  const lists = {
    items: listsLoad.data?.boardId === externalBoardId ? listsLoad.data?.items : undefined,
    error: listsLoad.error,
    loading: listsLoad.loading,
  };

  // imports need a list. If none is saved (or it's gone from the board), use "Triage" or the first one
  const targetListId = settings?.targetListId;
  useEffect(() => {
    if (!externalBoardId || !lists.items?.length) return;
    if (lists.items.some((list) => list.id === targetListId)) return;
    const fallback = pickDefaultList(lists.items);
    void settingsItem
      .getValue()
      .then((latest) => settingsItem.setValue({ ...latest, targetList: fallback.name, targetListId: fallback.id }));
  }, [externalBoardId, lists.items, targetListId]);

  // Jira sites for the site picker
  const jiraSites = useLoad(
    () => (apiUrl === undefined || !jiraConnected ? Promise.resolve([]) : listJiraSites({ apiUrl, apiToken: apiToken ?? '' })),
    [apiUrl, apiToken, jiraConnected],
  );

  // projects on the chosen site. Like the Trello lists, they remember which site they came from
  const jiraSiteId = settings?.jiraSite?.id;
  const projectsLoad = useLoad(async () => {
    if (apiUrl === undefined || !jiraConnected || !jiraSiteId) return undefined;
    const items = await listJiraProjects({ apiUrl, apiToken: apiToken ?? '' }, jiraSiteId);
    return { siteId: jiraSiteId, items };
  }, [apiUrl, apiToken, jiraConnected, jiraSiteId]);
  const jiraProjects = {
    items: projectsLoad.data?.siteId === jiraSiteId ? projectsLoad.data?.items : undefined,
    error: projectsLoad.error,
    loading: projectsLoad.loading,
    reload: projectsLoad.reload,
  };

  // issue types in the chosen project, for the import destination
  const jiraBoardId = boardProvider(selectedBoard) === 'jira' ? selectedBoard?.externalId : undefined;
  const issueTypesLoad = useLoad(async () => {
    const ids = jiraBoardId ? parseJiraBoardExternalId(jiraBoardId) : undefined;
    if (apiUrl === undefined || !jiraConnected || !ids) return undefined;
    const items = await listJiraIssueTypes({ apiUrl, apiToken: apiToken ?? '' }, ids.siteId, ids.projectId);
    return { boardId: jiraBoardId, items };
  }, [apiUrl, apiToken, jiraConnected, jiraBoardId]);
  const jiraIssueTypes = {
    items: issueTypesLoad.data?.boardId === jiraBoardId ? issueTypesLoad.data?.items : undefined,
    error: issueTypesLoad.error,
    loading: issueTypesLoad.loading,
    reload: issueTypesLoad.reload,
  };

  // fields the project requires for this issue type, which Settings asks the person to fill in.
  // Waits until the issue type is confirmed to belong to the project, so a stale one isn't requested.
  const jiraIssueTypeId = settings?.jiraIssueType?.id;
  const fieldsKey =
    jiraBoardId && jiraIssueTypeId && jiraIssueTypes.items?.some((type) => type.id === jiraIssueTypeId)
      ? jiraFieldContext(jiraBoardId, jiraIssueTypeId)
      : undefined;
  const fieldsLoad = useLoad(async () => {
    const ids = jiraBoardId ? parseJiraBoardExternalId(jiraBoardId) : undefined;
    if (apiUrl === undefined || !jiraConnected || !ids || !jiraIssueTypeId || !fieldsKey) return undefined;
    const items = await listJiraRequiredFields({ apiUrl, apiToken: apiToken ?? '' }, ids.siteId, ids.projectId, jiraIssueTypeId);
    return { key: fieldsKey, items };
  }, [apiUrl, apiToken, jiraConnected, fieldsKey]);
  const jiraFields = {
    items: fieldsLoad.data?.key === fieldsKey ? fieldsLoad.data?.items : undefined,
    error: fieldsLoad.error,
    loading: fieldsLoad.loading,
    reload: fieldsLoad.reload,
  };

  // a Jira account with a single site doesn't need to pick one
  const savedSite = settings?.jiraSite;
  useEffect(() => {
    const only = jiraSites.data?.length === 1 ? jiraSites.data[0] : undefined;
    if (!jiraConnected || savedSite || !only) return;
    void settingsItem.getValue().then((latest) => settingsItem.setValue({ ...latest, jiraSite: latest.jiraSite ?? only }));
  }, [jiraConnected, jiraSites.data, savedSite]);

  // imports need an issue type. If none is saved (or it's gone from the project), use Story, Task or the first one
  useEffect(() => {
    if (!jiraBoardId || !jiraIssueTypes.items?.length) return;
    if (jiraIssueTypes.items.some((type) => type.id === jiraIssueTypeId)) return;
    const fallback = pickDefaultIssueType(jiraIssueTypes.items);
    if (fallback) {
      void settingsItem.getValue().then((latest) => settingsItem.setValue({ ...latest, jiraIssueType: fallback }));
    }
  }, [jiraBoardId, jiraIssueTypes.items, jiraIssueTypeId]);

  // A board or project saved for one provider can't be used while the other is connected. This
  // catches settings saved before disconnecting cleared them. It waits for exactly one
  // connection, so a slow status check can't wipe a good choice.
  const connectedProvider = connected && !jiraConnected ? 'trello' : jiraConnected && !connected ? 'jira' : undefined;
  const savedProvider = selectedBoard ? boardProvider(selectedBoard) : undefined;
  useEffect(() => {
    if (connectedProvider && savedProvider && savedProvider !== connectedProvider) void forgetProvider(savedProvider);
  }, [connectedProvider, savedProvider]);

  if (!settings) return null;

  const board: BoardConnection = selectedBoard
    ? { connected: true, boardId: selectedBoard.id, externalBoardId: selectedBoard.externalId, boardName: selectedBoard.name }
    : { connected: false };

  function openStory(storyId: string) {
    setSelectedStoryId(storyId);
    setTab('drafts');
  }

  return (
    <main className="panel">
      <header className="app-header">
        <span className="app-logo" aria-hidden="true">
          <Icon name="logo" size={22} />
        </span>
        <div className="app-title">
          <h1>Backlogger</h1>
          <p className="hint">
            {board.connected ? `${boardNoun(selectedBoard)}: ${board.boardName}` : 'No board or project selected'}
          </p>
        </div>
      </header>

      <nav className="tabs" role="tablist">
        {TABS.map(({ id, label, icon }) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? 'tab active' : 'tab'}
            onClick={() => setTab(id)}
          >
            <Icon name={icon} />
            {label}
          </button>
        ))}
      </nav>

      <div className="tab-body">
        {tab === 'record' && (
          <RecordTab settings={settings} board={board} onReviewStories={() => setTab('drafts')} />
        )}
        {tab === 'drafts' && (
          <DraftsTab
            settings={settings}
            board={board}
            selectedStoryId={selectedStoryId}
            onSelectStory={setSelectedStoryId}
          />
        )}
        {tab === 'analytics' && <AnalyticsTab settings={settings} board={board} onOpenStory={openStory} />}
        {tab === 'settings' && (
          <SettingsTab
            settings={settings}
            connection={connection}
            boards={boards}
            lists={lists}
            models={models}
            jira={{
              connection: jiraConnection,
              sites: {
                items: jiraSites.data,
                error: jiraSites.error,
                loading: jiraSites.loading,
                reload: jiraSites.reload,
              },
              projects: jiraProjects,
              issueTypes: jiraIssueTypes,
              fields: jiraFields,
            }}
          />
        )}
      </div>
    </main>
  );
}

function pickDefaultList(lists: TrelloList[]): TrelloList {
  return lists.find((list) => list.name.trim().toLowerCase() === 'triage') ?? lists[0]!;
}
