import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useState, type ReactNode } from 'react';
import { fetchLog, fetchMeshInfo, fetchTransmissions } from '../api/client';
import { connectMeshSocket, type ConnectionState } from '../api/socket';
import {
  isActive,
  VOICE_BITRATES,
  type ImageCodec,
  type LogEntry,
  type MeshInfo,
  type MeshTransmissionEvent,
  type ServerMessage,
  type VoiceCodec,
} from '../api/types';
import { parse, useHashRoute, type Route } from '../lib/router';

const LOG_LIMIT = 600;

interface State {
  byId: Record<string, MeshTransmissionEvent>;
  connection: ConnectionState;
  logs: LogEntry[];
}

type Action =
  | { type: 'snapshot'; transmissions: MeshTransmissionEvent[] }
  | { type: 'upsert'; event: MeshTransmissionEvent }
  | { type: 'merge'; transmissions: MeshTransmissionEvent[] }
  | { type: 'connection'; state: ConnectionState }
  | { type: 'logs'; entries: LogEntry[] }
  | { type: 'clearLogs' };

const initial: State = { byId: {}, connection: 'connecting', logs: [] };

/** Never let a stale in-flight update overwrite a finished transmission (a retry gets a new createdAt). */
function shouldApply(prev: MeshTransmissionEvent | undefined, next: MeshTransmissionEvent): boolean {
  if (!prev) return true;
  if (!isActive(prev) && isActive(next) && prev.createdAt === next.createdAt) return false;
  return true;
}

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'snapshot': {
      const byId: State['byId'] = {};
      for (const tx of action.transmissions) byId[tx.fileId] = tx;
      return { ...state, byId };
    }
    case 'upsert': {
      if (!shouldApply(state.byId[action.event.fileId], action.event)) return state;
      return { ...state, byId: { ...state.byId, [action.event.fileId]: action.event } };
    }
    case 'merge': {
      const byId = { ...state.byId };
      for (const tx of action.transmissions) if (!byId[tx.fileId]) byId[tx.fileId] = tx;
      return { ...state, byId };
    }
    case 'connection':
      return { ...state, connection: action.state };
    case 'logs': {
      const last = state.logs.length ? state.logs[state.logs.length - 1].seq : 0;
      const fresh = action.entries.filter((e) => e.seq > last);
      if (!fresh.length) return state;
      return { ...state, logs: [...state.logs, ...fresh].slice(-LOG_LIMIT) };
    }
    case 'clearLogs':
      return { ...state, logs: [] };
  }
}

/** Which node's phone is this? Items without node info (the mock mesh) are always shown. */
function visibleTo(tx: MeshTransmissionEvent, me: number): boolean {
  if (tx.fromNode == null && tx.toNode == null) return true;
  return tx.direction === 'outgoing' ? tx.fromNode === me : tx.toNode === me;
}

const NODE_KEY = 'chrp.nodes';
const CODEC_KEY = 'chrp.codec';
const VOICE_KEY = 'chrp.voice';

export type PhoneId = 'left' | 'right';

function loadNodes(id: PhoneId): { me: number; peer: number } | null {
  try {
    const raw = window.localStorage.getItem(`${NODE_KEY}.${id}`);
    if (!raw) return null;
    const v = JSON.parse(raw) as { me?: unknown; peer?: unknown };
    if (typeof v.me === 'number' && typeof v.peer === 'number') return { me: v.me, peer: v.peer };
  } catch {
    /* storage can be blocked */
  }
  return null;
}

/** Everything the phones share: transmissions, the log, the socket and the simulator state. */
interface SharedValue extends State {
  mesh: MeshInfo | null;
  upsert: (event: MeshTransmissionEvent) => void;
  clearLogs: () => void;
}

