import { useState } from 'react';
import type { Priority, Story, StoryEdit } from '@/lib/types';

interface Props {
  story: Story;
  busy: boolean;
  onSave: (edit: StoryEdit) => void;
  onCancel: () => void;
}

export function StoryEditForm({ story, busy, onSave, onCancel }: Props) {
  const [title, setTitle] = useState(story.title);
  const [persona, setPersona] = useState(story.persona);
  const [goal, setGoal] = useState(story.goal);
  const [benefit, setBenefit] = useState(story.benefit);
  const [criteria, setCriteria] = useState(story.acceptanceCriteria.join('\n')); // one per line
  const [priority, setPriority] = useState<Priority>(story.priority);
  const [points, setPoints] = useState(story.points === null ? '' : String(story.points));

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    onSave({
      title: title.trim(),
      persona: persona.trim(),
      goal: goal.trim(),
      benefit: benefit.trim(),
      acceptanceCriteria: criteria
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean),
      priority,
      points: points === '' ? null : Number(points),
    });
  }

  return (
    <form className="card stack" onSubmit={handleSubmit}>
      <label className="field">
        Title
        <input value={title} onChange={(e) => setTitle(e.target.value)} required />
      </label>
      <label className="field">
        As a…
        <input value={persona} onChange={(e) => setPersona(e.target.value)} required />
      </label>
      <label className="field">
        I want…
        <input value={goal} onChange={(e) => setGoal(e.target.value)} required />
      </label>
      <label className="field">
        So that…
        <input value={benefit} onChange={(e) => setBenefit(e.target.value)} required />
      </label>
      <label className="field">
        Acceptance criteria (one per line)
        <textarea rows={5} value={criteria} onChange={(e) => setCriteria(e.target.value)} />
      </label>
      <div className="row">
        <label className="field">
          Priority
          <select value={priority} onChange={(e) => setPriority(e.target.value as Priority)}>
            <option value="high">High</option>
            <option value="med">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>
        <label className="field">
          Points
          <input type="number" min={0} value={points} onChange={(e) => setPoints(e.target.value)} />
        </label>
      </div>
      <div className="row">
        <button className="primary" type="submit" disabled={busy}>
          Save changes
        </button>
        <button type="button" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
