// Evidence for the evidence panel: eval.json from Diljit, read once from the app's own folder
// (public/eval.json → dist/eval.json), so it works offline on the demo laptop.
// Shape: docs/EVAL_JSON.md (PROPOSED). Until the file exists, the panel shows the PLACEHOLDER
// structure from data/mockEval.js and says so; any other failure is reported, not hidden.

import { mockEval } from '../data/mockEval.js';

const URL = `${import.meta.env.BASE_URL}eval.json`;

// → { status: 'loaded' | 'missing' | 'error', data, message }
export async function loadEval() {
  let res;
  try {
    res = await fetch(URL, { cache: 'no-store' });
  } catch (e) {
    return { status: 'error', data: mockEval, message: `eval.json could not be fetched: ${e.message}` };
  }
  // a static server answers 404; the Vite dev server answers a missing file with the app's HTML page
  const type = res.headers.get('content-type') ?? '';
  if (res.status === 404 || (res.ok && type.includes('text/html'))) {
    return { status: 'missing', data: mockEval, message: 'eval.json not available: placeholder structure, no results' };
  }
  if (!res.ok) {
    return { status: 'error', data: mockEval, message: `eval.json: HTTP ${res.status}` };
  }
  try {
    return { status: 'loaded', data: await res.json(), message: null };
  } catch (e) {
    return { status: 'error', data: mockEval, message: `eval.json is not valid JSON: ${e.message}` };
  }
}
