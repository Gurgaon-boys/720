import { useCallback, useEffect, useRef, useState } from 'react';
import './engine3d.css';

// Switches between the 2D dashboard, the lazy-loaded 3D engine view, the Results tab and the
// Sabotage Lab. The choice lives in the URL hash (#3d, #results, #sabotage), so a rehearsal or demo can open straight on a
// view and Back works.
export const VIEWS = [
  { id: '2d', name: '2D dashboard' },
  { id: '3d', name: '3D engine' },
  { id: 'results', name: 'Results' },
  { id: 'sabotage', name: 'SABOTAGE LAB' }, // src/lab/ (D140)
];
const HASH = { '2d': '', '3d': '#3d', results: '#results', sabotage: '#sabotage' };
const fromHash = () => VIEWS.find((v) => HASH[v.id] && HASH[v.id] === window.location.hash)?.id ?? '2d';

export function useViewTab() {
  const [view, setView] = useState(fromHash);
  useEffect(() => {
    const on = () => setView(fromHash());
    window.addEventListener('hashchange', on);
    window.addEventListener('popstate', on);
    return () => {
      window.removeEventListener('hashchange', on);
      window.removeEventListener('popstate', on);
    };
  }, []);
  const select = useCallback((id) => {
    const url = `${window.location.pathname}${window.location.search}${HASH[id]}`;
    window.history.pushState(null, '', url);
    setView(id);
  }, []);
  return [view, select];
}

export const tabId = (id) => `view-tab-${id}`;
export const panelId = (id) => `view-panel-${id}`;

// ARIA tabs: arrow keys move between tabs, Home/End jump to the ends.
export default function ViewTabs({ view, onChange }) {
  const refs = useRef({});
  const onKey = (e) => {
    const i = VIEWS.findIndex((v) => v.id === view);
    const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    let next = null;
    if (step) next = VIEWS[(i + step + VIEWS.length) % VIEWS.length];
    else if (e.key === 'Home') next = VIEWS[0];
    else if (e.key === 'End') next = VIEWS[VIEWS.length - 1];
    if (!next) return;
    e.preventDefault();
    onChange(next.id);
    refs.current[next.id]?.focus();
  };
  return (
    <div className="view-tabs" role="tablist" aria-label="Diagnostics view">
      {VIEWS.map((v) => {
        const on = v.id === view;
        return (
          <button
            key={v.id}
            ref={(el) => { refs.current[v.id] = el; }}
            type="button"
            role="tab"
            id={tabId(v.id)}
            aria-selected={on}
            aria-controls={panelId(v.id)}
            tabIndex={on ? 0 : -1}
            className={`view-tab${on ? ' on' : ''}`}
            onClick={() => onChange(v.id)}
            onKeyDown={onKey}
          >
            {v.name}
          </button>
        );
      })}
    </div>
  );
}
