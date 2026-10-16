// The three-step story of the lab, with the current step lit from backend state only:
//   1 Inject a fault   (no injection active)
//   2 Take a sensor away   (a fault is injected, every sensor on)
//   3 Bring it back   (a sensor is switched off or recovering)

const STEPS = [
  { n: 1, title: 'Inject a fault', sub: 'Two witnesses confirm an ALARM' },
  { n: 2, title: 'Take a sensor away', sub: 'Watch 720 lose evidence, not its honesty' },
  { n: 3, title: 'Bring it back', sub: 'Evidence returns only with real data' },
];

export function currentStep(state, sensors) {
  const anyCut = Object.values(sensors).some((s) => s.commanded || ['OFFLINE', 'FAILURE', 'RECOVERING'].includes(s.status));
  if (anyCut) return 3;
  if (state?.injection?.active) return 2;
  return 1;
}

export default function Guide({ step }) {
  return (
    <ol className="guide" aria-label="Demo steps">
      {STEPS.map((s) => (
        <li key={s.n} className={s.n === step ? 'on' : s.n < step ? 'done' : ''} aria-current={s.n === step ? 'step' : undefined}>
          <span className="guide-n">{s.n < step ? '✓' : s.n}</span>
          <span className="guide-text"><strong>{s.title}</strong><small>{s.sub}</small></span>
        </li>
      ))}
    </ol>
  );
}
