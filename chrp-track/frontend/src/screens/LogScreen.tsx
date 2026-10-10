import { useEffect, useRef, useState } from 'react';
import type { LogEntry } from '../api/types';
import { useMesh } from '../state/MeshProvider';

function stamp(ts: number): string {
  const d = new Date(ts * 1000);
  const p = (n: number, w = 2) => String(n).padStart(w, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`;
}

/** Live view of what the mesh is doing: chunks leaving, radio transmissions, relays, ACKs, rebuilds. */
export function LogScreen() {
  const { logs, clearLogs, mesh } = useMesh();
  const [follow, setFollow] = useState(true);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const box = boxRef.current;
    if (follow && box) box.scrollTop = box.scrollHeight;
  }, [logs, follow]);

  return (
    <main className="screen screen--log">
      <h1 className="page-title">Mesh log</h1>
      <div className="log-tools">
        <label className="log-follow">
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
          Follow
        </label>
        <button type="button" className="log-clear" onClick={clearLogs}>Clear</button>
      </div>
      <div className="log" ref={boxRef} role="log" aria-live="off">
        {logs.length === 0 ? (
          <p className="log__empty">
            {mesh?.mock
              ? 'The mock mesh doesn’t produce a packet log. Start the backend with MOCK_MESH=0 to see real events.'
              : 'Nothing yet. Send a message and every step shows up here.'}
          </p>
        ) : (
          logs.map((e: LogEntry) => (
            <div key={e.seq} className={`log__line log__line--${e.level}`}>
              <span className="log__time">{stamp(e.ts)}</span> {e.msg}
            </div>
          ))
        )}
      </div>
      <p className="log__legend">
        <span className="log__line--tx">sending / radio</span> · <span className="log__line--rx">receiving</span> ·{' '}
        <span className="log__line--ok">success</span> · <span className="log__line--error">error</span>
      </p>
    </main>
  );
}
