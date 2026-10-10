// Mirrors backend/app/models.py and CONTRACT.md. Draft — expect changes during integration.

export type FileType = 'text' | 'audio' | 'video' | 'image';
export type Direction = 'outgoing' | 'incoming';
export type TransmissionStatus = 'queued' | 'compressing' | 'sending' | 'delivered' | 'failed';
export type HopState = 'pending' | 'active' | 'acked' | 'failed';
export type TransmissionKind = 'file' | 'message';

export interface HopStat {
  from: string;
  to: string;
  state: HopState;
  packetLossRate: number | null;
  rssiDbm: number | null;
}

export interface MeshRoute {
  /** Full path: origin first, destination last. */
  hops: string[];
  currentHopIndex: number;
  rssiDbm: number | null;
  packetLossRate: number | null;
  /** One entry per hop: hops[i] -> hops[i + 1]. */
  hopStats: HopStat[];
}

export interface MeshTransmissionEvent {
  fileId: string;
  fileName: string;
  fileType: FileType;
  kind: TransmissionKind;
  preview: string | null;
  direction: Direction;
  status: TransmissionStatus;

  sizeBytes: number;
  progressPercentage: number;
  etrSeconds: number | null;
  totalQueueEtrSeconds: number | null;

  packetsAcked: number;
  packetsTotal: number | null;
  chunkSizeBytes: number;
  preset: string | null;

  createdAt: string;
  completedAt: string | null;
  failureReason: string | null;

  route: MeshRoute | null;

  /** Simulated mesh nodes involved. null while the mock mesh is used. */
  fromNode: number | null;
  toNode: number | null;
  /** True when the payload can be fetched from /api/transmissions/{fileId}/content. */
  hasContent: boolean;
  contentType: string | null;
}

export interface MeshInfo {
  mock: boolean;
  status: 'booting' | 'ready' | 'error' | 'off';
  error: string | null;
  nodes: number;
  localNode: number;
  peerNode: number;
  /** Which picture compression the server offers. "neural" is the CompressAI codec. */
  codecs: {
    default: ImageCodec;
    neural: CodecState;
    /** EnCodec voice compression: default choice, model state and default bitrate in kbps. */
    voiceDefault: VoiceCodec;
    voice: CodecState;
    voiceBitrate: number;
  };
}

export type CodecState = 'off' | 'idle' | 'loading' | 'ready' | 'unavailable';
export type ImageCodec = 'neural' | 'jpeg';
export type VoiceCodec = 'neural' | 'raw';
/** Bitrates (kbps) the EnCodec 24 kHz model supports. */
export const VOICE_BITRATES = [1.5, 3, 6, 12, 24] as const;

export type LogLevel = 'info' | 'tx' | 'rx' | 'ok' | 'error';

export interface LogEntry {
  seq: number;
  ts: number;
  level: LogLevel;
  msg: string;
}

export type ServerMessage =
  | { type: 'snapshot'; transmissions: MeshTransmissionEvent[]; totalQueueEtrSeconds: number | null }
  | { type: 'transmission'; event: MeshTransmissionEvent; totalQueueEtrSeconds: number | null }
  | { type: 'log'; entry: LogEntry }
  | { type: 'pong' };

export const ACTIVE_STATUSES: readonly TransmissionStatus[] = ['queued', 'compressing', 'sending'];

export function isActive(tx: Pick<MeshTransmissionEvent, 'status'>): boolean {
  return ACTIVE_STATUSES.includes(tx.status);
}
