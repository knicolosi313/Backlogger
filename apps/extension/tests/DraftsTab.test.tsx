import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { decideStory, deleteStory, importStories, listStories } from '@/lib/api';
import type { Settings, Story, StoryStatus } from '@/lib/types';
import { DraftsTab } from '@/components/DraftsTab';

vi.mock('@/lib/api', () => ({
  listStories: vi.fn(),
  decideStory: vi.fn(),
  deleteStory: vi.fn(),
  importStories: vi.fn(),
  requestEdits: vi.fn(),
  updateStory: vi.fn(),
  getMeetingAudio: vi.fn(),
}));

const settings = {
  apiUrl: 'http://127.0.0.1:8000', apiToken: '', webAppUrl: 'http://localhost:5173',
  selectedBoard: { id: 'board-1', externalId: 'trello-board', name: 'Board', provider: 'trello' },
  targetList: 'Triage', targetListId: 'list-1',
} as Settings;
const board = { connected: true, boardId: 'board-1' };

function story(id: string, status: StoryStatus): Story {
  return {
    id, meetingId: 'meeting-1', status, title: `Story ${id}`, persona: 'member', goal: 'do a thing',
    benefit: 'it helps', acceptanceCriteria: ['It works'], priority: 'med', points: null, evidence: [],
    confidence: 0.9, qualityScore: 80, investChecks: [], critique: '', openQuestions: [], unsupportedAssumptions: [],
  };
}

let container: HTMLDivElement;
let root: Root;
let stories: Story[];

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  stories = [story('a', 'draft'), story('b', 'needs_changes'), story('c', 'accepted'), story('d', 'rejected')];
  vi.mocked(listStories).mockReset().mockImplementation(async () => stories);
  vi.mocked(decideStory).mockReset().mockImplementation(async (_cfg, id, decision) => {
    const found = stories.find((s) => s.id === id)!;
    found.status = decision === 'accept' ? 'accepted' : decision === 'reject' ? 'rejected' : 'draft';
    return found;
  });
  vi.mocked(deleteStory).mockReset().mockImplementation(async (_cfg, id) => {
    stories = stories.filter((s) => s.id !== id);
  });
  vi.mocked(importStories).mockReset().mockImplementation(async (_cfg, items) =>
    items.map((item) => ({ storyId: item.id, cardUrl: '', duplicate: false })),
  );
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function render(props: { selectedStoryId?: string | null; onSelectStory?: (id: string | null) => void } = {}) {
  await act(async () =>
    root.render(createElement(DraftsTab, {
      settings, board, selectedStoryId: props.selectedStoryId ?? null, onSelectStory: props.onSelectStory ?? vi.fn(),
    })),
  );
}

const button = (label: RegExp | string) =>
  [...container.querySelectorAll('button')].find((b) =>
    typeof label === 'string' ? b.textContent === label : label.test(b.textContent ?? ''),
  ) as HTMLButtonElement | undefined;

const checkbox = (title: string) => container.querySelector(`input[aria-label="Select ${title}"]`) as HTMLInputElement;
const click = (element: Element | null | undefined) => act(async () => (element as HTMLElement).click());
const filter = (label: string) => click(button(new RegExp(`^${label} \\(`)));
const bar = () => container.querySelector('.sticky-bar');