/** Everything that belongs to one phone: its node, its screen and its toasts. */
interface PhoneValue {
  /** Outgoing items for this phone's node that are still in flight, oldest first. */
  active: MeshTransmissionEvent[];
  /** Delivered and failed items for this phone's node, newest first. */
  history: MeshTransmissionEvent[];
  /** Sum of the in-flight ETRs, or null when any of them has no estimate yet. */
  totalQueueEtrSeconds: number | null;
  /** The node this phone acts as, and the node it sends to. */
  me: number;
  peer: number;
  setNodes: (me: number, peer: number) => void;
  /** How pictures are compressed before they go over the mesh. */
  codec: ImageCodec;
  setCodec: (codec: ImageCodec) => void;
  /** How voice recordings are compressed, and the EnCodec bitrate (kbps) when neural. */
  voiceCodec: VoiceCodec;
  voiceBitrate: number;
  setVoice: (codec: VoiceCodec, bitrate: number) => void;
  toast: { id: number; text: string } | null;
  showToast: (text: string | null) => void;
  route: Route;
  /** Go to a "#/..." path inside this phone. */
  go: (href: string) => void;
  /** True when this phone owns the URL hash (the single-phone layout). */
  syncHash: boolean;
}

type MeshContextValue = SharedValue & PhoneValue;

const SharedContext = createContext<SharedValue | null>(null);
const PhoneContext = createContext<PhoneValue | null>(null);

export function MeshProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initial);
  const [mesh, setMesh] = useState<MeshInfo | null>(null);
  const socketOpen = state.connection === 'open';

  useEffect(() => {
    const onMessage = (msg: ServerMessage) => {
      if (msg.type === 'snapshot') dispatch({ type: 'snapshot', transmissions: msg.transmissions });
      else if (msg.type === 'transmission') dispatch({ type: 'upsert', event: msg.event });
      else if (msg.type === 'log') dispatch({ type: 'logs', entries: [msg.entry] });
    };
    const stop = connectMeshSocket(onMessage, (s) => dispatch({ type: 'connection', state: s }));
    // REST fallbacks so History and the log still load if the socket is slow to open.
    fetchTransmissions('all')
      .then((list) => dispatch({ type: 'merge', transmissions: list }))
      .catch(() => undefined);
    fetchLog(0)
      .then((entries) => dispatch({ type: 'logs', entries }))
      .catch(() => undefined);
    return stop;
  }, []);

  // Keep polling the simulator state: it takes about 30 s to boot, and it can restart on its own.
  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    const tick = () => {
      fetchMeshInfo()
        .then((info) => {
          if (stopped) return;
          setMesh(info);
          timer = window.setTimeout(tick, info.status === 'booting' ? 2000 : 6000);
        })
        .catch(() => {
          if (!stopped) timer = window.setTimeout(tick, 4000);
        });
    };
    tick();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [socketOpen]);

  const upsert = useCallback((event: MeshTransmissionEvent) => dispatch({ type: 'upsert', event }), []);
  const clearLogs = useCallback(() => dispatch({ type: 'clearLogs' }), []);
  const value = useMemo<SharedValue>(() => ({ ...state, mesh, upsert, clearLogs }), [state, mesh, upsert, clearLogs]);

  return <SharedContext.Provider value={value}>{children}</SharedContext.Provider>;
}

interface PhoneProviderProps {
  /** The left phone starts as the backend's local node, the right phone as its peer. */
  id: PhoneId;
  /** Single-phone layout: this phone owns the URL hash so browser and swipe-back work. */
  syncHash?: boolean;
  children: ReactNode;
}

