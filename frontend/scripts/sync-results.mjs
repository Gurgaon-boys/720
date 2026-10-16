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
// committed result files that live directly in eval/ (published path -> source under eval/)
export const EVAL_FILES = {
  'table_b.json': 'table_b.json',
};

mkdirSync(DST, { recursive: true });
const missing = [];
for (const f of RESULT_FILES) {
  const from = join(SRC, f);
  if (existsSync(from)) copyFileSync(from, join(DST, f));
  else missing.push(f);
}
for (const [to, from] of Object.entries(EVAL_FILES)) {
  const src = join(SRC, '..', from);
  if (existsSync(src)) copyFileSync(src, join(DST, to));
  else missing.push(`eval/${from}`);
}
const total = RESULT_FILES.length + Object.keys(EVAL_FILES).length;
console.log(`[sync-results] ${total - missing.length}/${total} result files copied from eval/results/ and eval/`);
if (missing.length) console.warn(`[sync-results] not found (the Results tab will say "not available"): ${missing.join(', ')}`);
