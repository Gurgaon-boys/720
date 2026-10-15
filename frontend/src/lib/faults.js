// One place for fault names and colours so every screen agrees.
// Fault labels follow schema 1.3: healthy | misfire | knock | injector_clog | valve_tick.
export const FAULTS = {
  // ISA-101: normal is grey, never green
  healthy: { name: 'Healthy', short: 'OK', color: '#8e9aab', title: () => 'Running clean', sub: 'All cylinders firing evenly' },
  misfire: { name: 'Misfire', short: 'NO FIRE', color: '#ffa94d', title: (c) => (c ? `Misfire · Cyl ${c}` : 'Misfire'), sub: 'Combustion missing on one cylinder' },
  knock: { name: 'Knock', short: 'KNOCK', color: '#ff5a6e', title: (c) => (c ? `Knock · Cyl ${c}` : 'Knock'), sub: 'Pressure ringing just after top dead centre' },
  injector_clog: { name: 'Injector clog', short: 'CLOG', color: '#f783ac', title: (c) => (c ? `Injector clog · Cyl ${c}` : 'Injector clog'), sub: 'Less fuel delivered; the ECU trims to compensate' },
  valve_tick: { name: 'Valve tick', short: 'TICK', color: '#b197fc', title: (c) => (c ? `Valve-train · Cyl ${c}` : 'Valve-train wear'), sub: 'Extra impacts at valve-closing angles' },
};

export function fault(label) {
  return FAULTS[label] || FAULTS.healthy;
}

// decision.state labels (ISA-18.2 wording: only ALARM is an alarm; ADVISORY is a watch).
// Colours are provisional until the ISA-101 restyle (task 2).
export const DECISION_STATES = {
  NORMAL: { name: 'Normal', color: '#8e9aab' },
  ADVISORY: { name: 'Watch', color: '#ffd43b' },
  ALARM: { name: 'Alarm', color: null }, // takes the fault colour
  // saturated magenta: distinct from normal grey and from every (pastel) fault colour
  UNCLASSIFIED: { name: 'Abnormal, unclassified: inspect', color: '#ff40e6' },
};

// One wording for every decision, shared by the banner, the decision panel and the intro so they
// can't drift apart. ADVISORY can arrive with `fault: null` when the classifier and the physics rule
// name different faults (rule layer: no alarm on a conflict).
//   kind   banner tag          title  headline            panelTitle  decision-panel headline
//   tag    short label (intro card)                       sub         one-line explanation
export function decisionText(d) {
  const f = fault(d.fault);
  if (d.state === 'ALARM') {
    return { kind: 'ALARM', title: f.title(d.location), panelTitle: f.title(d.location), tag: f.short, sub: f.sub };
  }
  if (d.state === 'ADVISORY') {
    if (!d.fault) {
      const disagree = d.witnesses.length === 2;
      const title = disagree ? 'Witnesses disagree' : 'Watch';
      return {
        kind: 'WATCH', title, panelTitle: title, tag: disagree ? 'WATCH · DISAGREE' : 'WATCH',
        sub: disagree ? 'Classifier and physics rule name different faults. Not an alarm.' : 'One witness only. Not an alarm.',
      };
    }
    return { kind: 'WATCH', title: f.title(d.location), panelTitle: `Watch · ${f.title(d.location)}`, tag: `WATCH · ${f.short}`, sub: 'One witness only. Not an alarm.' };
  }
  if (d.state === 'UNCLASSIFIED') {
    return { kind: 'INSPECT', title: DECISION_STATES.UNCLASSIFIED.name, panelTitle: DECISION_STATES.UNCLASSIFIED.name, tag: 'UNCLASSIFIED', sub: 'The classifiers cannot name this pattern.' };
  }
  return { kind: 'NORMAL', title: DECISION_STATES.NORMAL.name, panelTitle: DECISION_STATES.NORMAL.name, tag: 'OK', sub: 'No rule or classifier witness.' };
}

// Colour for the current decision: the fault colour on ALARM, the state colour otherwise.
export function decisionColor(decision) {
  if (!decision) return DECISION_STATES.NORMAL.color;
  if (decision.state === 'ALARM') return fault(decision.fault).color;
  return DECISION_STATES[decision.state]?.color ?? DECISION_STATES.NORMAL.color;
}

// Plotted traces: light neutral grey while NORMAL (ISA-101), the decision colour otherwise.
export const TRACE_NORMAL = '#c3cbd6';
export function traceColor(decision) {
  return !decision || decision.state === 'NORMAL' ? TRACE_NORMAL : decisionColor(decision);
}

// NAMUR NE 107 status signals for health.asset_ne107 and health.sensors.*.
// Grey when NORMAL; the standard NE 107 colours otherwise. `null` = sensor not present.
export const NE107 = {
  NORMAL: { name: 'Normal', color: null },
  MAINTENANCE_REQUIRED: { name: 'Maintenance required', color: '#4dabf7' },
  OFF_SPEC: { name: 'Out of specification', color: '#ffd43b' },
  CHECK_FUNCTION: { name: 'Check function', color: '#ff922b' },
  FAILURE: { name: 'Failure', color: '#ff5a6e' },
};
export function ne107(status) {
  if (status == null) return { name: 'Not present', color: null };
  const s = NE107[status];
  if (!s) throw new Error(`Unknown NE 107 status "${status}"`);
  return s;
}

// state.source → badge. Shape differs per source (solid / dashed / double) so it reads without colour.
export const SOURCES = {
  real: { name: 'REAL', style: 'solid' },
  simulated: { name: 'SIMULATED', style: 'dashed' },
  injected: { name: 'INJECTED', style: 'double' },
};
export function sourceInfo(source) {
  const s = SOURCES[source];
  if (!s) throw new Error(`Unknown source "${source}"`);
  return s;
}
