// 75 -> "01:15"
export function formatClock(totalSeconds: number): string {
  const total = Math.max(0, Math.floor(totalSeconds));
  const minutes = String(Math.floor(total / 60)).padStart(2, '0');
  const seconds = String(total % 60).padStart(2, '0');
  return `${minutes}:${seconds}`;
}

// 0.847 -> "85%"
export function formatPercent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

// -> "Nov 6"
export function formatShortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

// 6291456 -> "6.0 MB"
export function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// The backend's OAuth callbacks use 127.0.0.1, so a localhost API address loses the session cookie
export function isLocalhost(url: string): boolean {
  try {
    return new URL(url).hostname === 'localhost';
  } catch {
    return false;
  }
}

// -> "Nov 6, 3:00 PM"
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}
