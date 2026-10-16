"""Record the L2 live acceptance: two-witness ALARM -> ACK -> clear, for misfire, knock and injector clog,
then valve tick (held out: must be UNCLASSIFIED, never a named fault).

One backend process (REST + WebSocket + OPC UA) and the dashboard. For every fault:
  healthy NORMAL -> POST /inject -> ADVISORY -> ALARM -> click ACK on the dashboard -> OPC UA shows the
  same AlarmId with Acked = true -> POST /clear_faults -> NORMAL.
The decision is read from the OPC UA server (Decision/State, AlarmId, Acked) and from the dashboard text.
Frames are page screenshots stamped with wall-clock time, laid onto a fixed 10 fps timeline (video time
is real time). Writes the video, a last-frame PNG and a JSON log (time to ALARM per fault, checks).

Expects the backend on 127.0.0.1:8000 (OPC UA on :4840) and the dashboard on :5173 (VITE_API_URL set).
Dev-only deps: pip install playwright imageio-ffmpeg asyncua, plus a Playwright Chromium.
Usage (repo root): python tools/record_l2_demo.py docs/figures/l2_demo.mp4
"""

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import imageio_ffmpeg
from asyncua.sync import Client
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
W, H = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (1280, 1000)
API = "http://127.0.0.1:8000"
OPCUA = "opc.tcp://127.0.0.1:4840/720/"
NS_URI = "urn:720:hackhive"
FPS = 10
PLAN = [  # fault, cylinder, severity, expected state, max seconds to wait for it
    ("misfire", 3, 1.0, "ALARM", 20.0),
    ("knock", 2, 1.0, "ALARM", 30.0),
    ("injector_clog", 1, 0.5, "ALARM", 75.0),
    ("valve_tick", 4, 1.0, "UNCLASSIFIED", 20.0),
]


