import { useState } from 'react';
import { fmtSig } from '../../lib/format.js';
import ChartFrame, { SourceTag, contextLine } from './ChartFrame.jsx';

// Operating map: where healthy running stays quiet, per RPM x load cell of the 20 h healthy soak
// (SIM 2.1). Cell colour = false UNCLASSIFIED episodes per engine-hour (every one is a false alert:
// no fault is present), on one fixed scale for both settings so the toggle compares truthfully.
// The binning and bounds are done in eval/operating_map.py; this only draws them. It is not an
// efficiency map: the soak records no fuel use. Real carOBD coverage has its own panel, since its
// load is in % of calculated load (not N·m) and nothing in eval/results describes it yet.

// fixed steps (episodes per engine-hour); 0 has its own neutral swatch
const STEPS = [
  { max: 1, color: '#1f3a63', ink: 'var(--text)' },
  { max: 5, color: '#2c5ea8', ink: 'var(--text)' },
  { max: 25, color: '#4a86d8', ink: '#0e131c' },
  { max: 100, color: '#8fb7f0', ink: '#0e131c' },
  { max: Infinity, color: '#d6e5fb', ink: '#0e131c' },
];
const ZERO = { color: 'rgba(255,255,255,0.04)', ink: 'var(--text-2)' };
const swatch = (rate) => (rate === 0 ? ZERO : STEPS.find((s) => rate <= s.max));
const MODES = [
  ['after', 'Committed setting'],
  ['before', 'Before the fix'],
];

const W = 640;
const H = 340;
const M = { l: 74, r: 22, t: 8, b: 48 };

