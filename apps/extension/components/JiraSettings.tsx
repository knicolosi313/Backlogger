import { useState } from 'react';
import { registerBoard } from '@/lib/api';
import { openConnectTab } from '@/lib/connectTab';
import { formatDateTime, isLocalhost } from '@/lib/format';
import {
  buildAdditionalFields,
  disconnectJira,
  jiraBoardExternalId,
  jiraConnectUrl,
  jiraFieldContext,
  parseJiraBoardExternalId,
} from '@/lib/jira';
import { boardProvider } from '@/lib/provider';
import { Icon } from './Icon';
import { forgetProvider, settingsItem } from '@/lib/storage';
import type {
  JiraConnection,
  JiraField,
  JiraFieldValues,
  JiraIssueType,
  JiraProject,
  JiraSite,
  Settings,
} from '@/lib/types';
import type { Loaded } from '@/lib/useLoad';

// Same shape as the Trello pieces in SettingsTab, for Jira Cloud.

interface Choices<T> {
  items?: T[];
  error?: string;
  loading: boolean;
  reload: () => void;
}

export interface JiraState {
  connection: Loaded<JiraConnection | undefined>;
  sites: Choices<JiraSite>;
  projects: Choices<JiraProject>;
  issueTypes: Choices<JiraIssueType>;
  fields: Choices<JiraField>; // what the project requires for the chosen issue type
}

// merge onto the latest saved settings so a stale copy can't overwrite another change
async function updateSettings(change: Partial<Settings>) {
  const latest = await settingsItem.getValue();
  await settingsItem.setValue({ ...latest, ...change });
}

