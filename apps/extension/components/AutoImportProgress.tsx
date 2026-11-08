import { useState } from 'react';
import { getAutoImportStatus, retryAutoImport, type ApiConfig } from '@/lib/api';
import { useAutoReload, useLoad } from '@/lib/useLoad';

export function AutoImportProgress({ settings, meetingId }: { settings: ApiConfig; meetingId: string }) {
  const progress = useLoad(() => getAutoImportStatus(settings, meetingId), [settings.apiUrl, settings.apiToken, meetingId]);
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState('');
  const run = progress.data;
  useAutoReload(progress.reload, 2000, run?.status === 'pending' || run?.status === 'running');
  if (run === null && !progress.error) return null;
  if (!run && !progress.error) return <p className="hint">Checking auto-import…</p>;
  const stale = run?.status === 'running' && Date.now() - Date.parse(run.updated_at) > 15 * 60 * 1000;

  async function retry() {
    setRetrying(true);
    setError('');
    try {
      progress.setData(await retryAutoImport(settings, meetingId));
      progress.reload();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Could not retry auto-import.');
    } finally {
      setRetrying(false);
    }
  }

  return (
    <section className="stack" aria-label="Auto-import progress">
      <strong>Auto-import</strong>
      {run && <>
        <p role="status">{run.imported} imported · {run.skipped} left for review · {run.failed} failed</p>
        {(run.status === 'pending' || run.status === 'running') && <p className="hint">Importing eligible stories on the server. You can close the panel.</p>}
        {run.status === 'completed' && run.imported === 0 && <p className="hint">No stories met all auto-import checks. Review the drafts manually.</p>}
        {run.error && <p className="status error" role="alert">{run.error}</p>}
        {(run.status === 'failed' || run.status === 'pending' || stale) && <button disabled={retrying} onClick={() => void retry()}>{retrying ? 'Retrying…' : 'Retry auto-import'}</button>}
      </>}
      {(error || progress.error) && <p className="status error" role="alert">{error || progress.error}</p>}
      {progress.error && <button onClick={progress.reload}>Refresh import status</button>}
    </section>
  );
}
