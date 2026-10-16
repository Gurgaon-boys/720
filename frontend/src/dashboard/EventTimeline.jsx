import { useEffect, useRef, useState } from 'react';

// Compact alarm timeline under the banner: Raised → Acknowledged → Cleared. The alarm events come
// from App, which derives them once per received state broadcast (lib/eventLog.js), so a quick
// sequence of messages can't be skipped. It is not a historical log: anything before the page
// opened, or while the connection was down, was not observed, and the gaps (added here from the
// UI watchdog) say so. Collapsed by default to the newest event; "View session events" opens the
// full list with its gaps and limitation.

const clock = (unixS) => new Date(unixS * 1000).toLocaleTimeString();

export default function EventTimeline({ events, offline }) {
  const [gaps, setGaps] = useState([]);
  const lostAt = useRef(null);

  useEffect(() => {
    if (offline) {
      lostAt.current = Date.now() / 1000;
    } else if (lostAt.current != null) {
      const from = lostAt.current;
      lostAt.current = null;
      setGaps((g) => [...g, { id: `gap-${from}`, kind: 'Gap', text: `no data ${Math.round(Date.now() / 1000 - from)} s · events not observed`, at: from, gap: true }]);
    }
  }, [offline]);

  const [open, setOpen] = useState(false);
  const all = [...events, ...gaps].sort((a, b) => a.at - b.at);
  // collapsed: the newest alarm event only; the gaps and the full list are one click away
  const latest = [...all].reverse().find((e) => !e.gap) ?? null;
  const toggle = (
    <button type="button" className="toggle-btn" aria-expanded={open} aria-controls="session-events" onClick={() => setOpen((v) => !v)}>
      {open ? 'Hide session events' : `View session events (${all.length})`}
    </button>
  );

  if (!open) {
    return (
      <section className="timeline timeline-compact" aria-label="Latest event observed this session">
        <span className="eyebrow">Latest event</span>
        {latest ? (
          <ol className="timeline-list mono"><TimelineItem e={latest} first /></ol>
        ) : (
          <p className="timeline-empty">No alarm events observed yet.</p>
        )}
        {offline && <small className="timeline-down">connection down now</small>}
        {toggle}
      </section>
    );
  }

  return (
    <section className="timeline" id="session-events" aria-label="Events observed this session">
      <div className="timeline-head">
        <span className="eyebrow">Events observed this session</span>
        <small>from live broadcasts only · not a complete log{offline ? ' · connection down now' : ''}</small>
        {toggle}
      </div>
      {all.length === 0 ? (
        <p className="timeline-empty">No alarm events observed yet.</p>
      ) : (
        <ol className="timeline-list mono">
          {all.map((e, i) => (
            <TimelineItem key={e.id} e={e} first={i === 0} />
          ))}
        </ol>
      )}
    </section>
  );
}

function TimelineItem({ e, first }) {
  return (
    <>
      {!first && <li className="arrow" aria-hidden="true">→</li>}
      <li className={e.raised ? 'raised' : e.gap ? 'gap' : undefined} style={e.color ? { '--c': e.color } : undefined}>
        <span className="t">{clock(e.at)}</span>
        <span className="k">{e.kind}</span>
        <span>{e.text}</span>
      </li>
    </>
  );
}
