import { useState } from 'react';
import { registerBoard } from '@/lib/api';
import { disconnectTrello, trelloConnectUrl } from '@/lib/trello';
import { formatDateTime, formatPercent } from '@/lib/format';
import { CREATIVITY_OPTIONS, MODEL_OPTIONS } from '@/lib/options';
import { DEFAULT_SETTINGS, settingsItem } from '@/lib/storage';
import type { Creativity, Settings, TrelloBoard, TrelloConnection, TrelloList } from '@/lib/types';
import type { Loaded } from '@/lib/useLoad';

interface Props {
  settings: Settings;
  connection: Loaded<TrelloConnection | undefined>;
  boards: Loaded<TrelloBoard[]>;
  lists: { items?: TrelloList[]; error?: string; loading: boolean };
}

export function SettingsTab({ settings, connection, boards, lists }: Props) {
  return (
    <div className="stack">
      <TrelloConnect settings={settings} connection={connection} />
      {connection.data?.connected && <TrelloPicker settings={settings} boards={boards} lists={lists} />}
      <SettingsForm settings={settings} />
    </div>
  );
}

// merge onto the latest saved settings so a stale copy can't overwrite another change
async function updateSettings(change: Partial<Settings>) {
  const latest = await settingsItem.getValue();
  await settingsItem.setValue({ ...latest, ...change });
}

function TrelloConnect({ settings, connection }: { settings: Settings; connection: Loaded<TrelloConnection | undefined> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const status = connection.data;

  async function handleDisconnect() {
    setBusy(true);
    setError('');
    try {
      await disconnectTrello(settings);
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
            <button className="primary" onClick={() => browser.tabs.create({ url: trelloConnectUrl(settings) })}>
              Connect Trello
            </button>
          </div>
          <p className="hint">Approve access in the tab that opens. This panel updates when you're done.</p>
          {hostOf(settings.apiUrl) === 'localhost' && (
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
function TrelloPicker({ settings, boards, lists }: Omit<Props, 'connection'>) {
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
      const registered = await registerBoard(settings, trelloBoard);
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
        ? { selectedBoard: undefined, targetList: DEFAULT_SETTINGS.targetList, targetListId: undefined }
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
          Imports go to "{settings.targetList}". Change the list in the Trello section above. Turn auto-import on from
          the Record tab. It stays off until you do.
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

function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return '';
  }
}

function normalizeUrl(url: string): string {
  return url.trim().replace(/\/+$/, '');
}
