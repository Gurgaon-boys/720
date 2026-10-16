import { useState } from 'react';

// One tabbed "details" area under the story, so only one deep panel shows at a time:
// Reliability Agent · Incident timeline · OPC UA link · Live signals. All stay mounted (hidden), so the
// agent keeps auto-investigating and the timeline keeps filling while another tab is shown.

export default function Details({ tabs }) {
  const [on, setOn] = useState(tabs[0].id);
  return (
    <section className="details" aria-label="Details">
      <div className="details-tabs" role="tablist" aria-label="Details">
        {tabs.map((t) => (
          <button
            key={t.id} type="button" role="tab" aria-selected={on === t.id} id={`dt-${t.id}`} aria-controls={`dp-${t.id}`}
            className={`details-tab${on === t.id ? ' on' : ''}`} onClick={() => setOn(t.id)}
          >
            {t.label}
            {t.badge ? <span className={`details-badge t-${t.badgeTone ?? 'neutral'}`}>{t.badge}</span> : null}
          </button>
        ))}
      </div>
      {tabs.map((t) => (
        <div key={t.id} role="tabpanel" id={`dp-${t.id}`} aria-labelledby={`dt-${t.id}`} hidden={on !== t.id} className="details-panel">
          {t.body}
        </div>
      ))}
    </section>
  );
}
