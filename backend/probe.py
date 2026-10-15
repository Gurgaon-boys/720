"""WebSocket probe: connects to all three channels, checks them against contract 1.3 and reports rates.

    python -m backend.probe                          # 10 s against ws://127.0.0.1:8000
    python -m backend.probe --seconds 15 --ack       # also ACK the first unacknowledged alarm
    python -m backend.probe --bad-ack                # also send an ACK for an unknown alarm id

Exits non-zero if any message breaks the contract or a channel is silent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import websockets

from backend import messages
from backend.signal_frame import decode_frame


async def probe(url: str, seconds: float, do_ack: bool, bad_ack: bool) -> int:
    stats = {ch: {"n": 0, "bad": 0, "first": None, "last": None, "max_gap": 0.0} for ch in ("state", "obd", "signal")}
    per_channel: dict[str, int] = {}
    deadline = time.monotonic() + seconds

    def seen(ch):
        s, now = stats[ch], time.monotonic()
        if s["last"] is not None:
            s["max_gap"] = max(s["max_gap"], now - s["last"])
        s["first"] = s["first"] or now
        s["last"] = now
        s["n"] += 1

    async def state():
        last_state, acked, sent_bad = None, False, False
        async with websockets.connect(f"{url}/ws/state") as ws:
            while (left := deadline - time.monotonic()) > 0:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), left))
                except asyncio.TimeoutError:
                    break
                if msg.get("type") == "ack_error":
                    print(f"  ack_error  {msg['reason']}: {msg['detail']}")
                    continue
                seen("state")
                if errs := messages.errors("state", msg):
                    stats["state"]["bad"] += 1
                    print(f"  INVALID state: {errs[:3]}")
                d = msg["decision"]
                summary = (d["state"], d["fault"], d["location"], d["alarm_id"], d["acked"], msg["health"]["asset_ne107"],
                           tuple(sorted(msg["health"]["sensors"].items())))
                if summary != last_state:
                    print(f"  t={msg['t']:6.1f}  {d['state']:<12} fault={d['fault']} cyl={d['location']} "
                          f"alarm={d['alarm_id']} acked={d['acked']} asset={summary[5]} sensors={dict(summary[6])}")
                    last_state = summary
                if bad_ack and not sent_bad:
                    sent_bad = True
                    await ws.send(json.dumps({"schema_version": "1.4", "type": "ack", "alarm_id": "a_9999",
                                              "operator": "probe", "sent_at": time.time()}))
                if do_ack and not acked and d["state"] == "ALARM" and not d["acked"]:
                    acked = True
                    print(f"  -> sending ACK for {d['alarm_id']}")
                    await ws.send(json.dumps({"schema_version": "1.4", "type": "ack", "alarm_id": d["alarm_id"],
                                              "operator": "probe", "sent_at": time.time()}))

    async def obd():
        async with websockets.connect(f"{url}/ws/obd") as ws:
            while (left := deadline - time.monotonic()) > 0:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), left))
                except asyncio.TimeoutError:
                    break
                seen("obd")
                if errs := messages.errors("obd", msg):
                    stats["obd"]["bad"] += 1
                    print(f"  INVALID obd: {errs[:3]}")

    async def signal():
        async with websockets.connect(f"{url}/ws/signal") as ws:
            while (left := deadline - time.monotonic()) > 0:
                try:
                    buf = await asyncio.wait_for(ws.recv(), left)
                except asyncio.TimeoutError:
                    break
                seen("signal")
                try:
                    header, _ = decode_frame(buf)
                    per_channel[header["channel"]] = per_channel.get(header["channel"], 0) + 1
                    errs = messages.errors("signal_header", header)
                except Exception as e:  # noqa: BLE001
                    errs = [repr(e)]
                if errs:
                    stats["signal"]["bad"] += 1
                    print(f"  INVALID signal frame: {errs[:3]}")

    print(f"probing {url} for {seconds:g} s")
    await asyncio.gather(state(), obd(), signal())

    print("\nchannel  messages  rate (msg/s)  max gap (s)  invalid")
    failed = 0
    for ch, s in stats.items():
        span = (s["last"] - s["first"]) if s["n"] > 1 else 0
        rate = (s["n"] - 1) / span if span else 0
        print(f"{ch:<8} {s['n']:>8}  {rate:>12.1f}  {s['max_gap']:>11.2f}  {s['bad']:>7}")
        failed += s["bad"] + (s["n"] == 0)
    if per_channel:
        sg = stats["signal"]
        span = (sg["last"] - sg["first"]) or 1.0
        print("signal frames per channel: " + ", ".join(f"{k}={v} (~{v / span:.1f}/s)" for k, v in sorted(per_channel.items())))
    print("PASS" if not failed else "FAIL")
    return 1 if failed else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="ws://127.0.0.1:8000")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--ack", action="store_true", help="ACK the first unacknowledged alarm")
    ap.add_argument("--bad-ack", action="store_true", help="send one ACK for an unknown alarm id")
    a = ap.parse_args()
    sys.exit(asyncio.run(probe(a.url.rstrip("/"), a.seconds, a.ack, a.bad_ack)))


if __name__ == "__main__":
    main()
