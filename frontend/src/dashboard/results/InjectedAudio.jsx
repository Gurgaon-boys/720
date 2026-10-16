import { fmtSig } from '../../lib/format.js';
import ChartFrame, { contextLine } from './ChartFrame.jsx';

// Column B: simulated knock and valve tick added onto REAL engine recordings (eval/table_b.json,
// Diljit, method pre-registered in DECISIONS D97, results D98). The recordings are real; the fault
// sound is simulated. Shown as the file reports it: the pre-registered seed 0, severities pooled,
// at each injection level. Bars are neutral grey (neither real nor simulated); whisker = Wilson 95 %.
// Knock detection is drawn against the knock calls on HEALTHY windows of the same clips, which is
// what "chance" looks like here. The post-hoc louder levels (table_b_levels_posthoc.json) are not drawn.

const LEVELS = ['+0dB', '+6dB', '+12dB'];
const pct = (v) => `${Math.max(0, Math.min(100, v * 100))}%`;
const share = (r) => `${fmtSig(r.rate * 100)}%`;

function Row({ label, r, kind = 'inj' }) {
  if (!r) return <div className="rs-row"><span className="rs-row-label">{label}</span><span className="rs-row-na">not in the file</span></div>;
  return (
    <div className="rs-row" title={`${r.k} of ${r.n} windows · Wilson 95% ${fmtSig(r.wilson95?.[0])}–${fmtSig(r.wilson95?.[1])}`}>
      <span className="rs-row-label">{label}</span>
      <span className="rs-track" aria-hidden="true">
        <span className={`rs-bar ${kind}`} style={{ width: pct(r.rate) }} />
        {Array.isArray(r.wilson95) && <span className="rs-whisker-sym" style={{ left: pct(r.wilson95[0]), width: `calc(${pct(r.wilson95[1])} - ${pct(r.wilson95[0])})` }} />}
      </span>
      <span className="rs-row-val mono">{share(r)}<span className="rs-dim"> · {r.k}/{r.n}</span></span>
    </div>
  );
}

export default function InjectedAudio({ loaded }) {
  const d = loaded?.data;
  const s0 = d?.per_seed?.['0'];
  const fp = s0?.knock_false_positive_on_healthy;
  const knock = (lv) => s0?.knock_detection?.[`${lv}_ppooled`];
  const valve = (lv) => s0?.valve_tick?.[`${lv}_spooled`];

  const table = (
    <table className="mono">
      <thead><tr><th scope="col">level</th><th scope="col">knock detected</th><th scope="col">valve tick → unknown</th><th scope="col">valve tick → called knock</th></tr></thead>
      <tbody>
        {LEVELS.map((lv) => (
          <tr key={lv}><th scope="row">{lv}</th>
            <td>{knock(lv) ? `${knock(lv).k}/${knock(lv).n} (${share(knock(lv))})` : '—'}</td>
            <td>{valve(lv)?.unknown ? `${valve(lv).unknown.k}/${valve(lv).unknown.n} (${share(valve(lv).unknown)})` : '—'}</td>
            <td>{valve(lv)?.mislabelled_knock ? `${valve(lv).mislabelled_knock.k}/${valve(lv).mislabelled_knock.n} (${share(valve(lv).mislabelled_knock)})` : '—'}</td>
          </tr>
        ))}
        {fp && <tr><th scope="row">healthy windows</th><td colSpan={3}>knock called on {fp.k}/{fp.n} ({share(fp)})</td></tr>}
        {d?.across_seeds?.knock_detection_headline && (
          <tr><th scope="row">seeds 0–2</th><td colSpan={3}>knock detection at 0 dB {d.across_seeds.knock_detection_headline.per_seed.join(' / ')} (mean {fmtSig(d.across_seeds.knock_detection_headline.mean)})</td></tr>
        )}
      </tbody>
    </table>
  );

  return (
    <ChartFrame
      title="Faults added to real engine recordings"
      lede="The recordings are real; the fault sound added to them is simulated. This is the hardest test we ran, and the result is weak: it shows the gap between the simulator and real audio."
      file="table_b.json"
      srcPath="eval/table_b.json"
      loaded={loaded}
      column={d ? 'B' : undefined}
      tagNote={d ? `${d.base?.clips} real clips` : null}
      about={(
        <>
          <p>{contextLine([d?.column, d?.base && `${d.base.pool}, ≥ ${d.base.min_bandwidth_hz / 1000} kHz bandwidth`, d?.split, d?.base?.windows_per_version && `windows ${d.base.windows_per_version}`])}.</p>
          <p>Injection: {d?.injection?.model}; levels {d?.injection?.levels_db_re_clip_5_15k_rms?.join(' / ')} dB above the clip's 5–15 kHz loudness; severities {d?.injection?.severities?.join(', ')} pooled. Shown: seed 0, the pre-registered headline (DECISIONS D97). Knock is a trained class; valve tick was never trained, so the right answer for it is "unknown".</p>
          <p>Team diagnosis (D98): a 1 ms burst this quiet barely moves any feature, so the knock classifier is at chance, and valve tick on real audio is an open-set failure, unlike on simulator features. Louder levels were tried after the run (post-hoc, eval/table_b_levels_posthoc.json) and are not drawn here.</p>
        </>
      )}
      table={table}
    >
      <p className="rs-inj-banner"><strong>INJECTED INTO REAL AUDIO</strong> · real recordings, simulated fault sound</p>
      <div className="rs-multiples">
        <div className="rs-multiple rs-card inj">
          <p className="rs-mtitle"><strong>Knock (trained)</strong> <span className="rs-dim">share of windows called knock</span></p>
          {LEVELS.map((lv) => <Row key={lv} label={`injected ${lv.replace('dB', ' dB')}`} r={knock(lv)} />)}
          <Row label="healthy, nothing added" r={fp} kind="muted" />
          {fp && knock('+0dB') && (
            <p className="rs-callout">At 0 dB, knock is called on <strong>{share(knock('+0dB'))}</strong> of injected windows and <strong>{share(fp)}</strong> of healthy ones: no better than chance.</p>
          )}
        </div>
        <div className="rs-multiple rs-card inj">
          <p className="rs-mtitle"><strong>Valve tick (never trained)</strong> <span className="rs-dim">right answer: unknown</span></p>
          {LEVELS.map((lv) => (
            <div key={lv} className="rs-pair">
              <Row label={`${lv.replace('dB', ' dB')} · unknown`} r={valve(lv)?.unknown} />
              <Row label={`${lv.replace('dB', ' dB')} · called knock`} r={valve(lv)?.mislabelled_knock} kind="muted" />
            </div>
          ))}
          {valve('+0dB')?.unknown && (
            <p className="rs-callout">Valve tick is flagged unknown in only <strong>{share(valve('+0dB').unknown)}</strong> of windows at 0 dB and called knock in about half: a concrete gap between simulator and real audio (D98).</p>
          )}
        </div>
      </div>
    </ChartFrame>
  );
}
