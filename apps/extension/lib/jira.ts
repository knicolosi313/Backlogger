import { request, type ApiConfig } from './http';
import type {
  JiraConnection,
  JiraField,
  JiraFieldKind,
  JiraFieldValues,
  JiraIssueType,
  JiraProject,
  JiraSite,
} from './types';

// Asks our backend about Jira Cloud. The backend holds the Atlassian tokens for this browser,
// so the extension never sees them. IDs here are Jira's own, not our database ids.

const BASE = '/api/integrations/jira';

// Jira lists come in pages of 50. This is a stop in case a page never reports being the last.
const MAX_PAGES = 20;

interface ConnectionWire {
  connected: boolean;
  account_id: string | null;
  display_name: string | null;
  expires_at: string | null;
}

export async function getJiraConnection(cfg: ApiConfig): Promise<JiraConnection> {
  const wire = await request<ConnectionWire>(cfg, `${BASE}/connection`);
  return {
    connected: wire.connected,
    accountId: wire.account_id,
    displayName: wire.display_name,
    expiresAt: wire.expires_at,
  };
}

// Open this in a tab. It sends the user to Atlassian, and the backend's callback page saves the tokens.
export function jiraConnectUrl(cfg: ApiConfig): string {
  return `${cfg.apiUrl.replace(/\/+$/, '')}${BASE}/connect`;
}

export async function disconnectJira(cfg: ApiConfig): Promise<void> {
  await request(cfg, `${BASE}/connection`, { method: 'DELETE' });
}

export function listJiraSites(cfg: ApiConfig): Promise<JiraSite[]> {
  return request<JiraSite[]>(cfg, `${BASE}/sites`);
}

interface ProjectPage {
  values: JiraProject[];
  isLast: boolean;
}

// Projects the account can create issues in
export async function listJiraProjects(cfg: ApiConfig, siteId: string): Promise<JiraProject[]> {
  const path = `${BASE}/sites/${encodeURIComponent(siteId)}/projects`;
  const projects: JiraProject[] = [];
  for (let page = 0; page < MAX_PAGES; page++) {
    const result = await request<ProjectPage>(cfg, `${path}?start_at=${projects.length}`);
    projects.push(...result.values);
    if (result.isLast || result.values.length === 0) break;
  }
  return projects;
}

interface IssueTypePage {
  issueTypes: Array<JiraIssueType & { subtask: boolean }>;
  maxResults: number;
  total: number | null;
}

// Issue types a story can be created as. Sub-tasks are left out because Jira wants a parent issue for them.
export async function listJiraIssueTypes(cfg: ApiConfig, siteId: string, projectId: string): Promise<JiraIssueType[]> {
  const path = `${BASE}/sites/${encodeURIComponent(siteId)}/projects/${encodeURIComponent(projectId)}/issue-types`;
  const all: IssueTypePage['issueTypes'] = [];
  for (let page = 0; page < MAX_PAGES; page++) {
    const result = await request<IssueTypePage>(cfg, `${path}?start_at=${all.length}`);
    all.push(...result.issueTypes);
    const more = result.total === null ? result.issueTypes.length >= result.maxResults : all.length < result.total;
    if (!more || result.issueTypes.length === 0) break;
  }
  return all.filter((type) => !type.subtask).map(({ id, name }) => ({ id, name }));
}

// Our backend identifies a Jira project as "siteId:projectId"
export function jiraBoardExternalId(siteId: string, projectId: string): string {
  return `${siteId}:${projectId}`;
}

export function parseJiraBoardExternalId(externalId: string): { siteId: string; projectId: string } | undefined {
  const [siteId, projectId, ...rest] = externalId.split(':');
  return siteId && projectId && rest.length === 0 ? { siteId, projectId } : undefined;
}

// Story is the closest match to what we create, then Task. Otherwise the first one.
export function pickDefaultIssueType(types: JiraIssueType[]): JiraIssueType | undefined {
  const byName = (name: string) => types.find((type) => type.name.trim().toLowerCase() === name);
  return byName('story') ?? byName('task') ?? types[0];
}

// ---- required fields ----
//
// Jira projects can require fields we don't know about (a custom field, the reporter...). The
// backend refuses to create the issue until they're sent as `additional_fields`, so we ask
// for them in Settings. The metadata Jira gives us says what each field is.

// The fields the backend fills in itself, and won't accept from us
const CORE_FIELDS = new Set(['project', 'issuetype', 'summary', 'description', 'labels']);

interface FieldWire {
  fieldId?: string;
  key?: string;
  name?: string;
  required?: boolean;
  hasDefaultValue?: boolean;
  schema?: { type?: string; items?: string; custom?: string; system?: string };
  allowedValues?: Array<{ id?: string | number; name?: string; value?: string }>;
}

