import { useEffect, useState } from 'react';
import { fetchTransmission } from '../api/client';
import { isActive, type HopState, type HopStat, type MeshTransmissionEvent } from '../api/types';
import { ContentPreview } from '../components/ContentPreview';
import { Donut } from '../components/Donut';
import { FileIcon } from '../components/FileIcon';
import { Icon } from '../components/Icon';
import { RetryButton } from '../components/RetryButton';
import { displayName, formatBytes, formatEtr, formatLoss, formatWhen, inFlightPercent, typeLabel } from '../lib/format';
import { paths } from '../lib/router';
import { useMesh } from '../state/MeshProvider';

export function DetailsScreen({ id }: { id: string }) {
  const { byId, upsert } = useMesh();
  const tx = byId[id];
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    if (tx) return;
    let cancelled = false;
    fetchTransmission(id)
      .then((record) => !cancelled && upsert(record))
      .catch(() => !cancelled && setMissing(true));
    return () => {
      cancelled = true;
    };
  }, [id, tx, upsert]);

  return (
    <main className="screen">
      <div className="details-header">
        <a href={paths.history} className="back-link">
          <Icon name="chevronLeft" size={20} strokeWidth={2.2} />
          History
        </a>
        <h1 className="details-title">Transmission details</h1>
      </div>

      {!tx ? (
        <p className="empty">{missing ? 'This transmission couldn’t be found.' : 'Loading…'}</p>
      ) : (
        <>
          <Summary tx={tx} />
          <ContentPreview tx={tx} />
          <RouteSection tx={tx} />
          {tx.status === 'failed' && tx.direction === 'outgoing' && (
            <div className="details-actions">
              <RetryButton fileId={tx.fileId} label="Retry transmission" large />
            </div>
          )}
        </>
      )}
    </main>
  );
}

function Summary({ tx }: { tx: MeshTransmissionEvent }) {
  const incoming = tx.direction === 'incoming';
  const delivered = tx.status === 'delivered';
  const failed = tx.status === 'failed';
  const ackPct = tx.packetsTotal ? Math.round((tx.packetsAcked / tx.packetsTotal) * 100) : null;

  return (
    <section className="card details-summary">
      <div className="details-file">
        <FileIcon tx={tx} size={44} outline={incoming} />
        <div className="details-file__text">
          <span className="details-file__name">{displayName(tx)}</span>
          <span className="details-file__meta">
            {formatBytes(tx.sizeBytes)} · {typeLabel(tx)} · ID #{tx.fileId}
          </span>
        </div>
      </div>
      <div className="divider" />
      <div className="details-stats">
        <Donut
          size={116}
          stroke={10}
          className="donut--lg"
          percent={delivered ? 100 : failed ? tx.progressPercentage : inFlightPercent(tx)}
          color={failed ? 'var(--color-danger-ring)' : undefined}
          track={failed ? 'var(--color-danger-bg)' : undefined}
          label={delivered ? 'Delivered' : failed ? `Failed at ${Math.floor(tx.progressPercentage)}%` : `${inFlightPercent(tx)}% acknowledged`}
        >
          {delivered ? (
            <span className="donut__state donut__state--ok"><Icon name="check" size={28} strokeWidth={2.6} />Delivered</span>
          ) : failed ? (
            <span className="donut__state donut__state--danger"><Icon name="x" size={26} strokeWidth={2.6} />Failed</span>
          ) : (
            <>
              <span className="donut__pct">{inFlightPercent(tx)}%</span>
              <span className="donut__etr">{formatEtr(tx.etrSeconds)} left</span>
            </>
          )}
        </Donut>
        <dl className="facts">
          <Fact label={incoming ? 'Received' : 'Sent'} value={formatWhen(incoming ? tx.completedAt ?? tx.createdAt : tx.createdAt)} />
          <Fact
            label="Packets"
            value={tx.packetsTotal != null ? `${tx.packetsAcked} of ${tx.packetsTotal} · ACK ${ackPct}%` : '—'}
          />
          {tx.fromNode != null && tx.toNode != null && (
            <Fact label="Nodes" value={`Node ${tx.fromNode} → Node ${tx.toNode}`} />
          )}
          <Fact label="Preset" value={tx.preset ?? '—'} />
          <Fact label="Chunk size" value={`~${tx.chunkSizeBytes} bytes`} />
          {failed && tx.failureReason && <Fact label="Reason" value={tx.failureReason} danger />}
        </dl>
      </div>
      {isActive(tx) && (
        <p className="notice">Still in transit. The route is shown once the transmission finishes.</p>
      )}
    </section>
  );
}

