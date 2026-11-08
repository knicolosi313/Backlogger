import { useEffect, useState } from 'react';

interface Watchable<T> {
  getValue(): Promise<T>;
  watch(cb: (newValue: T, oldValue: T) => void): () => void;
}

// Reads a storage item and re-renders when it changes, even if the background worker changed it.
export function useStorageItem<T>(item: Watchable<T>): T | undefined {
  const [value, setValue] = useState<T>();

  useEffect(() => {
    let active = true;
    item.getValue().then((v) => active && setValue(v));
    const unwatch = item.watch((v) => setValue(v));
    return () => {
      active = false;
      unwatch();
    };
  }, [item]);

  return value;
}
