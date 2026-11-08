import { useEffect, useState } from 'react';
import { AnalyticsTab } from '@/components/AnalyticsTab';
import { DraftsTab } from '@/components/DraftsTab';
import { RecordTab } from '@/components/RecordTab';
import { SettingsTab } from '@/components/SettingsTab';
import { settingsItem } from '@/lib/storage';
import { getTrelloConnection, listTrelloBoards, listTrelloLists } from '@/lib/trello';
import type { BoardConnection, TrelloList } from '@/lib/types';
import { useAutoReload, useLoad } from '@/lib/useLoad';
import { useStorageItem } from '@/lib/useStorageItem';

const TABS = [
  { id: 'record', label: 'Record' },
  { id: 'drafts', label: 'Drafts' },
  { id: 'analytics', label: 'Analytics' },
  { id: 'settings', label: 'Settings' },
] as const;

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
  // after clicking Connect the user finishes in another tab, so keep checking until it works
  useAutoReload(connection.reload, 3000, !connected);

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
      <header>
        <h1>Meeting-to-Backlog</h1>
        <p className="hint">{board.connected ? `Board: ${board.boardName}` : 'No board selected'}</p>
      </header>

      <nav className="tabs" role="tablist">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? 'tab active' : 'tab'}
            onClick={() => setTab(id)}
          >
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
        {tab === 'settings' && <SettingsTab settings={settings} connection={connection} boards={boards} lists={lists} />}
      </div>
    </main>
  );
}

function pickDefaultList(lists: TrelloList[]): TrelloList {
  return lists.find((list) => list.name.trim().toLowerCase() === 'triage') ?? lists[0]!;
}