function Fact({ label, value, danger = false }: { label: string; value: string; danger?: boolean }) {
  return (
    <div className={`fact${danger ? ' fact--danger' : ''}`}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

/** Pad or trim hop stats so there is exactly one per link. */
function hopStatsFor(hops: string[], stats: HopStat[]): HopStat[] {
  return hops.slice(0, -1).map((from, i) => stats[i] ?? { from, to: hops[i + 1], state: 'pending', packetLossRate: null, rssiDbm: null });
}

function shortCode(name: string): string {
  const digits = name.match(/(\d+)\s*$/);
  if (digits) return digits[1].padStart(2, '0').slice(-3);
  return name.replace(/[^A-Za-z]/g, '').slice(0, 2).toUpperCase() || '?';
}

function hopBadge(state: HopState, loss: number | null): string {
  switch (state) {
    case 'acked':
      return loss != null ? `ACK received · ${formatLoss(loss)} loss` : 'ACK received';
    case 'failed':
      return 'No ACK · failed';
    case 'active':
      return 'In progress';
    default:
      return 'Not reached';
  }
}

function RouteSection({ tx }: { tx: MeshTransmissionEvent }) {
  const hops = tx.route?.hops ?? [];
  if (isActive(tx)) return null;
  if (!tx.route || hops.length < 2) {
    return (
      <section className="route-card">
        <h2 className="section__title">Route taken</h2>
        <p className="empty empty--plain">Route data isn’t available for this transmission.</p>
      </section>
    );
  }

  const stats = hopStatsFor(hops, tx.route.hopStats);
  const last = hops.length - 1;
  const localIndex = tx.direction === 'incoming' ? last : 0;
  const nameOf = (i: number) => (i === localIndex ? 'You' : hops[i]);
  const nodeState = (i: number): HopState => (i === 0 ? 'acked' : stats[i - 1].state);
  const nodeSub = (i: number): string => {
    if (i === 0) return 'Origin';
    if (i === last) return 'Destination';
    const rssi = stats[i - 1].rssiDbm;
    return rssi != null ? `${Math.round(rssi)} dBm`.replace('-', '−') : '—';
  };

  return (
    <section className="route-card">
      <h2 className="section__title">Route taken</h2>
      <div className="route" role="list" aria-label="Route">
        {hops.map((node, i) => (
          <RouteNodeAndLink
            key={`${node}-${i}`}
            code={shortCode(node)}
            name={nameOf(i)}
            sub={nodeSub(i)}
            state={nodeState(i)}
            linkState={i < last ? stats[i].state : null}
          />
        ))}
      </div>

      <h2 className="section__title">Hop status</h2>
      <ul className="hop-list">
        {stats.map((hop, i) => (
          <li key={i}>
            <span className="hop__label">
              <b>Hop {i + 1}</b>
              <small>{nameOf(i)} → {nameOf(i + 1)}</small>
            </span>
            <span className={`badge badge--${hop.state}`}>{hopBadge(hop.state, hop.packetLossRate)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function RouteNodeAndLink({
  code,
  name,
  sub,
  state,
  linkState,
}: {
  code: string;
  name: string;
  sub: string;
  state: HopState;
  linkState: HopState | null;
}) {
  return (
    <>
      <div className={`route__node route__node--${state}`} role="listitem">
        <span className="route__dot">{code}</span>
        <span className="route__name">{name}</span>
        <span className="route__sub">{sub}</span>
      </div>
      {linkState && <div className={`route__link route__link--${linkState}`} aria-hidden="true" />}
    </>
  );
}
