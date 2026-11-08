import { useState } from 'react';
import { registerBoard } from '@/lib/api';
import { openConnectTab } from '@/lib/connectTab';
import { disconnectTrello, trelloConnectUrl } from '@/lib/trello';
import { formatDateTime, formatPercent, isLocalhost } from '@/lib/format';
import { CREATIVITY_OPTIONS, MODEL_OPTIONS } from '@/lib/options';
import { boardProvider } from '@/lib/provider';
import { DEFAULT_SETTINGS, forgetProvider, settingsItem } from '@/lib/storage';
import type { Creativity, Settings, TrelloBoard, TrelloConnection, TrelloList } from '@/lib/types';
import type { Loaded } from '@/lib/useLoad';
import { JiraConnect, JiraPicker, type JiraState } from './JiraSettings';

interface Props {
  settings: Settings;
  connection: Loaded<TrelloConnection | undefined>;
  boards: Loaded<TrelloBoard[]>;
  lists: { items?: TrelloList[]; error?: string; loading: boolean };
  jira: JiraState;
}

export function SettingsTab({ settings, connection, boards, lists, jira }: Props) {
  const trelloConnected = connection.data?.connected === true;
  const jiraConnected = jira.connection.data?.connected === true;

  return (
    <div className="stack">
      {trelloConnected && jiraConnected && (
        <p className="note warn" role="alert">
          Trello and Jira are both connected, but only one connection works at a time. Disconnect one of them.
        </p>
      )}

      <TrelloConnect settings={settings} connection={connection} blocked={jiraConnected} />
      <JiraConnect settings={settings} connection={jira.connection} blocked={trelloConnected} />

      {trelloConnected && !jiraConnected && <TrelloPicker settings={settings} boards={boards} lists={lists} />}
      {jiraConnected && !trelloConnected && <JiraPicker settings={settings} jira={jira} />}

      <SettingsForm settings={settings} />
    </div>
  );
}

// merge onto the latest saved settings so a stale copy can't overwrite another change
async function updateSettings(change: Partial<Settings>) {
  const latest = await settingsItem.getValue();
  await settingsItem.setValue({ ...latest, ...change });
}

