// PLACEHOLDER evidence. Not results.
// Stands in for eval.json from Diljit until it exists. Shape: docs/EVAL_JSON.md (PROPOSED, not
// yet agreed with him). Every value is null on purpose: we don't invent evidence numbers, and the
// evidence panel shows each null result as "incomplete".

const rate = (column, split, extra = {}) => ({
  kind: 'rate', events: null, exposure_hours: null, per_hour: null, upper_95_per_hour: null,
  bound_method: 'poisson_exact_one_sided_95', n_files: null, column, split, ...extra,
});
const metric = (column, split, extra = {}) => ({ kind: 'metric', value: null, unit: null, n: null, ci_95: null, column, split, ...extra });
const proportion = (column, split) => ({ kind: 'proportion', successes: null, n: null, value: null, ci_95: null, ci_method: 'wilson_95', column, split });

const fpLevels = (column, split, { audioOnly = false } = {}) => ({
  anomaly_flags: rate(column, split),
  advisories: rate(column, split),
  classifier_false_faults: rate(column, split),
  alarms: rate(column, split, audioOnly ? { structurally_limited: true } : {}),
});

export const mockEval = {
  eval_version: 'proposed-0.1',
  placeholder: true,
  generated_at: null,
  source_commit: null,

  false_positives: {
    // headline: real ECU, held-out carOBD files
    headline: { dataset: 'carOBD held-out files', ...fpLevels('C', 'held-out files') },
    // secondary: vetted AudioSet idle clips; alarms are structurally limited on audio-only data
    secondary: { dataset: 'AudioSet P3 (vetted car idle)', ...fpLevels('C', 'held-out pool P3', { audioOnly: true }) },
  },

  // shown only once Diljit supplies both value and assumptions
  operator_projection: { engines: 50, value: null, unit: 'alarms per hour', basis: null, assumptions: null, column: 'C', split: 'held-out files' },

  column_a: {
    split_gap: { random: metric('A', 'random'), grouped: metric('A', 'grouped by run') },
    model_comparison: ['Baseline', 'SVM', 'ANN', 'CNN'].map((model) => ({
      model, result: metric('A', 'grouped by run (identical across models)'), sd_across_seeds: null,
    })),
    held_out_class: {
      valve_tick: { auroc: metric('A', 'held-out class'), pauc: metric('A', 'held-out class') },
      knock: { auroc: metric('A', 'held-out class'), pauc: metric('A', 'held-out class') },
    },
    injector_detectability: { smallest_clog_at_fixed_far: metric('A', 'grouped by run') },
    latency_ms: metric('A', 'live pipeline', { unit: 'ms' }),
  },

  // column B (injected onto real audio) covers knock and valve tick only
  column_b: { knock: proportion('B', 'grouped by source clip'), valve_tick: proportion('B', 'grouped by source clip') },

  column_c: { obd_branch: metric('C', 'held-out carOBD files') },
};
