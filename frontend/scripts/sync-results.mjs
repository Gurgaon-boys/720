// Copies the team's committed result files (eval/results/*.json) into public/results/ so the
// Results tab can read them, in dev and in the build. Runs before `npm run dev` and `npm run build`.
// The copies are git-ignored: eval/results/ stays the single source, nothing is hand-copied.
// A missing file is reported here and shown as "not available" in the tab, never filled in.

import { copyFileSync, existsSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const SRC = join(here, '..', '..', 'eval', 'results');
const DST = join(here, '..', 'public', 'results');

export const RESULT_FILES = [
  'alarm_budget.json',
  'valve_tick_cost.json',
  'model_comparison.json',
  'column_a.json',
  'robustness_v2.json',
  'clog_sweep.json',
  'operating_map.json',
  'column_c_detail.json',
  'soak_l3_long.json',
];

mkdirSync(DST, { recursive: true });
const missing = [];
for (const f of RESULT_FILES) {
  const from = join(SRC, f);
  if (existsSync(from)) copyFileSync(from, join(DST, f));
  else missing.push(f);
}
console.log(`[sync-results] ${RESULT_FILES.length - missing.length}/${RESULT_FILES.length} result files copied from eval/results/`);
if (missing.length) console.warn(`[sync-results] not found (the Results tab will say "not available"): ${missing.join(', ')}`);
