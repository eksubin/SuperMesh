import type { MeshTransmissionEvent } from '../api/types';

const pad = (n: number) => String(n).padStart(2, '0');

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb < 100 ? kb.toFixed(1) : Math.round(kb)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

/** "04m 12s" (padded) or "0m 12s" (compact, for small rings). "—" when unknown. */
export function formatEtr(seconds: number | null | undefined, compact = false): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—';
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}h ${pad(m)}m`;
  return compact ? `${m}m ${pad(r)}s` : `${pad(m)}m ${pad(r)}s`;
}

/** "Today, 14:20" / "Yesterday, 18:05" / "Oct 08, 09:12" */
export function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (sameDay(d, today)) return `Today, ${time}`;
  if (sameDay(d, yesterday)) return `Yesterday, ${time}`;
  const month = d.toLocaleString('en-US', { month: 'short' });
  return `${month} ${pad(d.getDate())}, ${time}`;
}

export function formatLoss(rate: number): string {
  return `${Number(rate.toFixed(1))}%`;
}

const TYPE_LABEL = { text: 'Text', audio: 'Audio', video: 'Video', image: 'Image' } as const;

export function typeLabel(tx: MeshTransmissionEvent): string {
  return tx.kind === 'message' ? 'Message' : TYPE_LABEL[tx.fileType];
}

export function displayName(tx: MeshTransmissionEvent): string {
  if (tx.kind === 'message' && tx.preview) return `“${tx.preview}”`;
  return tx.fileName;
}

/** Ring percentage for in-flight items: never shows 100% before "delivered". */
export function inFlightPercent(tx: MeshTransmissionEvent): number {
  return Math.min(99, Math.floor(tx.progressPercentage));
}
