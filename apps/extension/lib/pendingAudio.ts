// Audio waiting to be submitted. The offscreen page records it but the side panel uploads it,
// and messages can't carry a big Blob, so both use IndexedDB. It also survives closing the
// panel. Only one file is kept, since a meeting has one audio file.

const DB_NAME = 'backlogger-pending-audio';
const STORE = 'audio';
const KEY = 'current';

export interface PendingAudio {
  blob: Blob;
  fileName: string;
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error('Could not open local audio storage.'));
  });
}

async function withStore<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDatabase();
  try {
    return await new Promise<T>((resolve, reject) => {
      const transaction = db.transaction(STORE, mode);
      const request = run(transaction.objectStore(STORE));
      transaction.oncomplete = () => resolve(request.result);
      transaction.onerror = () => reject(transaction.error ?? new Error('Local audio storage failed.'));
      transaction.onabort = () => reject(transaction.error ?? new Error('Local audio storage failed.'));
    });
  } finally {
    db.close();
  }
}

export async function savePendingAudio(audio: PendingAudio): Promise<void> {
  await withStore('readwrite', (store) => store.put(audio, KEY));
}

export async function loadPendingAudio(): Promise<PendingAudio | undefined> {
  return withStore<PendingAudio | undefined>('readonly', (store) => store.get(KEY));
}

export async function clearPendingAudio(): Promise<void> {
  await withStore('readwrite', (store) => store.delete(KEY));
}
