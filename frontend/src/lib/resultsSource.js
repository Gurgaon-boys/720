// Results tab data: the team's committed eval/results/*.json, copied into public/results/ by
// scripts/sync-results.mjs. Read once per file. A missing file is "not available" and a broken one
// is an error; neither is ever replaced with stand-in numbers.

const BASE = `${import.meta.env.BASE_URL}results/`;

// → { status: 'loaded' | 'missing' | 'error', data, message }
export async function loadResult(file) {
  let res;
  try {
    res = await fetch(`${BASE}${file}`, { cache: 'no-store' });
  } catch (e) {
    return { status: 'error', data: null, message: `${file} could not be fetched: ${e.message}` };
  }
  // a static server answers 404; the Vite dev server answers a missing file with the app's HTML page
  const type = res.headers.get('content-type') ?? '';
  if (res.status === 404 || (res.ok && type.includes('text/html'))) {
    return { status: 'missing', data: null, message: `${file} not available (run npm run dev or build to copy eval/results)` };
  }
  if (!res.ok) return { status: 'error', data: null, message: `${file}: HTTP ${res.status}` };
  try {
    return { status: 'loaded', data: await res.json(), message: null };
  } catch (e) {
    return { status: 'error', data: null, message: `${file} is not valid JSON: ${e.message}` };
  }
}
