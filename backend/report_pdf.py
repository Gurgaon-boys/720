"""Incident report as a PDF (D140): the same recorded data as backend/report.py, laid out with reportlab.

Only what the backend recorded: the incident event log (backend clock), the last state broadcast and one
Reliability Agent investigation. Unrecorded values read "unavailable". No cost, damage, remaining-life or
real-world accuracy figure appears. Returns None when reportlab is not installed, so the caller can fall back
to the printable HTML report.
"""

from __future__ import annotations

import io
import time
from xml.sax.saxutils import escape

from backend.investigator import investigate
from backend.report import LIMITATIONS

try:  # optional at import time: the backend still runs without it
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    AVAILABLE = True
except ImportError:  # pragma: no cover
    AVAILABLE = False

INK = "#141a22"
MUTED = "#5b6676"
RULE = "#ccd3dc"
HEAD_BG = "#eef1f5"
RED = "#b3261e"
AMBER = "#9a6400"


def _t(epoch):
    if epoch is None:
        return "unavailable"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))


def _v(x):
    return "unavailable" if x is None or x == "" else str(x)


def _via(e) -> str:
    """Who made it happen: the command channel, or the state broadcast for confirmations."""
    return e["via"] or ("broadcast" if e["phase"] == "confirmed" else "unavailable")


def filename(rt) -> str:
    run = (rt.last_state or {}).get("run_id") or "run"
    return f"720_incident_{run}_{time.strftime('%Y%m%d_%H%M%S', time.localtime(rt.clock()))}.pdf"


