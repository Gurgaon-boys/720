import { useEffect, useState } from 'react';
import { loadResult } from '../lib/resultsSource.js';
import FalseAlarms from './results/FalseAlarms.jsx';
import Projection from './results/Projection.jsx';
import DetectionTable from './results/DetectionTable.jsx';
import Tradeoff from './results/Tradeoff.jsx';
import './results/results.css';

// Results tab: the team's committed results, drawn from eval/results/*.json (copied at dev/build).
// Order follows the demo story: real false alarms first, then the operator load, then detection
// on the simulator, then the operating point. Every chart keeps its column (A simulated / C real),
// split and counts beside it; simulated and real are never on one axis. Nothing is computed here
// except drawing: no new thresholds, no blended numbers.

const FILES = ['alarm_budget.json', 'valve_tick_cost.json'];

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
          From the team's committed result files (eval/results). Column A = simulated, C = real; they are never mixed on one axis.
          Every rate carries its counts and a 95% bound.
        </p>
      </header>

      <h3 className="ev-h">1 · False alarms on healthy engines</h3>
      <FalseAlarms loaded={files['alarm_budget.json']} />
      <Projection loaded={files['alarm_budget.json']} />

      <h3 className="ev-h">2 · Detection on the simulator</h3>
      <DetectionTable loaded={files['alarm_budget.json']} />

      <h3 className="ev-h">3 · The operating point</h3>
      <Tradeoff loaded={files['valve_tick_cost.json']} />
    </section>
  );
}
