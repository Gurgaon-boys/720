import { polarPoint } from './geometry.js';

// Crank-angle axes for a 720° polar plot: outer ring, a tick every 10°, a longer tick and a
// faint spoke every 90°, and degree labels. `skipLabels` leaves room for labels drawn elsewhere.
export default function PolarAxes({ cx, cy, r, inner = 0, labelR, fontSize = 11, stroke = '#2c3a4e', text = '#8e9aab', skipLabels = [] }) {
  const ticks = [];
  for (let deg = 0; deg < 720; deg += 10) {
    const major = deg % 90 === 0;
    const a = polarPoint(deg, r - (major ? 10 : 5), cx, cy);
    const b = polarPoint(deg, r, cx, cy);
    ticks.push(<line key={`t${deg}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={major ? text : stroke} strokeWidth="1" />);
    if (major) {
      const s = polarPoint(deg, inner, cx, cy);
      ticks.push(<line key={`s${deg}`} x1={s.x} y1={s.y} x2={a.x} y2={a.y} stroke={stroke} strokeOpacity="0.45" strokeWidth="1" />);
      if (!skipLabels.includes(deg)) {
        const l = polarPoint(deg, labelR, cx, cy);
        ticks.push(
          <text key={`l${deg}`} x={l.x} y={l.y} textAnchor="middle" dominantBaseline="middle"
            fontFamily="JetBrains Mono, monospace" fontSize={fontSize} fill={text}>
            {deg}°
          </text>,
        );
      }
    }
  }
  return (
    <g aria-hidden="true">
      <circle cx={cx} cy={cy} r={r} fill="none" stroke={stroke} />
      {ticks}
    </g>
  );
}
