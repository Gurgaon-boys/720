"""Record the L1 demo: live sim -> backend -> dashboard, misfire cyl 3 injected at 6 s.

Frames are page screenshots stamped with wall-clock time, then laid onto a fixed 10 fps timeline,
so video time is real time. Expects the backend on :8000 and the dashboard on :5173 (VITE_API_URL set).
Extra dev-only deps (not in requirements.txt): pip install playwright imageio-ffmpeg, plus a Playwright Chromium.
Usage (repo root): python tools/record_l1_demo.py docs/figures/l1_demo.mp4 [width height]
"""

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
W, H = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (1280, 1000)
API = "http://127.0.0.1:8000"
INJECT_AT, LENGTH, FPS = 6.0, 20.0, 10


def post(path, body=None):
    req = urllib.request.Request(API + path, method="POST", data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


def label(page, text):
    page.evaluate("""(t) => {
        let el = document.getElementById('rec-label');
        if (!el) { el = document.createElement('div'); el.id = 'rec-label';
          el.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:99999;padding:8px 12px;'
            + 'font:13px ui-monospace,Consolas,monospace;color:#e8eaed;background:rgba(20,22,26,.92);'
            + 'border:1px solid #5f6368;border-radius:6px';
          document.body.appendChild(el); }
        el.textContent = t; }""", text)


frames = []   # (t since start, png bytes)
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": W, "height": H})
    post("/scenario/sim")                       # fresh simulator and rule layer for the take
    page.goto("http://localhost:5173")
    page.get_by_text("Skip to diagnostics").click()
    page.wait_for_selector("text=CYLINDER BANK", timeout=10_000)
    page.wait_for_timeout(1500)
    post("/scenario/sim")                       # restart once the dashboard is up: video t = stream t
    t0 = time.monotonic()
    injected = None
    while (t := time.monotonic() - t0) < LENGTH:
        if injected is None and t >= INJECT_AT:
            injected = post("/inject", {"fault": "misfire", "cylinder": 3, "severity": 1.0})
            label(page, f"Annotation: POST /inject misfire cyl 3 p=1.0 at stream t = {injected['t']:.1f} s")
        elif t < INJECT_AT and not frames:
            label(page, "Annotation: live simulator, healthy idle")
        frames.append((time.monotonic() - t0, page.screenshot(type="png")))
    browser.close()

# lay the frames onto a fixed-rate timeline (each slot shows the latest frame taken before it)
ff = imageio_ffmpeg.get_ffmpeg_exe()
cmd = [ff, "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS), "-i", "-",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "26", "-preset", "slow", "-movflags", "+faststart", str(OUT)]
proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
k = 0
for slot in range(int(LENGTH * FPS)):
    ts = slot / FPS
    while k + 1 < len(frames) and frames[k + 1][0] <= ts:
        k += 1
    proc.stdin.write(frames[k][1])
proc.stdin.close()
proc.wait()
Path(OUT.with_suffix(".png")).write_bytes(frames[-1][1])
print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB): {len(frames)} screenshots in {LENGTH:.0f} s "
      f"({len(frames) / LENGTH:.1f}/s), injected at stream t = {injected['t']:.2f} s")
