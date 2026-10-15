// Readout formatting. Backend values are raw floats (e.g. 2394.8 rpm); show them with a sensible
// number of digits for their size: whole numbers from 100 up, then 1 or 2 decimals.
export function fmtNum(v) {
  if (v == null || Number.isNaN(v)) return '—';
  const a = Math.abs(v);
  const digits = a >= 100 ? 0 : a >= 10 ? 1 : 2;
  return v.toLocaleString(undefined, { maximumFractionDigits: digits });
}
