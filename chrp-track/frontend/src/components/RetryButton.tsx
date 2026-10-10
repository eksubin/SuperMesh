import { useState } from 'react';
import { errorMessage, retryTransmission } from '../api/client';
import { paths } from '../lib/router';
import { useMesh } from '../state/MeshProvider';
import { Icon } from './Icon';

interface RetryButtonProps {
  fileId: string;
  label?: string;
  large?: boolean;
}

export function RetryButton({ fileId, label = 'Retry', large = false }: RetryButtonProps) {
  const { upsert, showToast, go } = useMesh();
  const [busy, setBusy] = useState(false);

  async function onClick() {
    setBusy(true);
    try {
      upsert(await retryTransmission(fileId));
      showToast('Sending again. Track it on Main.');
      go(paths.main);
    } catch (err) {
      showToast(errorMessage(err, 'Couldn’t retry. Try again.'));
      setBusy(false);
    }
  }

  return (
    <button type="button" className={`btn-primary${large ? ' btn-primary--lg' : ''}`} onClick={onClick} disabled={busy}>
      <Icon name="retry" size={large ? 18 : 13} strokeWidth={2.4} />
      {busy ? 'Retrying…' : label}
    </button>
  );
}
