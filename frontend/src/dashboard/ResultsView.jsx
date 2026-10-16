import { useEffect, useState } from 'react';
import { loadResult } from '../lib/resultsSource.js';
import FalseAlarms from './results/FalseAlarms.jsx';
import Projection from './results/Projection.jsx';
import DetectionTable from './results/DetectionTable.jsx';
import Tradeoff from './results/Tradeoff.jsx';
import Models from './results/Models.jsx';
import { ClogSweep, MisfireSweep } from './results/Sweeps.jsx';
import BeforeAfter from './results/BeforeAfter.jsx';
import OperatingMap, { RealCoverage } from './results/OperatingMap.jsx';
import Signature from './results/Signature.jsx';
import Latency from './results/Latency.jsx';
import InjectedAudio from './results/InjectedAudio.jsx';
import './results/results.css';

// Results tab: the team's committed results, drawn from eval/results/*.json (copied at dev/build).
// Order follows the demo story: real false alarms first, then the operator load, then detection
// on the simulator, then the operating point. Every chart keeps its column (A simulated / C real),
// split and counts beside it; simulated and real are never on one axis. Nothing is computed here
// except drawing: no new thresholds, no blended numbers.

const FILES = ['alarm_budget.json', 'valve_tick_cost.json', 'model_comparison.json', 'column_a.json', 'robustness_v2.json', 'clog_sweep.json',
  'operating_map.json', 'column_c_detail.json', 'soak_l3_long.json', 'table_b.json'];

export default function ResultsView() {
  const [files, setFiles] = useState({});
  useEffect(() => {
    let live = true;
    FILES.forEach((f) => loadResult(f).then((r) => {
      if (r.status === 'error') console.error('[720]', r.message);
      if (live) setFiles((prev) => ({ ...prev, [f]: r }));
    }));
    return () => { live = false; };
  }, []);

  return (
    <section className="dash results" aria-label="Results">
      <header className="rs-head">
        <h2 className="panel-label">RESULTS</h2>
        <p className="rs-head-note">
          The team's committed results. Real and simulated data are never mixed in one chart; every rate has its counts and a 95% bound (hover, or open the table).
        </p>
        <div className="rs-key-row" aria-label="Colour key">
          <span><i className="rs-dot" style={{ background: 'var(--rs-real)' }} />real data</span>
          <span><i className="rs-dot" />simulated</span>
          <span><i className="rs-dot" style={{ background: 'var(--rs-watch)' }} />watch</span>
          <span><i className="rs-dot alarm" />ALARM</span>
          <span><i className="rs-dot unclass" />unclassified</span>
          <span><i className="rs-dot" style={{ background: 'var(--rs-inj)' }} />injected into real audio</span>
        </div>
      </header>
      <FalseAlarms loaded={files['alarm_budget.json']} />
      <BeforeAfter real={files['column_c_detail.json']} sim={files['soak_l3_long.json']} />
      <Projection loaded={files['alarm_budget.json']} />
      <DetectionTable loaded={files['alarm_budget.json']} />
      <Tradeoff loaded={files['valve_tick_cost.json']} />
      <InjectedAudio loaded={files['table_b.json']} />
      <Models comparison={files['model_comparison.json']} columnA={files['column_a.json']} />
      <h3 className="rs-group">Across operating conditions</h3>
      <OperatingMap loaded={files['operating_map.json']} />
      <RealCoverage />
      <Signature loaded={files['robustness_v2.json']} />
      <Latency loaded={files['robustness_v2.json']} />
      <MisfireSweep loaded={files['robustness_v2.json']} />
      <ClogSweep loaded={files['clog_sweep.json']} />
    </section>
  );
}
