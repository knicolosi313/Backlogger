// Messages passed with browser.runtime.sendMessage. The audio itself never goes in a
// message: the offscreen page saves it to IndexedDB and the side panel uploads it.

export type PanelToBackground =
  | { type: 'START_RECORDING' }
  | { type: 'STOP_RECORDING' };

export type PageToBackground = { type: 'OPEN_BACKLOGGER' };

export interface LauncherReply {
  opened: boolean;
  error?: string;
}

export type BackgroundToOffscreen =
  | { type: 'OFFSCREEN_START'; streamId: string }
  | { type: 'OFFSCREEN_STOP' };

export type OffscreenToBackground =
  | { type: 'REC_READY'; durationSeconds: number; sizeBytes: number }
  | { type: 'REC_ERROR'; message: string };

export type AnyMessage = PanelToBackground | PageToBackground | BackgroundToOffscreen | OffscreenToBackground;
