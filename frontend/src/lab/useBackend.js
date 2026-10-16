import { useEffect, useRef, useState } from 'react';

// Polling hooks for the Resilience Lab's backend reads (D141). Only while the tab is open.

const KEEP = 300;

// The incident record, appended incrementally (GET /events?after=last seq). A backend restart (seq goes
// back) starts the list over.
export function useBackendEvents(api, active, everyMs = 1000) {
  const [events, setEvents] = useState([]);
  const lastSeq = useRef(0);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!api || !active) return undefined;
    let dead = false;
    const tick = () => api.events(lastSeq.current).then((r) => {
      if (dead) return;
      setError(null);
      const fresh = r.events ?? [];
      if (!fresh.length) return;
      if (fresh[0].seq <= lastSeq.current) { // backend restarted
        lastSeq.current = 0;
        setEvents([]);
        return;
      }
      lastSeq.current = fresh[fresh.length - 1].seq;
      setEvents((l) => [...l, ...fresh].slice(-KEEP));
    }).catch((e) => !dead && setError(e.message));
    tick();
    const id = setInterval(tick, everyMs);
    return () => { dead = true; clearInterval(id); };
  }, [api, active, everyMs]);
  return { events, error };
}

export function usePoll(fn, active, everyMs = 2000) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!fn || !active) return undefined;
    let dead = false;
    const tick = () => fn().then((d) => { if (!dead) { setData(d); setError(null); } }).catch((e) => !dead && setError(e.message));
    tick();
    const id = setInterval(tick, everyMs);
    return () => { dead = true; clearInterval(id); };
  }, [fn, active, everyMs]);
  return { data, error };
}
