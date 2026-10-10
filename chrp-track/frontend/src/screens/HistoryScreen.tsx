import { useState } from 'react';
import type { MeshTransmissionEvent } from '../api/types';
import { FileIcon } from '../components/FileIcon';
import { Icon } from '../components/Icon';
import { RetryButton } from '../components/RetryButton';
import { displayName, formatBytes, formatWhen } from '../lib/format';
import { paths } from '../lib/router';
import { useMesh } from '../state/MeshProvider';

type Filter = 'all' | 'sent' | 'received' | 'failed';

const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'sent', label: 'Sent' },
  { id: 'received', label: 'Received' },
  { id: 'failed', label: 'Failed' },
];

const EMPTY: Record<Filter, string> = {
  all: 'No transmissions yet. Anything you send or receive will show up here.',
  sent: 'Nothing sent yet.',
  received: 'Nothing received yet.',
  failed: 'No failed transmissions.',
};

function matches(tx: MeshTransmissionEvent, filter: Filter): boolean {
  if (filter === 'sent') return tx.direction === 'outgoing';
  if (filter === 'received') return tx.direction === 'incoming';
  if (filter === 'failed') return tx.status === 'failed';
  return true;
}

export function HistoryScreen() {
  const { history } = useMesh();
  const [filter, setFilter] = useState<Filter>('all');
  const items = history.filter((tx) => matches(tx, filter));

  return (
    <main className="screen">
      <h1 className="page-title">History</h1>
      <div className="segmented" role="group" aria-label="Filter history">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            className="segmented__btn"
            aria-pressed={filter === f.id}
            onClick={() => setFilter(f.id)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {items.length === 0 ? (
        <p className="empty">{EMPTY[filter]}</p>
      ) : (
        <ul className="card-list card-list--tight">
          {items.map((tx) => (
            <li key={tx.fileId}>
              <HistoryRow tx={tx} />
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}

function HistoryRow({ tx }: { tx: MeshTransmissionEvent }) {
  const incoming = tx.direction === 'incoming';
  const failed = tx.status === 'failed';
  const verb = failed ? (incoming ? 'Failed to receive' : 'Failed to send') : incoming ? 'Received' : 'Sent';
  const when = formatWhen(tx.completedAt ?? tx.createdAt);

  const summary = (
    <>
      <FileIcon tx={tx} size={40} outline={incoming} />
      <span className="history-row__body">
        <span className="history-row__name truncate">{displayName(tx)}</span>
        <span className="history-row__meta truncate">
          {failed ? <span className="text-danger">{verb}</span> : verb} · {when} · {formatBytes(tx.sizeBytes)}
        </span>
      </span>
    </>
  );

  // Failed outgoing: the row opens details, and Retry replaces the status badge.
  if (failed && !incoming) {
    return (
      <div className="card history-row">
        <a href={paths.details(tx.fileId)} className="history-row__link">{summary}</a>
        <RetryButton fileId={tx.fileId} />
      </div>
    );
  }

  return (
    <a href={paths.details(tx.fileId)} className="card history-row">
      {summary}
      {failed ? (
        <span className="badge badge--failed"><Icon name="x" size={12} strokeWidth={2.8} />Failed</span>
      ) : (
        <span className="badge badge--acked"><Icon name="check" size={12} strokeWidth={2.8} />Delivered</span>
      )}
      <Icon name="chevronRight" size={16} className="chev" />
    </a>
  );
}
