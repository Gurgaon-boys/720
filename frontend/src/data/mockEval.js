// PLACEHOLDER evidence. Not results.
// Stands in for eval.json from Diljit until its exact shape is agreed (CLAUDE.md §11).
// Every value is null on purpose: we don't invent evidence numbers. The structure follows the
// sections listed in docs/DATA_CONTRACT.md so the evidence panel (task 8) has something to bind to.
// Each number carries: value, n, column (A simulated / B injected / C real), split, and a bound.

const stat = (column, split, extra = {}) => ({ value: null, n: null, column, split, upper_95: null, ...extra });

const fpLevels = (column, split, { audioOnly = false } = {}) => ({
  anomaly_flags_per_hour: stat(column, split),
  advisories_per_hour: stat(column, split),
  classifier_false_faults_per_hour: stat(column, split),
  alarms_per_hour: stat(column, split, audioOnly ? { structurally_limited: true } : {}),
});

export const mockEval = {
  PLACEHOLDER: true,
  note: 'PLACEHOLDER: not results. Replace with eval.json from Diljit.',

  false_positives: {
    // headline: real ECU, held-out carOBD files
    headline: { dataset: 'carOBD held-out files', ...fpLevels('C', 'held-out files') },
    // secondary: vetted AudioSet idle clips; alarms are structurally limited on audio-only data
    secondary: { dataset: 'AudioSet P3 (vetted car idle)', ...fpLevels('C', 'held-out pool P3', { audioOnly: true }) },
  },

  operator_scaling: { engines: 50, alarms_per_hour: null, column: 'C', split: 'held-out files' },

  split_gap: { random: stat('A', 'random'), grouped: stat('A', 'grouped') },

  model_comparison: ['Baseline', 'SVM', 'ANN', 'CNN'].map((model) => ({
    model,
    ...stat('A', 'grouped (identical across models)'),
    sd_across_seeds: null,
  })),

  sensor_ablation: [], // per-fault, per-sensor rows

  held_out_class: {
    valve: { auroc: stat('A', 'held-out class'), pauc: stat('A', 'held-out class') },
    knock: { auroc: stat('A', 'held-out class'), pauc: stat('A', 'held-out class') },
  },

  injector_detectability: {
    smallest_clog_at_fixed_far: stat('A', 'grouped'),
    score_vs_severity: [], // [{ severity, score }]
  },

  // column B (injected) covers knock and valve tick only
  column_b: { knock: stat('B', 'injected'), valve_tick: stat('B', 'injected') },

  obd_branch: stat('C', 'held-out carOBD files'),

  latency_ms: stat('A', 'live pipeline'),
};
