"""Reproduce the post-misfire false alarm on a running backend (live demo path, SIM 2.1).

    python -m eval.post_misfire_repro [--api http://127.0.0.1:8000] [--out file.json]

Sequence (as Livana ran it): simulator, warm-up, inject misfire cyl 3 p = 1.0, wait for ALARM, ACK, clear,
then watch 120 s. Reports every state change after the clear, any new ALARM (fault, cylinder, time after the
clear), the cylinder the classifier names per window, and the first NORMAL.
"""

from __future__ import annotations

import argparse
import json
import threading
import time

import httpx
from websockets.sync.client import connect

WATCH_S = 120.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--out")
    ap.add_argument("--warmup", type=float, default=25.0)
    ap.add_argument("--hold", type=float, default=2.0, help="seconds between ACK and clear (misfire still running)")
    args = ap.parse_args()
    api, wsurl = args.api, args.api.replace("http", "ws") + "/ws/state"
    hist, lock = [], threading.Lock()
    ws = connect(wsurl)

    def reader():
        while True:
            try:
                m = json.loads(ws.recv())
            except Exception:
                return
            if "decision" in m:
                with lock:
                    hist.append((time.monotonic(), m))

    threading.Thread(target=reader, daemon=True).start()

    def cur():
        with lock:
            return hist[-1][1] if hist else None

    def wait(pred, timeout):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            m = cur()
            if m and pred(m):
                return round(time.monotonic() - t0, 2), m
            time.sleep(0.05)
        return None, cur()

    httpx.post(f"{api}/scenario/sim").raise_for_status()
    time.sleep(args.warmup)
    t_inj = time.monotonic()
    httpx.post(f"{api}/inject", json={"fault": "misfire", "cylinder": 3, "severity": 1.0}).raise_for_status()
    t_alarm, m = wait(lambda m: m["decision"]["state"] == "ALARM", 30)
    first_id = m["decision"]["alarm_id"]
    ws.send(json.dumps({"schema_version": m["schema_version"], "type": "ack", "alarm_id": first_id,
                        "operator": "repro", "sent_at": time.time()}))
    wait(lambda m: m["decision"]["acked"], 5)
    time.sleep(args.hold)
    t_clear = time.monotonic()
    httpx.post(f"{api}/clear_faults").raise_for_status()
    time.sleep(WATCH_S)
    with lock:
        after = [(round(t - t_clear, 2), m) for t, m in hist if t >= t_clear]
    ws.close()

    changes, alarms, prev, cyl_seq = [], [], None, []
    for t, m in after:
        d = m["decision"]
        key = (d["state"], d["fault"], d["location"], d["alarm_id"])
        if key != prev:
            changes.append({"t_after_clear_s": t, "state": d["state"], "fault": d["fault"], "location": d["location"],
                            "alarm_id": d["alarm_id"], "guidance": d.get("guidance")})
            prev = key
        if d["state"] == "ALARM" and d["alarm_id"] != first_id and d["alarm_id"] not in [a["alarm_id"] for a in alarms]:
            alarms.append({"t_after_clear_s": t, "alarm_id": d["alarm_id"], "fault": d["fault"], "location": d["location"]})
        c = (m.get("features_summary") or {}).get("classifier_cylinder")
        if c and (not cyl_seq or cyl_seq[-1] != c):
            cyl_seq.append(c)
    first_normal = next((t for t, m in after if m["decision"]["state"] == "NORMAL"), None)
    counts = {}
    for _, m in after:
        counts[m["decision"]["state"]] = counts.get(m["decision"]["state"], 0) + 1
    res = {"label": "SIM 2.1 (live demo backend)", "hold_s": args.hold, "misfire_alarm_s": t_alarm, "first_alarm_id": first_id,
           "new_alarms_after_clear": alarms, "first_normal_after_clear_s": first_normal,
           "state_messages_after_clear": counts, "classifier_cylinder_sequence": cyl_seq[:20],
           "changes": changes[:60]}
    print(json.dumps({k: v for k, v in res.items() if k != "changes"}, indent=1))
    for c in changes[:40]:
        print(c)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
