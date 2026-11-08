import { scenario } from './demo-runtime';
import React from 'react';
import { createRoot } from 'react-dom/client';
import App from '../../apps/extension/entrypoints/sidepanel/App';
import '../../apps/extension/entrypoints/sidepanel/style.css';
import './screenshot.css';

const copy: Record<string, [string, string, string]> = {
  record: ['Start with a conversation.', 'Record meeting audio or upload a file, then turn the discussion into draft stories.', '01 / MEETING CAPTURE'],
  drafts: ['Your backlog, ready for review.', 'Review confidence and quality scores. Accept, revise, or reject each draft before import.', '02 / DRAFT STORIES'],
  detail: ['Keep the evidence close.', 'Check acceptance criteria and jump back to the moment a requirement was discussed.', '03 / STORY REVIEW'],
  analytics: ['See what needs attention.', 'Explore story quality, review outcomes, and INVEST issues for the selected board.', '04 / BOARD ANALYTICS'],
  settings: ['Make it your workflow.', 'Choose your destination, story creativity, and optional automatic import settings.', '05 / CONNECTIONS & SETTINGS'],
};
const [title, description, eyebrow] = copy[scenario] ?? copy.record;
createRoot(document.getElementById('capture-root')!).render(
  <div className="capture-layout">
    <aside className="capture-copy">
      <div className="capture-brand"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round"><path d="M12 3 3 7.5 12 12l9-4.5L12 3Z M3 12l9 4.5 9-4.5 M3 16.5l9 4.5 9-4.5" /></svg>Backlogger</div>
      <div className="capture-eyebrow">{eyebrow}</div>
      <h1>{title}</h1>
      <p>{description}</p>
      <div className="capture-footer">Chrome extension · Demonstration data</div>
    </aside>
    <section className="capture-extension" aria-label="Actual Backlogger extension interface"><App /></section>
  </div>,
);

// Navigate with the real UI controls; no production components or styles are replaced.
const tab = scenario === 'detail' ? 'Drafts' : scenario[0].toUpperCase() + scenario.slice(1);
const timer = setInterval(() => {
  const button = [...document.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find((item) => item.textContent?.trim() === tab);
  if (!button) return;
  if (button.getAttribute('aria-selected') !== 'true') { button.click(); return; }
  if (scenario === 'detail') {
    const story = [...document.querySelectorAll<HTMLButtonElement>('button.story-row')].find((item) => item.textContent?.includes('Email notifications'));
    if (story) { story.click(); clearInterval(timer); }
  } else clearInterval(timer);
}, 100);
