import { useEffect, useRef, useState } from 'react';

// Compact alarm timeline under the banner: Raised → Acknowledged → Cleared. The alarm events come
// from App, which derives them once per received state broadcast (lib/eventLog.js), so a quick
// sequence of messages can't be skipped. It is not a historical log: anything before the page
// opened, or while the connection was down, was not observed, and the gaps (added here from the
// UI watchdog) say so.

const SHOW = 8; // newest events kept on screen; older ones are counted
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

  const all = [...events, ...gaps].sort((a, b) => a.at - b.at);
  const shown = all.slice(-SHOW);
  const older = all.length - shown.length;

  return (
    <section className="timeline" aria-label="Events observed this session">
      <div className="timeline-head">
        <span className="eyebrow">Events observed this session</span>
        <small>from live broadcasts only · not a complete log{offline ? ' · connection down now' : ''}</small>
      </div>
      {shown.length === 0 ? (
        <p className="timeline-empty">No alarm events observed yet.</p>
      ) : (
        <ol className="timeline-list mono">
          {older > 0 && <li className="gap">{older} earlier</li>}
          {shown.map((e, i) => (
            <TimelineItem key={e.id} e={e} first={i === 0 && older === 0} />
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
