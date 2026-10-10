import { useEffect, useState, type MouseEvent } from 'react';
import { TabBar } from './components/TabBar';
import { DetailsScreen } from './screens/DetailsScreen';
import { HistoryScreen } from './screens/HistoryScreen';
import { LogScreen } from './screens/LogScreen';
import { MainScreen } from './screens/MainScreen';
import { MeshProvider, PhoneProvider, useMesh } from './state/MeshProvider';

/** Two phones side by side need room; on a real phone or a narrow window show just one. */
const TWIN_QUERY = '(min-width: 900px)';

function useTwinLayout(): boolean {
  const [twin, setTwin] = useState(() => window.matchMedia(TWIN_QUERY).matches);
  useEffect(() => {
    const mq = window.matchMedia(TWIN_QUERY);
    const onChange = () => setTwin(mq.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  return twin;
}

function Phone() {
  const { connection, toast, route, go, syncHash } = useMesh();

  // Twin phones keep their own screen, so "#/..." links navigate inside the phone instead of the browser URL.
  function onClick(e: MouseEvent<HTMLDivElement>) {
    if (syncHash || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey) return;
    const link = (e.target as HTMLElement).closest('a[href^="#/"]');
    if (link) {
      e.preventDefault();
      go(link.getAttribute('href') ?? '#/');
    }
  }

  return (
    <div className="app" onClick={onClick}>
      {connection === 'reconnecting' && (
        <div className="conn-banner" role="status">Connection to the mesh backend lost. Reconnecting…</div>
      )}

      {route.name === 'main' && <MainScreen />}
      {route.name === 'history' && <HistoryScreen />}
      {route.name === 'log' && <LogScreen />}
      {route.name === 'details' && <DetailsScreen key={route.id} id={route.id} />}

      {route.name !== 'details' && <TabBar active={route.name} />}

      {toast && (
        <div key={toast.id} className="toast" role="status">
          {toast.text}
        </div>
      )}
    </div>
  );
}

export default function App() {
  const twin = useTwinLayout();
  return (
    <MeshProvider>
      <div className={`phones${twin ? ' phones--twin' : ''}`}>
        <PhoneProvider id="left" syncHash={!twin}>
          <Phone />
        </PhoneProvider>
        {twin && (
          <PhoneProvider id="right">
            <Phone />
          </PhoneProvider>
        )}
      </div>
    </MeshProvider>
  );
}
