// REST calls to the FastAPI backend. Same origin by default (Vite proxy in dev,
// nginx in Docker); set VITE_API_BASE to point somewhere else.
import type { FileType, ImageCodec, LogEntry, MeshInfo, MeshTransmissionEvent, VoiceCodec } from './types';

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '';

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, init);
  } catch {
    throw new ApiError(0, 'Can’t reach the CHRP backend. Check the connection.');
  }
  if (!res.ok) {
    let detail: string = res.statusText;
    try {
      const body = await res.json();
      if (typeof body?.detail === 'string') detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

/** Which simulated nodes a send goes between, and an optional type override (e.g. a .webm recording is audio). */
export interface SendOptions {
  fromNode?: number;
  toNode?: number;
  fileType?: FileType;
  imageCodec?: ImageCodec;
  voiceCodec?: VoiceCodec;
  /** kbps, used with the neural voice codec. */
  voiceBitrate?: number;
}

/** Upload a file; the backend compresses, chunks and starts the mesh transfer. */
export function uploadFile(file: File, opts: SendOptions = {}): Promise<MeshTransmissionEvent> {
  const body = new FormData();
  body.append('file', file, file.name);
  if (opts.fromNode != null) body.append('from_node', String(opts.fromNode));
  if (opts.toNode != null) body.append('to_node', String(opts.toNode));
  if (opts.fileType) body.append('file_type', opts.fileType);
  if (opts.imageCodec) body.append('image_codec', opts.imageCodec);
  if (opts.voiceCodec) body.append('voice_codec', opts.voiceCodec);
  if (opts.voiceBitrate != null) body.append('voice_bitrate', String(opts.voiceBitrate));
  return request('/api/transmissions', { method: 'POST', body });
}

/** Send a typed text message over the mesh. */
export function sendTextMessage(text: string, opts: SendOptions = {}): Promise<MeshTransmissionEvent> {
  return request('/api/messages', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, from_node: opts.fromNode, to_node: opts.toNode }),
  });
}

/** URL of the stored payload (image, audio or text) for previews and playback. */
export function contentUrl(fileId: string): string {
  return `${API_BASE}/api/transmissions/${encodeURIComponent(fileId)}/content`;
}

export function fetchMeshInfo(): Promise<MeshInfo> {
  return request('/api/mesh');
}

export function fetchLog(since = 0): Promise<LogEntry[]> {
  return request(`/api/log?since=${since}`);
}

/** Retry a failed outgoing transmission. */
export function retryTransmission(fileId: string): Promise<MeshTransmissionEvent> {
  return request(`/api/transmissions/${encodeURIComponent(fileId)}/retry`, { method: 'POST' });
}

/** Fetch transmissions (used as a fallback when the WebSocket is down). */
export function fetchTransmissions(scope: 'all' | 'active' | 'history' = 'all'): Promise<MeshTransmissionEvent[]> {
  return request(`/api/transmissions?scope=${scope}`);
}

/** Fetch one transmission, e.g. when the details page is opened from a link. */
export function fetchTransmission(fileId: string): Promise<MeshTransmissionEvent> {
  return request(`/api/transmissions/${encodeURIComponent(fileId)}`);
}

export function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError && err.message) return err.message;
  return fallback;
}