def build_pdf(rt) -> bytes | None:
    if not AVAILABLE:
        return None
    inv = investigate(rt, trigger="pdf report")
    events = rt.events.since(0)
    s = rt.last_state or {}
    f = inv.get("findings", {})
    alarm, op, sens, ev, avail = (f.get(k, {}) for k in ("alarm", "operating_point", "sensors", "evidence", "availability"))

    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK)
    small = ParagraphStyle("s", parent=body, fontSize=8, leading=10.5, textColor=MUTED)
    cell = ParagraphStyle("c", parent=body, fontSize=8.2, leading=10.5, alignment=TA_LEFT)
    h1 = ParagraphStyle("h1", parent=body, fontName="Helvetica-Bold", fontSize=20, leading=24, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=body, fontName="Helvetica-Bold", fontSize=10.5, leading=14, spaceBefore=12,
                        spaceAfter=5, textColor=INK, keepWithNext=1)
    lead = ParagraphStyle("lead", parent=body, fontName="Helvetica-Bold", fontSize=11.5, leading=15)

    def P(text, style=cell):
        return Paragraph(escape(_v(text)), style)

    def table(head, rows, widths, highlight=None):
        data = [[Paragraph(f"<b>{escape(h)}</b>", cell) for h in head]]
        data += [[P(c) for c in r] for r in rows] or [[P("none recorded")] + [P("") for _ in head[1:]]]
        t = Table(data, colWidths=widths, repeatRows=1)
        style = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(RULE)),
                 ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(HEAD_BG)),
                 ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        for i, r in enumerate(rows, start=1):
            if highlight and highlight(r):
                style.append(("TEXTCOLOR", (0, i), (-1, i), colors.HexColor(RED)))
        t.setStyle(TableStyle(style))
        return t

    W = A4[0] - 30 * mm
    story = [
        Paragraph("720 · INCIDENT REPORT", h1),
        Paragraph(escape(f"Generated {_t(rt.clock())} from the backend's recorded data · source "
                         f"{(s.get('source_label') or s.get('source') or 'unavailable').upper()}"), small),
        Spacer(1, 8),
        Paragraph(escape(_v(inv.get("headline"))), lead),
    ]

    level = avail.get("level")
    asset = [
        ["Asset", f"EngineAsset · run {_v(s.get('run_id'))}", "Source mode", f"{rt.scenario.name} · {_v(s.get('mode'))}"],
        ["Operating point", f"{_v(op.get('rpm'))} rpm · load {_v(op.get('load_pct'))} % · {_v(op.get('regime'))}", "Stream time", f"{_v(s.get('t'))} s"],
        ["Decision", f"{_v(alarm.get('state'))} {alarm.get('fault') or ''} {('cyl ' + str(alarm.get('location'))) if alarm.get('location') else ''}".strip(),
         "Alarm IDs this session", ", ".join(sorted({e['data'].get('alarm_id') for e in events if e['kind'] == 'decision' and e['data'].get('alarm_id')})) or "none"],
        ["Evidence availability", _v(level), "New confirmed alarm possible", "yes" if avail.get("new_alarm_possible") else "no"],
    ]
    t = Table([[Paragraph(f"<b>{escape(a)}</b>", cell), P(b), Paragraph(f"<b>{escape(c)}</b>", cell), P(d)] for a, b, c, d in asset],
              colWidths=[W * 0.17, W * 0.33, W * 0.2, W * 0.3])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(RULE)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("BACKGROUND", (0, 0), (0, -1), colors.HexColor(HEAD_BG)),
                           ("BACKGROUND", (2, 0), (2, -1), colors.HexColor(HEAD_BG))]))
    story += [Paragraph("ASSET", h2), t]

    eps = [e for e in events if e["kind"] in ("inject", "clear") and e["phase"] == "accepted"]
    story += [Paragraph("FAULT EPISODES (INJECTIONS)", h2),
              table(["Time", "Via", "Action"], [[_t(e["t"]), _v(e["via"]), e["text"]] for e in eps], [W * 0.22, W * 0.1, W * 0.68])]

    health = sens.get("health") or {}
    disabled = sens.get("disabled_by_operator") or []
    story += [Paragraph("SENSOR AVAILABILITY (LAST STATE)", h2),
              table(["Sensor", "Watchdog health", "Switched off by operator"],
                    [[k.upper(), v, "yes" if k in disabled else "no"] for k, v in health.items()],
                    [W * 0.25, W * 0.35, W * 0.4], highlight=lambda r: r[1] == "FAILURE")]

    story += [Paragraph("PHYSICS EVIDENCE (LAST WINDOW)", h2),
              table(["Rule", "State", "Cylinder", "Basis"],
                    [[r["rule"], "FIRED" if r.get("fired") else "quiet", _v(r.get("location")), _v(r.get("rule_basis"))] for r in ev.get("physics_rules", [])],
                    [W * 0.3, W * 0.2, W * 0.15, W * 0.35])]
    models = ev.get("models", [])
    story += [Paragraph("ML EVIDENCE (LAST WINDOW)", h2),
              table(["Model", "Label", "p"], [[m.get("model"), m.get("label"), m.get("p")] for m in models], [W * 0.3, W * 0.4, W * 0.3])
              if models else P("No classifier output in the last window (its inputs were incomplete, or no model is loaded).", body)]

    acks = [e for e in events if e["kind"] == "ack" and e["phase"] in ("accepted", "confirmed", "refused")]
    story += [Paragraph("OPERATOR ACKNOWLEDGEMENT", h2),
              table(["Time", "Phase", "Via", "Event"], [[_t(e["t"]), e["phase"], _via(e), e["text"]] for e in acks],
                    [W * 0.21, W * 0.12, W * 0.12, W * 0.55])]

    kinds = ("decision", "ack", "sensor_health", "classifier", "physics", "sensor_off", "sensor_on", "sensor_restore", "inject", "clear", "source")
    trans = [e for e in events if e["kind"] in kinds][-120:]
    story += [Paragraph("TIMELINE (REQUESTED · ACCEPTED · CONFIRMED)", h2),
              table(["Time", "Stream t", "Phase", "Via", "Event"],
                    [[_t(e["t"]), "—" if e["stream_t"] is None else e["stream_t"], e["phase"], _via(e), e["text"]] for e in trans],
                    [W * 0.19, W * 0.09, W * 0.11, W * 0.11, W * 0.5],
                    highlight=lambda r: "FAILURE" in r[4] or "refused" in r[2])]

    story.append(Paragraph("RELIABILITY AGENT FINDINGS", h2))
    for a in inv.get("answers", []):
        story.append(KeepTogether([Paragraph(f"<b>{escape(a['question'])}</b>", body), P(a["answer"], body),
                                   Paragraph(escape("Grounded in: " + ", ".join(a["facts"])), small), Spacer(1, 5)]))

    story.append(Paragraph("DATA-QUALITY LIMITATIONS", h2))
    story += [Paragraph(escape("• " + x), body) for x in LIMITATIONS]
    story += [Spacer(1, 8), Paragraph(escape(_v(inv.get("authority"))), small)]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor(MUTED))
        canvas.drawString(15 * mm, 10 * mm, "720 condition monitor · incident report · recorded data only · SIMULATED source")
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=16 * mm,
                            title="720 Incident Report", author="720 backend")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