interface FieldPage {
  fields: FieldWire[];
  total?: number;
}

function describeField(wire: FieldWire, id: string): JiraField {
  const { type, items, custom = '', system = '' } = wire.schema ?? {};
  const options = (wire.allowedValues ?? [])
    .filter((value) => value.id !== undefined)
    .map((value) => ({ id: String(value.id), label: value.name ?? value.value ?? String(value.id) }));

  let kind: JiraFieldKind = 'json';
  if (options.length > 0) kind = 'select';
  else if (type === 'user') kind = 'user';
  else if (type === 'number') kind = 'number';
  else if (type === 'date') kind = 'date';
  else if (type === 'datetime') kind = 'datetime';
  // Jira's API v3 wants multi-line text as a document, not a plain string
  else if (type === 'string') kind = custom.endsWith(':textarea') || system === 'environment' ? 'richtext' : 'text';
  else if (type === 'array' && items === 'string') kind = 'labels';

  return { id, name: wire.name ?? id, kind, multiple: kind === 'select' && type === 'array', options };
}

// The fields a person has to fill in for this project and issue type. Same test as the backend's:
// required, with no default, and not something we set ourselves.
export async function listJiraRequiredFields(
  cfg: ApiConfig,
  siteId: string,
  projectId: string,
  issueTypeId: string,
): Promise<JiraField[]> {
  const base = `${BASE}/sites/${encodeURIComponent(siteId)}/projects/${encodeURIComponent(projectId)}`;
  const path = `${base}/issue-types/${encodeURIComponent(issueTypeId)}/fields`;
  const wires: FieldWire[] = [];
  for (let page = 0; page < MAX_PAGES; page++) {
    const result = await request<FieldPage>(cfg, `${path}?start_at=${wires.length}`);
    wires.push(...result.fields);
    if (result.fields.length === 0 || wires.length >= (result.total ?? 0)) break;
  }

  const needed: JiraField[] = [];
  for (const wire of wires) {
    const id = wire.fieldId ?? wire.key;
    if (!id || CORE_FIELDS.has(id) || !wire.required || wire.hasDefaultValue) continue;
    needed.push(describeField(wire, id));
  }
  return needed;
}

// Values are saved for one project and issue type. Another one starts blank.
export function jiraFieldContext(boardExternalId: string, issueTypeId: string): string {
  return `${boardExternalId}/${issueTypeId}`;
}

function isBlank(value: string | string[] | undefined): boolean {
  return value === undefined || (Array.isArray(value) ? value.length === 0 : value.trim() === '');
}

// Turns what was typed into the shape Jira wants for the field
function toJiraValue(field: JiraField, raw: string | string[]): unknown {
  const text = (Array.isArray(raw) ? raw.join(',') : raw).trim();
  switch (field.kind) {
    case 'text':
    case 'date':
      return text;
    case 'richtext':
      return { type: 'doc', version: 1, content: [{ type: 'paragraph', content: [{ type: 'text', text }] }] };
    case 'number': {
      const number = Number(text);
      if (!Number.isFinite(number)) throw new Error(`${field.name} must be a number.`);
      return number;
    }
    case 'datetime': {
      const date = new Date(text);
      if (Number.isNaN(date.getTime())) throw new Error(`${field.name} must be a date and time.`);
      return date.toISOString().replace('Z', '+0000'); // the form Jira accepts
    }
    case 'select': {
      const ids = Array.isArray(raw) ? raw : [raw];
      return field.multiple ? ids.map((id) => ({ id })) : { id: ids[0] };
    }
    case 'user':
      return { accountId: text };
    case 'labels':
      return text.split(',').map((label) => label.trim()).filter(Boolean);
    case 'json':
      try {
        return JSON.parse(text);
      } catch {
        throw new Error(`${field.name} must be valid JSON.`);
      }
  }
}

// What goes in `additional_fields`, plus the names of required fields still empty.
// A user field with nothing typed means the person themselves. That also covers a required
// reporter, which is the only one most projects ask for.
export function buildAdditionalFields(
  fields: JiraField[],
  values: JiraFieldValues,
  accountId?: string | null,
): { fields: Record<string, unknown>; missing: string[] } {
  const built: Record<string, unknown> = {};
  const missing: string[] = [];
  for (const field of fields) {
    let raw = values[field.id];
    if (isBlank(raw) && field.kind === 'user' && accountId) raw = accountId;
    if (raw === undefined || isBlank(raw)) {
      missing.push(field.name);
      continue;
    }
    built[field.id] = toJiraValue(field, raw);
  }
  return { fields: built, missing };
}