export default function OperatingMap({ loaded }) {
  const [mode, setMode] = useState('after');
  const [tip, setTip] = useState(null);
  const d = loaded?.data;
  const rpmE = d?.rpm_edges ?? [];
  const loadE = d?.load_nm_edges ?? [];
  const cells = d?.cells ?? [];
  const cw = (W - M.l - M.r) / Math.max(1, rpmE.length - 1);
  const ch = (H - M.t - M.b) / Math.max(1, loadE.length - 1);
  const col = (c) => rpmE.indexOf(c.rpm[0]);
  const row = (c) => loadE.length - 2 - loadE.indexOf(c.load_nm[0]); // heavy load at the top

  const total = cells.reduce((a, c) => ({ runs: a.runs + c.runs, h: a.h + c.engine_hours, k: a.k + (c[mode]?.count ?? 0) }), { runs: 0, h: 0, k: 0 });
  const quiet = cells.filter((c) => c[mode]?.count === 0);
  const quietBounds = quiet.map((c) => c[mode].upper95_per_hour);
  const hours = cells.map((c) => c.engine_hours);

  const table = (
    <table className="mono">
      <thead><tr><th scope="col">rpm</th><th scope="col">load N·m</th><th scope="col">runs</th><th scope="col">engine-h</th>
        {MODES.map(([k, n]) => <th key={k} scope="col">{n}: episodes · /h · 95% upper · runs with any</th>)}</tr></thead>
      <tbody>
        {cells.map((c) => (
          <tr key={`${c.rpm[0]}-${c.load_nm[0]}`}>
            <th scope="row">{c.rpm.join('–')}</th><td>{c.load_nm.join('–')}</td><td>{c.runs}</td><td>{fmtSig(c.engine_hours)}</td>
            {MODES.map(([k]) => <td key={k}>{c[k].count} · {fmtSig(c[k].per_hour)} · ≤ {fmtSig(c[k].upper95_per_hour)} · {c[k].runs_with_any}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <ChartFrame
      title="Operating sweet spot: where healthy engines raise no false alerts"
      lede="Each cell is an RPM × load range of healthy simulated engines. Colour = false alerts per engine-hour: darker means fewer, and an empty cell means none were seen. This measures how trustworthy the monitor is at each operating point, not fuel efficiency."
      file="operating_map.json"
      loaded={loaded}
      column={d?.column}
      tagNote={d ? `${total.runs} runs · ${fmtSig(total.h)} engine-h` : null}
      about={(
        <>
          <p>{contextLine([d?.label, d && `sim ${d.sim_version}`, d?.split, d && `${d.run_s_counted} s counted per run, one operating point per run`])}</p>
          <p>Metric: {d?.metric}. Bound: {d?.bound_method}. Episodes inside one run come from one engine, so where a single run holds most of a cell's episodes the bound is too narrow; "runs with any" is in the hover and table for that reason.</p>
          <p>Settings: committed = {d?.configs?.after}; before = {d?.configs?.before}.</p>
          <p>Not measured: {d?.not_measured}. Derived by {d?.produced_by} from {d?.derived_from?.file} ({d?.derived_from?.created_at?.slice(0, 10)}).</p>
        </>
      )}
      table={table}
    >
      <div className="rs-toggle" role="group" aria-label="Rule setting">
        {MODES.map(([k, n]) => (
          <button key={k} type="button" className={`rs-toggle-btn${mode === k ? ' on' : ''}`} aria-pressed={mode === k} onClick={() => setMode(k)}>{n}</button>
        ))}
      </div>
      <div className="rs-map-row">
        <div className="rs-svgwrap rs-map" onMouseLeave={() => setTip(null)}>
          <svg viewBox={`0 0 ${W} ${H}`} className="rs-svg" role="img" aria-label={`False alerts per engine-hour by RPM and load, ${mode === 'after' ? 'committed setting' : 'before the fix'}`}>
            {cells.map((c) => {
              const r = c[mode];
              const s = swatch(r.per_hour);
              const x = M.l + col(c) * cw;
              const y = M.t + row(c) * ch;
              const show = () => setTip({
                x: x + cw / 2, y: y + ch / 2,
                text: `${c.rpm.join('–')} rpm · ${c.load_nm.join('–')} N·m\n${r.count} episodes in ${fmtSig(c.engine_hours)} engine-h (${c.runs} runs)\n${fmtSig(r.per_hour)} /h · 95% upper ${fmtSig(r.upper95_per_hour)} /h\nruns with any: ${r.runs_with_any} of ${c.runs}`,
              });
              return (
                <g key={`${c.rpm[0]}-${c.load_nm[0]}`} tabIndex={0} onMouseEnter={show} onFocus={show}
                  aria-label={`${c.rpm.join(' to ')} rpm, ${c.load_nm.join(' to ')} newton metres: ${r.count} episodes in ${fmtSig(c.engine_hours)} engine-hours`}>
                  <rect x={x + 1} y={y + 1} width={cw - 2} height={ch - 2} rx="4" fill={s.color} />
                  <text x={x + cw / 2} y={y + ch / 2 - 6} textAnchor="middle" className="rs-cell-main" style={{ fill: s.ink }}>
                    {r.count === 0 ? 'none' : `${fmtSig(r.per_hour)} /h`}
                  </text>
                  <text x={x + cw / 2} y={y + ch / 2 + 12} textAnchor="middle" className="rs-cell-sub" style={{ fill: s.ink }}>
                    {r.count} in {fmtSig(c.engine_hours)} h
                  </text>
                </g>
              );
            })}
            {rpmE.map((e, i) => (
              <text key={`x${e}`} x={M.l + i * cw} y={H - M.b + 16} textAnchor="middle" className="rs-axis">{e}</text>
            ))}
            {loadE.map((e, i) => (
              <text key={`y${e}`} x={M.l - 8} y={M.t + (loadE.length - 1 - i) * ch} textAnchor="end" dominantBaseline="middle" className="rs-axis">{e}</text>
            ))}
            <text x={(M.l + W - M.r) / 2} y={H - 8} textAnchor="middle" className="rs-axis-title">engine speed (rpm)</text>
            <text x={14} y={(M.t + H - M.b) / 2} textAnchor="middle" className="rs-axis-title" transform={`rotate(-90 14 ${(M.t + H - M.b) / 2})`}>load (N·m)</text>
          </svg>
          {tip && (
            <div className="rs-tip mono" style={{ left: `${(tip.x / W) * 100}%`, top: `${(tip.y / H) * 100}%` }}>
              {tip.text.split('\n').map((line) => <div key={line}>{line}</div>)}
            </div>
          )}
        </div>
        <aside className="rs-map-side">
          <p className="eyebrow">Scale · episodes per engine-hour</p>
          <ul className="rs-scale">
            <li><i style={{ background: ZERO.color }} />none seen</li>
            {STEPS.map((s, i) => <li key={s.max}><i style={{ background: s.color }} />{i === 0 ? '≤ 1' : s.max === Infinity ? `> ${STEPS[i - 1].max}` : `${STEPS[i - 1].max}–${s.max}`}</li>)}
          </ul>
          <p className="eyebrow">Coverage</p>
          <p className="rs-note">{total.runs} healthy runs, {fmtSig(total.h)} engine-h; {fmtSig(Math.min(...hours))}–{fmtSig(Math.max(...hours))} engine-h per cell.</p>
          <p className="eyebrow">This setting</p>
          <p className="rs-note">
            {total.k} episodes in total. {quiet.length} of {cells.length} cells saw none
            {quiet.length ? `; with about an hour per cell, "none" is still only bounded at ≤ ${fmtSig(Math.min(...quietBounds))}–${fmtSig(Math.max(...quietBounds))} /h.` : '.'}
          </p>
        </aside>
      </div>
    </ChartFrame>
  );
}

// Real carOBD coverage: where the real car ran. Nothing in eval/results describes it yet, so it says
// so instead of drawing anything. Kept separate from the simulated map (different load unit).
export function RealCoverage() {
  return (
    <section className="panel rs-frame" aria-label="Real operating coverage">
      <div className="rs-frame-head">
        <h3 className="rs-title">Where the real car ran</h3>
        <SourceTag column="C">carOBD</SourceTag>
      </div>
      <p className="rs-lede">
        The real evidence (carOBD, 2014 Toyota Etios) would show here as hours per RPM × engine-load range. It shows where real data exists, not which region is best.
      </p>
      <p className="panel-note" role="status">
        Results pending: no file in eval/results gives the carOBD time per RPM × load range yet, and the raw logs are not on this machine. carOBD reports load as % of calculated load (not N·m), so it will get its own axes, never the simulated grid.
      </p>
    </section>
  );
}
