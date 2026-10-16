"""Incident report (D138): a printable HTML page built only from what the backend recorded.

Sources: the incident event log (backend clock), the last state broadcast and one Reliability Agent
investigation. A value that was not recorded is written "unavailable". No cost, damage, remaining-life or
real-world accuracy figure appears, because none is measured here. Print to PDF from the browser.
"""

from __future__ import annotations

import html
import time

from backend.investigator import investigate

LIMITATIONS = [
    "Source: the physics-informed engine simulator (SIMULATED). Faults are injected on purpose; nothing here is a measurement of a real engine.",
    "Times are the backend clock of this laptop. A sensor is reported FAILURE only after 3 s without data (backend watchdog, D15).",
    "The ML classifier abstains until it has 20 s of OBD history after a run starts.",
    "The incident record is kept in memory (last 500 events) and is lost when the backend restarts.",
    "Evidence availability (FULL / PARTIAL / INSUFFICIENT) describes which witnesses can speak; it is not a diagnosis.",
]


def _t(epoch: float | None) -> str:
    return "unavailable" if epoch is None else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch)) + f".{int(epoch * 1000) % 1000:03d}"


def _e(x) -> str:
    return html.escape("unavailable" if x is None or x == "" else str(x))


def build(rt) -> str:
    inv = investigate(rt, trigger="report")
    events = [e for e in rt.events.since(0)]
    s = rt.last_state or {}
    f = inv.get("findings", {})
    alarm = f.get("alarm", {})
    op = f.get("operating_point", {})
    sens = f.get("sensors", {})
    ev = f.get("evidence", {})
    avail = f.get("availability", {})
    eps = [e for e in events if e["kind"] in ("inject", "clear") and e["phase"] == "accepted"]
    alarms = sorted({e["data"].get("alarm_id") for e in events if e["kind"] == "decision" and e["data"].get("alarm_id")})
    trans = [e for e in events if e["kind"] in ("decision", "ack", "sensor_health", "classifier", "physics",
                                                "sensor_off", "sensor_on", "sensor_restore", "inject", "clear", "source")]

    def rows(items, cols):
        return "".join("<tr>" + "".join(f"<td>{c(i)}</td>" for c in cols) + "</tr>" for i in items) or \
            f"<tr><td colspan='{len(cols)}'>none recorded</td></tr>"

    phase = lambda e: f"<span class='ph {e['phase']}'>{e['phase']}</span>"   # noqa: E731
    sensors_rows = "".join(
        f"<tr><td>{_e(k.upper())}</td><td class='{'bad' if v == 'FAILURE' else ''}'>{_e(v)}</td>"
        f"<td>{'yes' if k in (sens.get('disabled_by_operator') or []) else 'no'}</td></tr>"
        for k, v in (sens.get("health") or {}).items())
    physics_rows = "".join(
        f"<tr><td>{_e(r['rule'])}</td><td>{'FIRED' if r.get('fired') else 'quiet'}</td><td>{_e(r.get('location'))}</td>"
        f"<td>{_e(r.get('rule_basis'))}</td></tr>" for r in ev.get("physics_rules", []))
    models_rows = "".join(f"<tr><td>{_e(m.get('model'))}</td><td>{_e(m.get('label'))}</td><td>{_e(m.get('p'))}</td></tr>"
                          for m in ev.get("models", [])) or "<tr><td colspan='3'>no classifier output in the last window</td></tr>"
    answers = "".join(f"<div class='qa'><h4>{_e(a['question'])}</h4><p>{_e(a['answer'])}</p>"
                      f"<p class='facts'>Grounded in: {_e(', '.join(a['facts']))}</p></div>" for a in inv.get("answers", []))
    acks = [e for e in events if e["kind"] == "ack" and e["phase"] in ("accepted", "confirmed")]

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>720 Incident Report</title><style>
body{{font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;color:#141a22;background:#fff;margin:0;padding:32px 40px;max-width:1000px}}
h1{{font-size:24px;margin:0;letter-spacing:.04em}} h2{{font-size:15px;text-transform:uppercase;letter-spacing:.12em;border-bottom:2px solid #141a22;padding-bottom:4px;margin-top:28px}}
h4{{margin:10px 0 2px;font-size:13.5px}} table{{border-collapse:collapse;width:100%;font-size:12.5px}} td,th{{border:1px solid #ccd3dc;padding:4px 7px;text-align:left;vertical-align:top;overflow-wrap:anywhere}}
th{{background:#eef1f5}} .bad{{color:#b3261e;font-weight:700}} .meta{{color:#4b5563;font-size:12.5px}} .grid{{display:grid;grid-template-columns:1fr 1fr;gap:4px 24px}}
.badge{{display:inline-block;border:1px dashed #4b5563;border-radius:99px;padding:2px 10px;font-size:11px;letter-spacing:.12em}}
.ph{{font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;padding:1px 6px;border-radius:4px;background:#eef1f5}} .ph.confirmed{{background:#dfe9f7}} .ph.refused{{background:#f8dcdc}}
.facts{{color:#6b7280;font-size:11.5px;margin:0}} .lvl{{font-weight:700}} ul{{margin:4px 0;padding-left:20px}}
@media (max-width:760px){{body{{padding:18px 16px}} .grid{{grid-template-columns:1fr}}}} @media print{{body{{padding:0}} button{{display:none}}}}</style></head><body>
<button onclick="window.print()" style="float:right;padding:6px 14px">Print / save as PDF</button>
<h1>720 · INCIDENT REPORT</h1>
<p class="meta">Generated {_e(_t(rt.clock()))} from the backend's recorded data · <span class="badge">{_e((s.get('source_label') or s.get('source') or '').upper())}</span></p>
<h2>Asset</h2><div class="grid">
<div>Asset: <b>EngineAsset</b> · run <b>{_e(s.get('run_id'))}</b></div><div>Source mode: {_e(rt.scenario.name)} · {_e(s.get('mode'))}</div>
<div>Operating point: {_e(op.get('rpm'))} rpm · load {_e(op.get('load_pct'))} % · {_e(op.get('regime'))}</div><div>Stream time: {_e(s.get('t'))} s</div>
<div>Current decision: <b>{_e(alarm.get('state'))}</b> {_e(alarm.get('fault') or '')} {('cyl ' + str(alarm.get('location'))) if alarm.get('location') else ''}</div>
<div>Alarm IDs this session: {_e(', '.join(alarms) or 'none')}</div>
<div>Evidence availability: <span class="lvl">{_e(avail.get('level'))}</span></div><div>New confirmed alarm possible: {'yes' if avail.get('new_alarm_possible') else 'no'}</div></div>
<h2>Summary</h2><p>{_e(inv.get('headline'))}</p>
<h2>Fault episodes (injections)</h2><table><tr><th>Time</th><th>Via</th><th>Action</th></tr>{rows(eps, [lambda e: _e(_t(e['t'])), lambda e: _e(e['via']), lambda e: _e(e['text'])])}</table>
<h2>Sensor availability (last state)</h2><table><tr><th>Sensor</th><th>Watchdog health</th><th>Switched off by operator</th></tr>{sensors_rows or "<tr><td colspan='3'>unavailable</td></tr>"}</table>
<h2>Physics evidence (last window)</h2><table><tr><th>Rule</th><th>State</th><th>Cylinder</th><th>Basis</th></tr>{physics_rows or "<tr><td colspan='4'>unavailable</td></tr>"}</table>
<h2>ML evidence (last window)</h2><table><tr><th>Model</th><th>Label</th><th>p</th></tr>{models_rows}</table>
<h2>Operator acknowledgement</h2><table><tr><th>Time</th><th>Phase</th><th>Via</th><th>Event</th></tr>{rows(acks, [lambda e: _e(_t(e['t'])), phase, lambda e: _e(e['via']), lambda e: _e(e['text'])])}</table>
<h2>Timeline (requested · accepted · confirmed)</h2><table><tr><th>Time</th><th>Stream t</th><th>Phase</th><th>Via</th><th>Event</th></tr>
{rows(trans, [lambda e: _e(_t(e['t'])), lambda e: _e(e['stream_t']), phase, lambda e: _e(e['via']), lambda e: _e(e['text'])])}</table>
<h2>Reliability Agent findings</h2>{answers or '<p>unavailable</p>'}
<h2>Data-quality limitations</h2><ul>{''.join(f'<li>{html.escape(x)}</li>' for x in LIMITATIONS)}</ul>
<p class="meta">{_e(inv.get('authority'))}</p></body></html>"""