// `blocked` is set while Trello is connected. Only one connection works at a time, so Jira
// can't be connected until Trello is disconnected.
export function JiraConnect({
  settings,
  connection,
  blocked,
}: {
  settings: Settings;
  connection: Loaded<JiraConnection | undefined>;
  blocked: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const status = connection.data;

  async function handleDisconnect() {
    setBusy(true);
    setError('');
    try {
      await disconnectJira(settings);
      // the project and issue type belong to this account
      await forgetProvider('jira');
      connection.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not disconnect.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card stack">
      <h2 className="card-title">
        <Icon name="link" size={20} />
        Jira
      </h2>
      {!status && !connection.error && <p className="hint">Checking your Jira connection…</p>}

      {status?.connected && (
        <>
          <p className="status">
            Connected{status.displayName ? ` as ${status.displayName}` : ''}
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
          <p className="hint">Connect your Atlassian account to pick a Jira project and import stories as issues.</p>
          <div className="row">
            <button
              className="primary"
              disabled={blocked}
              onClick={() => void openConnectTab('jira', jiraConnectUrl(settings))}
            >
              Connect Jira
            </button>
          </div>
          {blocked ? (
            <p className="hint">Only one connection works at a time. Disconnect Trello first.</p>
          ) : (
            <p className="hint">Approve access in the tab that opens. It closes by itself once you're connected.</p>
          )}
          {isLocalhost(settings.apiUrl) && (
            <p className="note warn">
              The API address uses localhost, but the backend's Jira callback uses 127.0.0.1 unless you changed it. They
              need the same host or the connection won't save. Change it under Server connection below.
            </p>
          )}
        </>
      )}

      {connection.error && <p className="status error" role="alert">{connection.error}</p>}
      {error && <p className="status error" role="alert">{error}</p>}
    </section>
  );
}

// Site, project and issue type apply right away, there's no Save button for this part.
export function JiraPicker({ settings, jira }: { settings: Settings; jira: JiraState }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const { sites, projects, issueTypes } = jira;

  const site = settings.jiraSite;
  const board = settings.selectedBoard;
  const project = boardProvider(board) === 'jira' ? board : undefined;
  const projectId = (project && parseJiraBoardExternalId(project.externalId)?.projectId) ?? '';

  async function chooseSite(siteId: string) {
    if (siteId === site?.id) return;
    setError('');
    const chosen = sites.items?.find((s) => s.id === siteId);
    // a project belongs to one site, so changing it starts the choices over
    await updateSettings({
      jiraSite: chosen,
      jiraIssueType: undefined,
      ...(project ? { selectedBoard: undefined } : {}),
    });
  }

  async function chooseProject(id: string) {
    if (!site || id === projectId) return;
    setError('');

    if (!id) {
      await updateSettings({ selectedBoard: undefined, jiraIssueType: undefined });
      return;
    }
    const chosen = projects.items?.find((p) => p.id === id);
    if (!chosen) return;

    setBusy(true);
    try {
      // the backend has to know about the project before meetings can belong to it
      const registered = await registerBoard(settings, {
        provider: 'jira',
        externalId: jiraBoardExternalId(site.id, chosen.id),
        name: `${chosen.name} (${chosen.key})`,
      });
      // the old issue type belonged to the old project, App picks a new default
      await updateSettings({ selectedBoard: { ...registered, siteUrl: site.url }, jiraIssueType: undefined });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not use this project.');
    } finally {
      setBusy(false);
    }
  }

  async function chooseIssueType(id: string) {
    const type = issueTypes.items?.find((t) => t.id === id);
    if (type) await updateSettings({ jiraIssueType: type });
  }

  // keep the saved choices in the dropdowns even if Jira didn't return them this time
  const projectChoices = projects.items ?? [];
  const savedProjectMissing = project && !projectChoices.some((p) => p.id === projectId);
  const loadingSites = sites.loading && !sites.items;

  return (
    <section className="card stack">
      <h2 className="card-title">
        <Icon name="board" size={20} />
        Project
      </h2>
      <p className="hint">Pick the Jira project your meetings and imported stories belong to.</p>

      <label className="field">
        Site
        <select value={site?.id ?? ''} disabled={busy || loadingSites} onChange={(e) => void chooseSite(e.target.value)}>
          <option value="">{loadingSites ? 'Loading sites…' : 'Choose a site…'}</option>
          {site && !sites.items?.some((s) => s.id === site.id) && <option value={site.id}>{site.name}</option>}
          {sites.items?.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        {sites.items?.length === 0 && <span className="hint">This account has no Jira Cloud sites.</span>}
      </label>

      {site && (
        <label className="field">
          Project
          <select
            value={projectId}
            disabled={busy || (projects.loading && !projects.items)}
            onChange={(e) => void chooseProject(e.target.value)}
          >
            <option value="">{projects.loading && !projects.items ? 'Loading projects…' : 'Choose a project…'}</option>
            {savedProjectMissing && <option value={projectId}>{project.name}</option>}
            {projectChoices.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.key})
              </option>
            ))}
          </select>
          {projects.items?.length === 0 && <span className="hint">You can't create issues in any project on this site.</span>}
        </label>
      )}

      {project && (
        <label className="field">
          Import stories as
          <select
            value={settings.jiraIssueType?.id ?? ''}
            disabled={!issueTypes.items?.length}
            onChange={(e) => void chooseIssueType(e.target.value)}
          >
            {!settings.jiraIssueType && (
              <option value="">{issueTypes.loading ? 'Loading issue types…' : 'Choose an issue type…'}</option>
            )}
            {issueTypes.items?.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
          {issueTypes.items?.length === 0 && <span className="hint">This project has no issue types stories can use.</span>}
        </label>
      )}

      {project && settings.jiraIssueType && jira.fields.items && jira.fields.items.length > 0 && (
        <RequiredFields
          // start over when the project or issue type changes
          key={jiraFieldContext(project.externalId, settings.jiraIssueType.id)}
          settings={settings}
          context={jiraFieldContext(project.externalId, settings.jiraIssueType.id)}
          fields={jira.fields.items}
          accountId={jira.connection.data?.accountId}
        />
      )}

      <div className="row">
        <button
          type="button"
          disabled={sites.loading || projects.loading}
          onClick={() => {
            sites.reload();
            projects.reload();
            issueTypes.reload();
            jira.fields.reload();
          }}
        >
          {sites.loading || projects.loading ? 'Loading…' : 'Refresh projects'}
        </button>
      </div>

      {sites.error && <p className="status error" role="alert">{sites.error}</p>}
      {projects.error && <p className="status error" role="alert">{projects.error}</p>}
      {issueTypes.error && <p className="status error" role="alert">{issueTypes.error}</p>}
      {jira.fields.error && <p className="status error" role="alert">{jira.fields.error}</p>}
      {error && <p className="status error" role="alert">{error}</p>}
    </section>
  );
}

// Jira won't create an issue without these, so they're filled in once here and sent with every
// import. Typing is kept in the form and saved when you leave a field, so the box doesn't jump
// around while the saved copy catches up.
function RequiredFields({
  settings,
  context,
  fields,
  accountId,
}: {
  settings: Settings;
  context: string;
  fields: JiraField[];
  accountId?: string | null;
}) {
  const saved = settings.jiraFieldValues?.for === context ? settings.jiraFieldValues.values : {};
  const [values, setValues] = useState<JiraFieldValues>(saved);

  async function save(next: JiraFieldValues) {
    await updateSettings({ jiraFieldValues: { for: context, values: next } });
  }

  // typing only changes the form. Choices (and leaving a box) save it
  function set(field: JiraField, value: string | string[], saveNow: boolean) {
    const next = { ...values, [field.id]: value };
    setValues(next);
    if (saveNow) void save(next);
  }

  let problem = '';
  try {
    const { missing } = buildAdditionalFields(fields, values, accountId);
    if (missing.length) problem = `Still needed before importing: ${missing.join(', ')}.`;
  } catch (err) {
    problem = err instanceof Error ? err.message : 'One of these fields is not valid.';
  }

  return (
    <div className="stack">
      <h3>Required by this project</h3>
      <p className="hint">Jira won't create issues without these. They're sent with every story you import.</p>

      {fields.map((field) => (
        <FieldInput
          key={field.id}
          field={field}
          value={values[field.id] ?? ''}
          accountId={accountId}
          onChange={(value, saveNow) => set(field, value, saveNow)}
          onBlur={() => void save(values)}
        />
      ))}

      {problem && <p className="note warn">{problem}</p>}
    </div>
  );
}

function FieldInput({
  field,
  value,
  accountId,
  onChange,
  onBlur,
}: {
  field: JiraField;
  value: string | string[];
  accountId?: string | null;
  onChange: (value: string | string[], saveNow: boolean) => void;
  onBlur: () => void;
}) {
  const text = Array.isArray(value) ? value.join(', ') : value;

  if (field.kind === 'select' && field.multiple) {
    const chosen = Array.isArray(value) ? value : [];
    return (
      <fieldset className="field">
        {field.name}
        {field.options.map((option) => (
          <label key={option.id} className="checkbox">
            <input
              type="checkbox"
              checked={chosen.includes(option.id)}
              onChange={(e) =>
                onChange(e.target.checked ? [...chosen, option.id] : chosen.filter((id) => id !== option.id), true)
              }
            />
            {option.label}
          </label>
        ))}
      </fieldset>
    );
  }

  if (field.kind === 'select') {
    return (
      <label className="field">
        {field.name}
        <select value={text} onChange={(e) => onChange(e.target.value, true)}>
          <option value="">Choose…</option>
          {field.options.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
    );
  }

  const textareaKind = field.kind === 'richtext' || field.kind === 'json';
  const inputType = { number: 'number', date: 'date', datetime: 'datetime-local' }[field.kind as string] ?? 'text';
  const hint =
    field.kind === 'user'
      ? 'An Atlassian account ID. Leave it empty to use your own.'
      : field.kind === 'labels'
        ? 'Separate labels with commas.'
        : field.kind === 'json'
          ? "Backlogger doesn't know this field type. Enter the JSON value Jira expects."
          : undefined;

  return (
    <label className="field">
      {field.name}
      {textareaKind ? (
        <textarea rows={3} value={text} onChange={(e) => onChange(e.target.value, false)} onBlur={onBlur} />
      ) : (
        <input
          type={inputType}
          value={text}
          placeholder={field.kind === 'user' && accountId ? accountId : undefined}
          onChange={(e) => onChange(e.target.value, false)}
          onBlur={onBlur}
        />
      )}
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}