export function PhoneProvider({ id, syncHash = false, children }: PhoneProviderProps) {
  const shared = useContext(SharedContext);
  if (!shared) throw new Error('<PhoneProvider> must be inside <MeshProvider>');
  const { byId, mesh } = shared;

  const [nodes, setNodesState] = useState<{ me: number; peer: number }>(
    () => loadNodes(id) ?? (id === 'left' ? { me: 0, peer: 5 } : { me: 5, peer: 0 }),
  );
  const [codecChoice, setCodecChoice] = useState<ImageCodec | null>(() => {
    try {
      const saved = window.localStorage.getItem(CODEC_KEY);
      return saved === 'neural' || saved === 'jpeg' ? saved : null;
    } catch {
      return null;
    }
  });
  // Until someone picks one, follow the server's default; fall back to JPEG if the neural codec can't load.
  const codec: ImageCodec = codecChoice ?? (mesh?.codecs.default === 'neural' && mesh.codecs.neural !== 'unavailable' ? 'neural' : 'jpeg');
  const setCodec = useCallback((next: ImageCodec) => {
    setCodecChoice(next);
    try {
      window.localStorage.setItem(CODEC_KEY, next);
    } catch {
      /* ignore */
    }
  }, []);
  const [voiceChoice, setVoiceChoice] = useState<{ codec: VoiceCodec; bitrate: number } | null>(() => {
    try {
      const v = JSON.parse(window.localStorage.getItem(VOICE_KEY) ?? 'null') as { codec?: unknown; bitrate?: unknown } | null;
      if ((v?.codec === 'neural' || v?.codec === 'raw') && VOICE_BITRATES.some((b) => b === v.bitrate)) {
        return { codec: v.codec, bitrate: v.bitrate as number };
      }
    } catch {
      /* ignore */
    }
    return null;
  });
  const voiceUp = mesh?.codecs.voice !== 'unavailable' && mesh?.codecs.voice !== 'off';
  const voiceCodec: VoiceCodec = voiceChoice?.codec ?? (mesh?.codecs.voiceDefault === 'neural' && voiceUp ? 'neural' : 'raw');
  const voiceBitrate = voiceChoice?.bitrate ?? (VOICE_BITRATES.find((b) => b === mesh?.codecs.voiceBitrate) ?? 6);
  const setVoice = useCallback((codec: VoiceCodec, bitrate: number) => {
    setVoiceChoice({ codec, bitrate });
    try {
      window.localStorage.setItem(VOICE_KEY, JSON.stringify({ codec, bitrate }));
    } catch {
      /* ignore */
    }
  }, []);
  const [toast, setToast] = useState<{ id: number; text: string } | null>(null);
  const hashRoute = useHashRoute();
  const [localRoute, setLocalRoute] = useState<Route>({ name: 'main' });
  const route = syncHash ? hashRoute : localRoute;

  // First visit: start from the backend's default pair of nodes.
  useEffect(() => {
    if (!mesh || loadNodes(id)) return;
    setNodesState(id === 'left' ? { me: mesh.localNode, peer: mesh.peerNode } : { me: mesh.peerNode, peer: mesh.localNode });
  }, [mesh, id]);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 3500);
    return () => window.clearTimeout(t);
  }, [toast]);

  const showToast = useCallback((text: string | null) => setToast(text ? { id: Date.now(), text } : null), []);
  const setNodes = useCallback(
    (me: number, peer: number) => {
      setNodesState({ me, peer });
      try {
        window.localStorage.setItem(`${NODE_KEY}.${id}`, JSON.stringify({ me, peer }));
      } catch {
        /* ignore */
      }
    },
    [id],
  );
  const go = useCallback(
    (href: string) => {
      if (syncHash) window.location.hash = href.replace(/^#/, '');
      else setLocalRoute(parse(href));
    },
    [syncHash],
  );

  const value = useMemo<PhoneValue>(() => {
    const mine = Object.values(byId).filter((t) => visibleTo(t, nodes.me));
    const active = mine
      .filter((t) => t.direction === 'outgoing' && isActive(t))
      .sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    const history = mine
      .filter((t) => !isActive(t))
      .sort((a, b) => (b.completedAt ?? b.createdAt).localeCompare(a.completedAt ?? a.createdAt));
    const totalQueueEtrSeconds = active.some((t) => t.etrSeconds == null)
      ? null
      : Math.round(active.reduce((sum, t) => sum + (t.etrSeconds ?? 0), 0));
    return { active, history, totalQueueEtrSeconds, me: nodes.me, peer: nodes.peer, setNodes, codec, setCodec, voiceCodec, voiceBitrate, setVoice, toast, showToast, route, go, syncHash };
  }, [byId, nodes, setNodes, codec, setCodec, voiceCodec, voiceBitrate, setVoice, toast, showToast, route, go, syncHash]);

  return <PhoneContext.Provider value={value}>{children}</PhoneContext.Provider>;
}

export function useMesh(): MeshContextValue {
  const shared = useContext(SharedContext);
  const phone = useContext(PhoneContext);
  if (!shared || !phone) throw new Error('useMesh must be used inside <MeshProvider> and <PhoneProvider>');
  return useMemo(() => ({ ...shared, ...phone }), [shared, phone]);
}
