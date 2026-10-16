// Which evidence numbers may be shown (docs/EVAL_JSON.md, PROPOSED). The panel never shows a
// number without its context: a result missing any required field is "incomplete", and then
// none of its numbers (not even a bound) are shown. Nothing here computes or adjusts a result.

export const COLUMNS = {
  A: 'A · simulated',
  B: 'B · injected',
  C: 'C · real',
};

// column B (injected onto real audio) is additive faults only
export const COLUMN_B_ALLOWED = ['knock', 'valve_tick'];

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const isCount = (v) => Number.isInteger(v) && v >= 0;

// fields each kind needs before anything is shown
const REQUIRED = {
  rate: [['events', isCount], ['exposure_hours', (v) => isNum(v) && v > 0], ['per_hour', isNum], ['upper_95_per_hour', isNum]],
  proportion: [['successes', isCount], ['n', isCount], ['value', isNum], ['ci_95', (v) => Array.isArray(v) && v.length === 2 && v.every(isNum)]],
  metric: [['value', isNum], ['n', isCount]],
};

// → { ok, missing: [field names] }
export function checkResult(r) {
  if (!r || typeof r !== 'object') return { ok: false, missing: ['result'] };
  const missing = [];
  if (!(r.column in COLUMNS)) missing.push('column');
  if (typeof r.split !== 'string' || !r.split) missing.push('split');
  const req = REQUIRED[r.kind];
  if (!req) return { ok: false, missing: [...missing, `kind (got ${JSON.stringify(r.kind)})`] };
  req.forEach(([field, valid]) => { if (!valid(r[field])) missing.push(field); });
  return { ok: missing.length === 0, missing };
}

// the operator line is a projection: shown only with a value and its stated assumptions
export function projectionShown(p) {
  return !!p && isNum(p.value) && typeof p.assumptions === 'string' && p.assumptions.trim() !== '';
}
