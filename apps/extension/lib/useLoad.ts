import { useCallback, useEffect, useState } from 'react';

// Runs `load` on mount and again whenever `deps` change. The old data stays on screen
// while it reloads, so the UI doesn't flash.
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [reloadCount, setReloadCount] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);

    load()
      .then((result) => {
        if (!active) return;
        setData(result);
        setError(undefined);
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : 'Something went wrong.');
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
    // load is a new function every render, so only the deps matter
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, reloadCount]);

  const reload = useCallback(() => setReloadCount((n) => n + 1), []);

  return { data, setData, error, loading, reload };
}

// Calls reload on a timer while the panel is visible, so changes made elsewhere show up.
export function useAutoReload(reload: () => void, intervalMs: number, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => {
      if (document.visibilityState === 'visible') reload();
    }, intervalMs);
    return () => clearInterval(id);
  }, [reload, intervalMs, enabled]);
}

export type Loaded<T> = ReturnType<typeof useLoad<T>>;