describe('picking several drafts', () => {
  it('shows nothing to act on until something is picked, then counts what each action applies to', async () => {
    await render();
    expect(bar()).toBeNull();

    await click(checkbox('Story a'));
    await click(checkbox('Story b'));
    expect(bar()?.textContent).toContain('2 selected');
    expect(button('Accept (2)')).toBeDefined();
    expect(button('Reject (2)')).toBeDefined();
    // neither pick is accepted yet, and there is nothing rejected to delete
    expect(button('Import (0)')?.disabled).toBe(true);
    expect(button(/^Delete/)).toBeUndefined();
  });

  it('select all picks every story in the current filter, and again clears them', async () => {
    await render();
    const all = container.querySelector('.split input[type="checkbox"]') as HTMLInputElement;
    await click(all);
    expect(checkbox('Story a').checked && checkbox('Story b').checked).toBe(true);
    expect(bar()?.textContent).toContain('2 selected');
    await click(all);
    expect(bar()).toBeNull();
  });

  it('accepts every picked draft with one click and reports it', async () => {
    await render();
    await click(checkbox('Story a'));
    await click(checkbox('Story b'));
    await click(button('Accept (2)'));

    expect(vi.mocked(decideStory).mock.calls.map(([, id, decision]) => [id, decision])).toEqual([
      ['a', 'accept'], ['b', 'accept'],
    ]);
    expect(container.textContent).toContain('Accepted 2.');
    // the list is read again and the picks are cleared
    expect(listStories).toHaveBeenCalledTimes(2);
    expect(bar()).toBeNull();
  });

  it('only imports the accepted stories among those picked', async () => {
    await render();
    await filter('Accepted');
    await click(checkbox('Story c'));
    await filter('Pending');
    await click(checkbox('Story a'));
    expect(bar()?.textContent).toContain('2 selected');
    expect(button('Import (1)')?.disabled).toBe(false);

    await click(button('Import (1)'));
    expect(importStories).toHaveBeenCalledTimes(1);
    expect(vi.mocked(importStories).mock.calls[0]![1].map((s) => s.id)).toEqual(['c']);
    expect(container.textContent).toContain('Imported 1 to "Triage".');
    expect(container.textContent).toContain('1 skipped: only accepted stories can be imported');
  });

  it('rejects drafts and accepted stories but leaves already rejected ones alone', async () => {
    await render();
    await click(container.querySelector('.split input[type="checkbox"]'));
    await filter('Accepted');
    await click(checkbox('Story c'));
    await filter('Rejected');
    await click(checkbox('Story d'));
    expect(bar()?.textContent).toContain('4 selected');

    await click(button('Reject (3)'));
    expect(vi.mocked(decideStory).mock.calls.map(([, id]) => id)).toEqual(['a', 'b', 'c']);
    expect(container.textContent).toContain('Rejected 3.');
    expect(container.textContent).toContain('1 skipped');
  });

  it('keeps going when one story fails and says how many did', async () => {
    vi.mocked(decideStory).mockImplementation(async (_cfg, id) => {
      if (id === 'b') throw new Error('Finish or reconcile the pending export first');
      return stories.find((s) => s.id === id)!;
    });
    await render();
    await click(checkbox('Story a'));
    await click(checkbox('Story b'));
    await click(button('Accept (2)'));
    expect(decideStory).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain('Accepted 1. 1 failed: Finish or reconcile the pending export first');
  });
});

describe('deleting rejected drafts', () => {
  it('offers delete only when a rejected draft is picked, and asks first', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await render();
    await filter('Rejected');
    await click(checkbox('Story d'));
    expect(button('Delete (1)')).toBeDefined();

    await click(button('Delete (1)'));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('stay in the database'));
    expect(deleteStory).not.toHaveBeenCalled();
    // declining keeps the pick, so nothing was lost
    expect(bar()?.textContent).toContain('1 selected');

    confirm.mockReturnValue(true);
    await click(button('Delete (1)'));
    expect(deleteStory).toHaveBeenCalledWith(settings, 'd');
    expect(container.textContent).toContain('Deleted 1.');
    expect(checkbox('Story d')).toBeNull();
  });

  it('never deletes a story that is not rejected, even if it is picked with one that is', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    await render();
    await click(checkbox('Story a'));
    await filter('Rejected');
    await click(checkbox('Story d'));
    await click(button('Delete (1)'));
    expect(vi.mocked(deleteStory).mock.calls.map(([, id]) => id)).toEqual(['d']);
    expect(container.textContent).toContain('1 skipped');
  });

  it('deletes a rejected story from its own page and returns to the list', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const onSelectStory = vi.fn();
    await render({ selectedStoryId: 'd', onSelectStory });
    await click(button('Delete'));
    expect(deleteStory).toHaveBeenCalledWith(settings, 'd');
    expect(onSelectStory).toHaveBeenCalledWith(null);
  });

  it('has no delete button on a story that is not rejected', async () => {
    await render({ selectedStoryId: 'a' });
    expect(button('Delete')).toBeUndefined();
  });

  it('keeps the story and says why when the server refuses', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    vi.mocked(deleteStory).mockRejectedValue(new Error('Finish or reconcile the pending export first'));
    const onSelectStory = vi.fn();
    await render({ selectedStoryId: 'd', onSelectStory });
    await click(button('Delete'));
    expect(container.textContent).toContain('Finish or reconcile the pending export first');
    expect(onSelectStory).not.toHaveBeenCalled();
  });
});
