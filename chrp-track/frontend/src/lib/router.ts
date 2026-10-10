// Tiny hash router: #/ (main), #/history, #/tx/<fileId> (details).
// Hash URLs keep iOS swipe-back and browser back working without server config.
import { useEffect, useState } from 'react';

export type Route = { name: 'main' } | { name: 'history' } | { name: 'log' } | { name: 'details'; id: string };

export function parse(hash: string): Route {
  const path = hash.replace(/^#\/?/, '');
  if (path === 'history') return { name: 'history' };
  if (path === 'log') return { name: 'log' };
  const match = path.match(/^tx\/(.+)$/);
  if (match) return { name: 'details', id: decodeURIComponent(match[1]) };
  return { name: 'main' };
}

export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parse(window.location.hash));
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  return route;
}

export const paths = {
  main: '#/',
  history: '#/history',
  log: '#/log',
  details: (id: string) => `#/tx/${encodeURIComponent(id)}`,
};

export function navigate(href: string): void {
  window.location.hash = href.replace(/^#/, '');
}
