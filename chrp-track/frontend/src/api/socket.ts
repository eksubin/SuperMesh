// Live updates from the backend over one WebSocket, with automatic reconnect.
import type { ServerMessage } from './types';

export type ConnectionState = 'connecting' | 'open' | 'reconnecting';

function socketUrl(): string {
  const explicit = import.meta.env.VITE_WS_URL as string | undefined;
  if (explicit) return explicit;
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.host}/ws`;
}

export function connectMeshSocket(
  onMessage: (message: ServerMessage) => void,
  onState: (state: ConnectionState) => void,
): () => void {
  let ws: WebSocket | null = null;
  let stopped = false;
  let attempt = 0;
  let retryTimer: number | undefined;
  let pingTimer: number | undefined;

  const open = () => {
    window.clearTimeout(retryTimer);
    onState(attempt === 0 ? 'connecting' : 'reconnecting');
    const socket = new WebSocket(socketUrl());
    ws = socket;

    socket.onopen = () => {
      attempt = 0;
      onState('open');
      pingTimer = window.setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) socket.send('ping');
      }, 20_000);
    };
    socket.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data as string) as ServerMessage);
      } catch {
        /* ignore malformed frames */
      }
    };
    socket.onclose = () => {
      window.clearInterval(pingTimer);
      if (stopped || ws !== socket) return;
      attempt += 1;
      onState('reconnecting');
      const delay = Math.min(10_000, 500 * 2 ** Math.min(attempt, 5));
      retryTimer = window.setTimeout(open, delay);
    };
    socket.onerror = () => socket.close();
  };

  // iOS suspends sockets in the background: reconnect straight away on return.
  const onVisible = () => {
    if (document.visibilityState === 'visible' && ws && ws.readyState > WebSocket.OPEN) {
      attempt = Math.max(attempt, 1);
      open();
    }
  };
  document.addEventListener('visibilitychange', onVisible);

  open();

  return () => {
    stopped = true;
    document.removeEventListener('visibilitychange', onVisible);
    window.clearTimeout(retryTimer);
    window.clearInterval(pingTimer);
    ws?.close();
  };
}
