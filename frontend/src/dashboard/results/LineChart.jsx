import { useState } from 'react';

// Small SVG line chart for the Results tab: linear x, y = a share (0–1), one line per series,
// optional 95 % interval whiskers per point, distinct marker shapes (identity is never colour
// alone), end-of-line labels pushed apart, and a hover/focus tooltip with the exact numbers.
// It only draws what it is given.

export function Marker({ shape, x, y, color, r = 4 }) {
  if (shape === 'square') return <rect x={x - r} y={y - r} width={2 * r} height={2 * r} rx="1" fill={color} stroke="var(--panel)" strokeWidth="2" />;
  if (shape === 'diamond') return <path d={`M${x},${y - r - 1} L${x + r + 1},${y} L${x},${y + r + 1} L${x - r - 1},${y} Z`} fill={color} stroke="var(--panel)" strokeWidth="2" />;
  if (shape === 'triangle') return <path d={`M${x},${y - r - 1} L${x + r + 1},${y + r} L${x - r - 1},${y + r} Z`} fill={color} stroke="var(--panel)" strokeWidth="2" />;
  return <circle cx={x} cy={y} r={r} fill={color} stroke="var(--panel)" strokeWidth="2" />;
}

export default function LineChart({
  series, xDomain, xTicks, yTitle, xTitle, width = 520, height = 260, compact = false, labelEnds = true, ariaLabel,
}) {
  const [tip, setTip] = useState(null);
  const M = compact ? { l: 34, r: 10, t: 10, b: 30 } : { l: 52, r: labelEnds ? 120 : 16, t: 14, b: xTitle ? 56 : 40 };
  const xOf = (v) => M.l + ((v - xDomain[0]) / (xDomain[1] - xDomain[0])) * (width - M.l - M.r);
  const yOf = (p) => M.t + (1 - p) * (height - M.t - M.b);
  const yTicks = compact ? [0, 0.5, 1] : [0, 0.25, 0.5, 0.75, 1];

  const ends = labelEnds && !compact
    ? series.filter((s) => s.points.length).map((s) => {
      const last = s.points.reduce((a, b) => (b.x > a.x ? b : a));
      return { s, x: xOf(last.x) + 10, y: yOf(last.y) };
    }).sort((a, b) => a.y - b.y)
    : [];
  ends.forEach((e, i) => { if (i > 0 && e.y - ends[i - 1].y < 15) e.y = ends[i - 1].y + 15; });

  // x labels: skip one that would overlap the last label kept (the table and hover keep every value)
  const shownTicks = [];
  let lastMain = -Infinity;
  let lastSub = -Infinity;
  xTicks.forEach((t) => {
    const x = xOf(t.v);
    const showMain = x - lastMain >= 30;
    const showSub = Boolean(t.sub) && showMain && x - lastSub >= 54;
    if (showMain) lastMain = x;
    if (showSub) lastSub = x;
    shownTicks.push({ ...t, x, showMain, showSub });
  });

  return (
    <div className="rs-svgwrap" onMouseLeave={() => setTip(null)}>
      <svg viewBox={`0 0 ${width} ${height}`} className="rs-svg" role="img" aria-label={ariaLabel}>
        {yTicks.map((p) => (
          <g key={p}>
            <line x1={M.l} x2={width - M.r} y1={yOf(p)} y2={yOf(p)} className="rs-grid" />
            <text x={M.l - 6} y={yOf(p)} className="rs-axis" textAnchor="end" dominantBaseline="middle">{Math.round(p * 100)}%</text>
          </g>
        ))}
        {shownTicks.map((t) => (
          <g key={t.v}>
            <line x1={t.x} x2={t.x} y1={height - M.b} y2={height - M.b + 4} className="rs-grid" />
            {t.showMain && <text x={t.x} y={height - M.b + 15} className="rs-axis" textAnchor="middle">{t.label}</text>}
            {t.showSub && <text x={t.x} y={height - M.b + 28} className="rs-axis rs-axis-sub" textAnchor="middle">{t.sub}</text>}
          </g>
        ))}
        {xTitle && <text x={(M.l + width - M.r) / 2} y={height - 8} className="rs-axis-title" textAnchor="middle">{xTitle}</text>}
        {yTitle && !compact && (
          <text x={12} y={(M.t + height - M.b) / 2} className="rs-axis-title" textAnchor="middle" transform={`rotate(-90 12 ${(M.t + height - M.b) / 2})`}>{yTitle}</text>
        )}

        {series.map((s) => (
          <g key={s.key}>
            {s.points.map((p) => (Array.isArray(p.ci) ? (
              <line key={`ci-${p.x}`} x1={xOf(p.x)} x2={xOf(p.x)} y1={yOf(p.ci[0])} y2={yOf(p.ci[1])} stroke={s.color} strokeOpacity="0.55" strokeWidth="1.5" />
            ) : null))}
            <polyline points={s.points.map((p) => `${xOf(p.x)},${yOf(p.y)}`).join(' ')} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" />
            {s.points.map((p) => <Marker key={p.x} shape={s.shape} x={xOf(p.x)} y={yOf(p.y)} color={s.color} r={compact ? 3 : 4} />)}
          </g>
        ))}
        {ends.map((e) => <text key={e.s.key} x={e.x} y={e.y} className="rs-direct" dominantBaseline="middle">{e.s.label}</text>)}

        {series.flatMap((s) => s.points.map((p) => (
          <circle key={`hit-${s.key}-${p.x}`} cx={xOf(p.x)} cy={yOf(p.y)} r={compact ? 8 : 11} fill="transparent" tabIndex={0}
            aria-label={p.tip.replace(/\n/g, ', ')}
            onMouseEnter={() => setTip({ x: xOf(p.x), y: yOf(p.y), text: p.tip })} onFocus={() => setTip({ x: xOf(p.x), y: yOf(p.y), text: p.tip })} />
        )))}
      </svg>
      {tip && (
        <div className="rs-tip mono" style={{ left: `${(tip.x / width) * 100}%`, top: `${(tip.y / height) * 100}%` }}>
          {tip.text.split('\n').map((line) => <div key={line}>{line}</div>)}
        </div>
      )}
    </div>
  );
}
