import type { MeshTransmissionEvent } from '../api/types';
import { Donut } from '../components/Donut';
import { FileIcon } from '../components/FileIcon';
import { Icon } from '../components/Icon';
import { CodecPicker } from '../components/CodecPicker';
import { MessageBar } from '../components/MessageBar';
import { NodePicker } from '../components/NodePicker';
import { UploadButton } from '../components/UploadButton';
import { VoiceRecorder } from '../components/VoiceRecorder';
import { displayName, formatBytes, formatEtr, inFlightPercent, typeLabel } from '../lib/format';
import { useMesh } from '../state/MeshProvider';

export function MainScreen() {
  const { active, totalQueueEtrSeconds, mesh } = useMesh();

  return (
    <main className="screen">
      <h1 className="brand">CHRP Track</h1>
      {mesh && !mesh.mock && mesh.status === 'booting' && (
        <p className="notice" role="status">The mesh simulator is starting (about 30 seconds). Anything you send waits until it is ready.</p>
      )}
      {mesh && mesh.status === 'error' && (
        <p className="notice notice--danger" role="alert">The mesh simulator failed to start: {mesh.error}</p>
      )}
      <NodePicker />
      <CodecPicker />
      <UploadButton />
      <MessageBar />
      <VoiceRecorder />

      <section className="section" aria-labelledby="sending-title">
        <div className="section__head">
          <h2 id="sending-title" className="section__title">Sending now</h2>
          {active.length > 0 && (
            <span className="queue-pill">
              <Icon name="clock" size={13} strokeWidth={2.2} />
              {totalQueueEtrSeconds == null ? 'Estimating…' : `${formatEtr(totalQueueEtrSeconds)} left`}
              {' · '}
              {active.length} in queue
            </span>
          )}
        </div>

        {active.length === 0 ? (
          <p className="empty">Nothing sending right now. Files and messages stay here until they’re delivered.</p>
        ) : (
          <ul className="card-list">
            {active.map((tx) => (
              <li key={tx.fileId}>
                <ActiveCard tx={tx} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}

function ActiveCard({ tx }: { tx: MeshTransmissionEvent }) {
  const preparing = tx.status === 'queued' || tx.status === 'compressing';
  const pct = inFlightPercent(tx);
  const name = displayName(tx);
  const label = preparing
    ? `${name}: ${tx.status === 'compressing' ? 'compressing' : 'queued'}`
    : `${name}: ${pct}% acknowledged, ${formatEtr(tx.etrSeconds)} remaining`;

  return (
    <div className="card active-card">
      <Donut size={60} stroke={6} percent={preparing ? 0 : pct} label={label}>
        {preparing ? (
          <span className="donut__pct donut__pct--muted">…</span>
        ) : (
          <>
            <span className="donut__pct">{pct}%</span>
            <span className="donut__etr">{formatEtr(tx.etrSeconds, true)}</span>
          </>
        )}
      </Donut>
      <div className="active-card__body">
        <div className="active-card__title">
          <FileIcon tx={tx} size={24} />
          <span className="truncate">{name}</span>
        </div>
        <span className="active-card__meta">
          {preparing
            ? tx.status === 'compressing' ? 'Compressing…' : 'Queued'
            : `${typeLabel(tx)} · ${formatBytes(tx.sizeBytes)}`}
        </span>
      </div>
    </div>
  );
}
