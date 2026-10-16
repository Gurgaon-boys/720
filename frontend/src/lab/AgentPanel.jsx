import { useCallback, useEffect, useRef, useState } from 'react';
import { reducedMotion } from '../lib/motion.js';

// 720 RELIABILITY AGENT (D141): an engineering investigator over the backend's own state.
// GET /investigate runs six read-only tools and two deterministic checks on the backend and returns each
// completed step with its duration. The steps are then revealed in order (a replay of calls that already
// finished; the panel says so). No language model: the answers are generated from the facts, and each
// lists the fields it used. The agent can't acknowledge, inject, switch sensors or change any alarm.

const REVEAL_MS = 380;
const AUTO_KINDS = (e) => (e.kind === 'sensor_health' || (e.kind === 'decision' && e.data?.state === 'ALARM') || e.kind === 'classifier')
  && e.phase === 'confirmed';

export default function AgentPanel({ api, events, active }) {
  const [inv, setInv] = useState(null);
  const [shown, setShown] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState('sensor_failed');
  const lastAuto = useRef(0);
  const still = useRef(reducedMotion());

  const run = useCallback((trigger) => {
    if (!api) return;
    setBusy(true);
    setError(null);
    api.investigate(trigger).then((r) => {
      setInv(r);
      setShown(still.current ? r.steps.length : 0);
      const first = r.answers?.find((a) => a.key === (r.findings?.availability?.offline?.length ? 'sensor_failed' : 'why_alarm'));
      if (first) setOpen(first.key);
    }).catch((e) => setError(e.message)).finally(() => setBusy(false));
  }, [api]);

  // automatic investigation on a confirmed sensor failure / recovery, a witness change or a new ALARM
  useEffect(() => {
    if (!active || !events.length) return;
    const trig = [...events].reverse().find((e) => AUTO_KINDS(e) && e.seq > lastAuto.current);
    if (!trig) return;
    lastAuto.current = events[events.length - 1].seq;
    run(trig.text);
  }, [events, active, run]);

  useEffect(() => { if (active && api && !inv) run('tab opened'); }, [active, api, inv, run]);

  useEffect(() => {
    if (!inv || shown >= inv.steps.length) return undefined;
    const id = setTimeout(() => setShown((n) => n + 1), REVEAL_MS);
    return () => clearTimeout(id);
  }, [inv, shown]);

  const done = inv && shown >= inv.steps.length;
  const total = inv?.steps?.reduce((a, s) => a + s.ms, 0) ?? 0;
  return (
    <section className="lab-card agent" aria-label="720 Reliability Agent">
      <div className="lab-card-head">
        <h3>720 Reliability Agent</h3>
        <button type="button" className="lab-btn ghost" disabled={!api || busy} onClick={() => run('operator request')}>
          {busy ? 'Investigating…' : 'Investigate now'}
        </button>
      </div>
      {!api && <p className="lab-note">Needs the live backend.</p>}
      {error && <p className="lab-note err">{error}</p>}
      {inv?.status === 'no_data' && <p className="lab-note">{inv.headline}</p>}
      {inv?.status === 'ok' && (
        <>
          <p className="agent-trigger mono">Trigger: {inv.trigger ?? 'manual'}</p>
          <ol className="agent-steps">
            {inv.steps.map((s, i) => (
              <li key={s.tool} className={i < shown ? 'done' : i === shown ? 'running' : 'wait'}>
                <span className="agent-tick" aria-hidden="true">{i < shown ? '✓' : i === shown ? '◌' : '·'}</span>
                <span className="agent-step-label">{s.label}</span>
                <code className="agent-tool">{s.tool}()</code>
                {i < shown && <span className="agent-sum">{s.summary}</span>}
              </li>
            ))}
          </ol>
          {done && (
            <>
              <p className="agent-headline">{inv.headline}</p>
              <div className="agent-qa">
                {inv.answers.map((a) => (
                  <div key={a.key} className={`agent-q${open === a.key ? ' open' : ''}`}>
                    <button type="button" aria-expanded={open === a.key} onClick={() => setOpen(open === a.key ? null : a.key)}>{a.question}</button>
                    {open === a.key && (
                      <div className="agent-a">
                        <p>{a.answer}</p>
                        <p className="agent-facts mono">grounded in: {a.facts.join(' · ')}</p>
                      </div>
                    )}
                  </div>
                ))}
              </div>
              <p className="lab-note faint">
                {inv.steps.length} backend calls completed in {total.toFixed(1)} ms, replayed in order. Deterministic: no language model.
                Read-only: the rule layer is the only diagnostic authority.
              </p>
            </>
          )}
        </>
      )}
    </section>
  );
}
