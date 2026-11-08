import { defineConfig } from 'wxt';

export default defineConfig({
  modules: ['@wxt-dev/module-react'],
  manifest: {
    name: 'Backlogger',
    icons: {
      16: '/icon/16.png',
      32: '/icon/32.png',
      48: '/icon/48.png',
      128: '/icon/128.png',
    },
    description: 'Record a meeting, review AI-drafted user stories, and import the good ones into Trello or Jira.',
    permissions: [
      'tabCapture', // meeting tab audio
      'offscreen', // hidden page that runs MediaRecorder
      'storage',
      'sidePanel',
      'activeTab', // granted when the toolbar icon is clicked
    ],
    host_permissions: [
      'https://trello.com/*',
      'https://www.trello.com/*',
      'https://*.atlassian.net/*',
      'https://backlogger-production.up.railway.app/*',
      'http://localhost/*',
      'http://127.0.0.1/*',
    ],
    action: { default_title: 'Open Backlogger' },
  },
});
