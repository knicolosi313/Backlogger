import { defineConfig } from 'wxt';

export default defineConfig({
  modules: ['@wxt-dev/module-react'],
  manifest: {
    name: 'Meeting-to-Backlog',
    description: 'Record a meeting, review AI-drafted user stories, and import the good ones into Trello.',
    permissions: [
      'tabCapture', // meeting tab audio
      'offscreen', // hidden page that runs MediaRecorder
      'storage',
      'sidePanel',
      'activeTab', // granted when the toolbar icon is clicked
    ],
    // the API server. Tighten once there's a real URL
    host_permissions: ['http://localhost/*', 'http://127.0.0.1/*'],
    action: { default_title: 'Open Meeting-to-Backlog' },
  },
});