def post(path, body=None):
    req = urllib.request.Request(API + path, method="POST", data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


def label(page, text):
    page.evaluate("""(t) => {
        let el = document.getElementById('rec-label');
        if (!el) { el = document.createElement('div'); el.id = 'rec-label';
          el.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:99999;padding:8px 12px;max-width:560px;'
            + 'font:13px ui-monospace,Consolas,monospace;color:#e8eaed;background:rgba(20,22,26,.92);'
            + 'border:1px solid #5f6368;border-radius:6px';
          document.body.appendChild(el); }
        el.textContent = t; }""", text)


frames, log = [], {"faults": [], "checks": []}
t0 = None


def snap(page):
    frames.append((time.monotonic() - t0, page.screenshot(type="png")))


def check(name, ok, detail=""):
    log["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f" ({detail})" if detail else ""), flush=True)


with Client(OPCUA) as ua, sync_playwright() as p:
    idx = ua.get_namespace_index(NS_URI)
    node = {k: ua.get_node(f"ns={idx};s=EngineAsset.Decision.{k}") for k in ("State", "AlarmId", "Acked", "Fault", "Cylinder")}
    opc = lambda: {k: n.read_value() for k, n in node.items()}
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": W, "height": H})
    post("/scenario/sim")
    page.goto("http://127.0.0.1:5173")
    page.get_by_text("Skip to diagnostics").click()
    page.wait_for_selector("text=CYLINDER BANK", timeout=15_000)
    page.wait_for_timeout(1500)
    post("/scenario/sim")                       # restart once the dashboard is up
    t0 = time.monotonic()

    def wait(pred, timeout, note):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            s = opc()
            if pred(s):
                return s
            snap(page)
        return None

    label(page, "Live simulator (SIMULATED), healthy idle: 35 s so the 30 s OBD window is full")
    s = wait(lambda s: False, 35.0, "")
    check("healthy start is NORMAL", opc()["State"] == "NORMAL", opc()["State"])
    for fault, cyl, sev, expect, limit in PLAN:
        inj = post("/inject", {"fault": fault, "cylinder": cyl, "severity": sev})
        t_inj = time.monotonic()
        rec = {"fault": fault, "cylinder": cyl, "severity": sev, "expected": expect, "injected_stream_t": inj.get("t")}
        label(page, f"POST /inject {fault} cyl {cyl} severity {sev}" + (" (HELD OUT: never trained)" if fault == "valve_tick" else ""))
        seen = set()

        def reached(s):
            seen.add(s["State"])
            return s["State"] == expect
        s = wait(reached, limit, expect)
        rec["states_seen"] = sorted(seen)
        if s is None:
            rec["reached"] = False
            check(f"{fault}: reaches {expect} within {limit:.0f} s", False, f"seen {sorted(seen)}")
        else:
            rec["reached"] = True
            rec["time_to_state_s"] = round(time.monotonic() - t_inj, 2)
            rec["opcua_at_state"] = {k: (v if not hasattr(v, "isoformat") else v.isoformat()) for k, v in s.items()}
            check(f"{fault}: {expect} in {rec['time_to_state_s']:.1f} s", True, f"fault={s['Fault']} cyl={s['Cylinder']}")
            if expect == "ALARM":
                check(f"{fault}: ADVISORY came first", "ADVISORY" in seen, sorted(seen))
                check(f"{fault}: alarm names {fault} cyl {cyl}", s["Fault"] == fault and s["Cylinder"] == cyl,
                      f"{s['Fault']} cyl {s['Cylinder']}")
                alarm_id = s["AlarmId"]
                label(page, f"ALARM {alarm_id}: two witnesses (physics + classifier). Operator clicks ACK")
                for _ in range(10):
                    snap(page)
                page.get_by_role("button", name="ACK").first.click()
                s2 = wait(lambda s: s["Acked"], 5.0, "acked")
                check(f"{fault}: OPC UA shows acked, same AlarmId", s2 is not None and s2["AlarmId"] == alarm_id,
                      f"{alarm_id} -> {s2 and s2['AlarmId']}")
                check(f"{fault}: dashboard shows the same AlarmId", page.get_by_text(alarm_id).count() > 0, alarm_id)
                rec["alarm_id"] = alarm_id
            else:
                check(f"{fault}: never a named fault (no ALARM, no ADVISORY)",
                      not ({"ALARM", "ADVISORY"} & seen), sorted(seen))
                label(page, "Valve tick (held out): UNCLASSIFIED, 'abnormal, inspect'. No fault is named")
                for _ in range(30):
                    snap(page)
        post("/clear_faults")
        label(page, f"POST /clear_faults ({fault})")
        t_clr = time.monotonic()
        s = wait(lambda s: s["State"] == "NORMAL", 90.0, "NORMAL")
        rec["back_to_normal_s"] = round(time.monotonic() - t_clr, 2) if s else None
        check(f"{fault}: back to NORMAL after clear", s is not None, rec["back_to_normal_s"])
        log["faults"].append(rec)
        label(page, "Healthy again")
        wait(lambda s: False, 4.0, "")
    browser.close()

LENGTH = frames[-1][0]
ff = imageio_ffmpeg.get_ffmpeg_exe()
cmd = [ff, "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS), "-i", "-",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "28", "-preset", "medium", "-movflags", "+faststart", str(OUT)]
proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
k = 0
for slot in range(int(LENGTH * FPS)):
    ts = slot / FPS
    while k + 1 < len(frames) and frames[k + 1][0] <= ts:
        k += 1
    proc.stdin.write(frames[k][1])
proc.stdin.close()
proc.wait()
OUT.with_suffix(".png").write_bytes(frames[-1][1])
log["video_s"] = round(LENGTH, 1)
log["screenshots"] = len(frames)
OUT.with_suffix(".json").write_text(json.dumps(log, indent=1))
print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB, {LENGTH:.0f} s); "
      f"{sum(c['ok'] for c in log['checks'])}/{len(log['checks'])} checks passed")