// `blocked` is set while Jira is connected. Only one connection works at a time, so Trello
// can't be connected until Jira is disconnected.
function TrelloConnect({
  settings,
  connection,
  blocked,
}: {
  settings: Settings;
  connection: Loaded<TrelloConnection | undefined>;
  blocked: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const status = connection.data;

  async function handleDisconnect() {
    setBusy(true);
    setError('');
    try {
      await disconnectTrello(settings);
      // the board and list belong to this account
      await forgetProvider('trello');
      connection.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not disconnect.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card stack">
      <h2>Trello</h2>
      {!status && !connection.error && <p className="hint">Checking your Trello connection…</p>}

      {status?.connected && (
        <>
          <p className="status">
            Connected{status.username ? ` as ${status.username}` : ''}
            {status.expiresAt && <span className="hint"> · expires {formatDateTime(status.expiresAt)}</span>}
          </p>
          <div className="row">
            <button disabled={busy} onClick={handleDisconnect}>
              Disconnect
            </button>
          </div>
        </>
      )}

      {status && !status.connected && (
        <>
          <p className="hint">Connect your Trello account to pick a board and import stories.</p>
          <div className="row">
            <button
              className="primary"
              disabled={blocked}
              onClick={() => void openConnectTab('trello', trelloConnectUrl(settings))}
            >
              Connect Trello
            </button>
          </div>
          {blocked ? (
            <p className="hint">Only one connection works at a time. Disconnect Jira first.</p>
          ) : (
            <p className="hint">Approve access in the tab that opens. It closes by itself once you're connected.</p>
          )}
          {isLocalhost(settings.apiUrl) && (
            <p className="note warn">
              The API address uses localhost, but the backend's Trello callback uses 127.0.0.1 unless you changed
              it. They need the same host or the connection won't save. Change it under Server connection below.
            </p>
          )}
        </>
      )}

      {connection.error && <p className="status error" role="alert">{connection.error}</p>}
      {error && <p className="status error" role="alert">{error}</p>}
    </section>
  );
}

// Board and list apply right away, there's no Save button for this part.
function TrelloPicker({ settings, boards, lists }: Omit<Props, 'connection' | 'jira'>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const selected = settings.selectedBoard;

  async function chooseBoard(externalId: string) {
    if (externalId === selected?.externalId) return;
    setError('');

    if (!externalId) {
      await updateSettings({ selectedBoard: undefined, targetList: DEFAULT_SETTINGS.targetList, targetListId: undefined });
      return;
    }
    const trelloBoard = boards.data?.find((b) => b.id === externalId);
    if (!trelloBoard) return;

    setBusy(true);
    try {
      // the backend has to know about the board before meetings can belong to it
      const registered = await registerBoard(settings, {
        provider: 'trello',
        externalId: trelloBoard.id,
        name: trelloBoard.name,
      });
      // the old list belonged to the old board, App picks a new default
      await updateSettings({
        selectedBoard: registered,
        targetList: DEFAULT_SETTINGS.targetList,
        targetListId: undefined,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not use this board.');
    } finally {
      setBusy(false);
    }
  }

  async function chooseList(listId: string) {
    const list = lists.items?.find((l) => l.id === listId);
    if (list) await updateSettings({ targetList: list.name, targetListId: list.id });
  }

  // keep the saved board in the dropdown even if Trello didn't return it this time
  const boardChoices = boards.data ?? [];
  const savedIsMissing = selected && !boardChoices.some((b) => b.id === selected.externalId);

  return (
    <section className="card stack">
      <h2>Board</h2>
      <p className="hint">Pick the board your meetings and imported stories belong to.</p>

      <label className="field">
        Board
        <select
          value={selected?.externalId ?? ''}
          disabled={busy || (boards.loading && !boards.data)}
          onChange={(e) => void chooseBoard(e.target.value)}
        >
          <option value="">{boards.loading && !boards.data ? 'Loading boards…' : 'Choose a board…'}</option>
          {savedIsMissing && <option value={selected.externalId}>{selected.name}</option>}
          {boardChoices.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
      </label>

      {selected && (
        <label className="field">
          Import destination list
          <select
            value={settings.targetListId ?? ''}
            disabled={!lists.items?.length}
            onChange={(e) => void chooseList(e.target.value)}
          >
            {!settings.targetListId && <option value="">{lists.loading ? 'Loading lists…' : 'Choose a list…'}</option>}
            {lists.items?.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
          {lists.items?.length === 0 && <span className="hint">This board has no open lists.</span>}
        </label>
      )}

      <div className="row">
        <button type="button" disabled={boards.loading} onClick={boards.reload}>
          {boards.loading ? 'Loading…' : 'Refresh boards'}
        </button>
      </div>

      {boards.error && <p className="status error" role="alert">{boards.error}</p>}
      {lists.error && <p className="status error" role="alert">{lists.error}</p>}
      {error && <p className="status error" role="alert">{error}</p>}
    </section>
  );
}

// Everything else is saved with the Save button. Only its own fields are written, so it
// can't undo a board or list picked above after the form loaded.
function SettingsForm({ settings }: { settings: Settings }) {
  const [draft, setDraft] = useState(settings);
  const [saved, setSaved] = useState(false);

  function change<K extends keyof Settings>(key: K, value: Settings[K]) {
    setSaved(false);
    setDraft((current) => ({ ...current, [key]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const latest = await settingsItem.getValue();

    // the board is a record on one server, so a different server means picking it again
    const serverChanged = normalizeUrl(draft.apiUrl) !== normalizeUrl(latest.apiUrl);

    await settingsItem.setValue({
      ...latest,
      creativity: draft.creativity,
      model: draft.model,
      autoImportThreshold: draft.autoImportThreshold,
      consentReminder: draft.consentReminder,
      apiUrl: draft.apiUrl,
      apiToken: draft.apiToken,
      webAppUrl: draft.webAppUrl,
      ...(serverChanged
        ? {
            selectedBoard: undefined,
            targetList: DEFAULT_SETTINGS.targetList,
            targetListId: undefined,
            jiraIssueType: undefined,
            jiraFieldValues: undefined,
          }
        : {}),
    });
    setSaved(true);
  }

  const creativityHelp = CREATIVITY_OPTIONS.find((o) => o.value === draft.creativity)?.help;

  return (
    <form className="stack" onSubmit={handleSubmit}>
      <section className="card stack">
        <h2>AI</h2>
        <label className="field">
          Creativity
          <select value={draft.creativity} onChange={(e) => change('creativity', e.target.value as Creativity)}>
            {CREATIVITY_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <span className="hint">{creativityHelp} Creativity never means inventing facts.</span>
        </label>
        <label className="field">
          Model
          <select value={draft.model} onChange={(e) => change('model', e.target.value)}>
            {MODEL_OPTIONS.map((model) => (
              <option key={model}>{model}</option>
            ))}
          </select>
        </label>
      </section>

      <section className="card stack">
        <h2>Auto-import</h2>
        <label className="field">
          Minimum confidence: {formatPercent(draft.autoImportThreshold)}
          <input
            type="range"
            min={0.5}
            max={1}
            step={0.05}
            value={draft.autoImportThreshold}
            onChange={(e) => change('autoImportThreshold', Number(e.target.value))}
          />
        </label>
        <p className="hint">
          {boardProvider(settings.selectedBoard) === 'jira'
            ? `Imports are created in ${settings.selectedBoard?.name ?? 'your Jira project'} as ${settings.jiraIssueType?.name ?? 'an issue type you pick'}. Change that in the Jira section above.`
            : `Imports go to "${settings.targetList}". Change the list in the Trello section above.`}{' '}
          Turn auto-import on from the Record tab. It stays off until you do.
        </p>
      </section>

      <section className="card stack">
        <h2>Recording</h2>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={draft.consentReminder}
            onChange={(e) => change('consentReminder', e.target.checked)}
          />
          Ask me to confirm everyone consents before recording
        </label>
      </section>

      <details className="card stack">
        <summary>Server connection</summary>
        <label className="field">
          API address
          <input value={draft.apiUrl} onChange={(e) => change('apiUrl', e.target.value)} />
        </label>
        <label className="field">
          Access token
          <input type="password" value={draft.apiToken} onChange={(e) => change('apiToken', e.target.value)} />
        </label>
        <label className="field">
          Web app address
          <input value={draft.webAppUrl} onChange={(e) => change('webAppUrl', e.target.value)} />
        </label>
      </details>

      <button className="primary" type="submit">
        {saved ? 'Saved' : 'Save settings'}
      </button>
    </form>
  );
}

function normalizeUrl(url: string): string {
  return url.trim().replace(/\/+$/, '');
}
