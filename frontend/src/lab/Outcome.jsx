import { SENSOR_INFO } from './evidence.js';

// WHAT JUST HAPPENED (D138): the result of the judge's action, in one glance.
//   YOU REMOVED -> 720 LOST -> STILL WORKING -> RESULT
// Lost / kept come from the sensor dependency table; every RESULT line quotes the rule layer's broadcast.

const KEY_LINE = 'You took away a sensor, so 720 stopped claiming what it could no longer prove.';

export default function Outcome({ result, state, sensors }) {
  const d = state.decision;
  if (!result.removed.length) {
    const story = d.state === 'ALARM'
      ? { tone: 'red', big: `ALARM confirmed · ${d.fault?.replace('_', ' ')}${d.location ? ` · cylinder ${d.location}` : ''}`, sub: `Two independent witnesses agreed (${(d.witnesses ?? []).join(' + ')}). Now take a sensor away and watch what 720 can still prove.` }
      : d.state === 'ADVISORY'
        ? { tone: 'amber', big: `WATCH · ${d.fault ? d.fault.replace('_', ' ') : 'one witness'}`, sub: 'One witness sees it. The rule layer waits for a second, matching witness before it alarms.' }
        : d.state === 'UNCLASSIFIED'
          ? { tone: 'amber', big: 'UNCLASSIFIED', sub: 'Something is abnormal and 720 says it cannot name it, instead of guessing.' }
          : { tone: 'neutral', big: 'All sensors live · full evidence', sub: 'Inject a fault, wait for the confirmed alarm, then disable a sensor.' };
    return (
      <section className={`oc oc-idle tone-${story.tone}`} aria-label="What just happened" aria-live="polite">
        <span className="oc-kicker">WHAT JUST HAPPENED</span>
        <p className="oc-story-big">{story.big}</p>
        <p className="oc-story-sub">{story.sub}</p>
      </section>
    );
  }
  return (
    <section className="oc" aria-label="What just happened" aria-live="polite">
      <span className="oc-kicker">WHAT JUST HAPPENED</span>
      <div className="oc-flow">
        <div className="oc-col oc-removed">
          <h4>You removed</h4>
          {result.removed.map((ch) => (
            <div key={ch} className="oc-sensor">
              <span><span className="oc-x" aria-hidden="true">✕</span>{SENSOR_INFO[ch].short}</span>
              <small>{SENSOR_INFO[ch].name}</small>
              <small className="oc-conf">{sensors?.[ch]?.health === 'FAILURE' ? 'Offline confirmed by the watchdog' : 'Command accepted · awaiting watchdog (3 s)'}</small>
            </div>
          ))}
        </div>
        <span className="oc-arrow" aria-hidden="true">→</span>
        <div className="oc-col oc-lost">
          <h4>720 lost</h4>
          {result.lost.length ? result.lost.map((c) => (
            <div key={c.id} className="oc-item lost"><s>{c.name}</s><small>needs {c.because.join(' + ')}</small></div>
          )) : <div className="oc-item none">No diagnostic capability</div>}
          {result.held.map((c) => <div key={c.id} className="oc-item held">{c.name}<small>held from the last cam pulse</small></div>)}
        </div>
        <span className="oc-arrow" aria-hidden="true">→</span>
        <div className="oc-col oc-kept">
          <h4>Still working</h4>
          {result.kept.filter((c) => c.id !== 'phase').map((c) => <div key={c.id} className="oc-item kept">{c.name}</div>)}
        </div>
        <span className="oc-arrow" aria-hidden="true">→</span>
        <div className="oc-col oc-result">
          <h4>Result</h4>
          {result.verdicts.map((v) => (
            <div key={v.big} className={`oc-verdict tone-${v.tone}`}><strong>{v.big}</strong><small>{v.sub}</small></div>
          ))}
        </div>
      </div>
      {result.classifierGone && <p className="oc-key">{KEY_LINE}</p>}
    </section>
  );
}
