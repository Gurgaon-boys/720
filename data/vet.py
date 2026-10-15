"""Two-person vetting of AudioSet clips: play, look at the spectrogram, answer.

    python data/vet.py --rater diljit            # opens http://127.0.0.1:8765
    python data/vet.py --rater dipen --port 8766 --classes knock

One process per rater. Answers go to data/vetting/<rater>.csv (one row per clip,
appended as you go; rerunning resumes where you stopped). The tool reads only
that rater's own file, so raters never see each other's answers. Don't open
the other rater's CSV until both are done, then run data/vet_merge.py.

The AudioSet labels are hidden (they would bias the answers); only the class we
downloaded the clip for is shown, because knock clips get an extra question.
Questions:
    is_passenger_car     y / n / unsure
    plausibly_4cyl       y / n / unsure
    speech_or_music      y / n
    is_detonation_knock  y / n / unsure   (knock clips only)

Keys: 1/2/3 car y/n/?, q/w/e 4-cyl y/n/?, a/s speech/music y/n,
      z/x/c detonation y/n/?, space play/pause, Enter save, b back.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import random
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import EVAL, RAW, REPO, utc_now_iso  # noqa: E402

VET_DIR = REPO / "data" / "vetting"
SPEC_DIR = RAW / "audioset" / "_spec"
QUESTIONS = {
    "is_passenger_car": ("y", "n", "unsure"),
    "plausibly_4cyl": ("y", "n", "unsure"),
    "speech_or_music": ("y", "n"),
    "is_detonation_knock": ("y", "n", "unsure"),
}
FIELDS = ["clip_id", "video_id", "start", "target_class", "rater",
          *QUESTIONS, "note", "rated_at"]


def load_clips(manifest: Path, classes: set[str], seed: int, limit: int | None) -> list[dict]:
    with open(manifest, newline="") as fh:
        rows = [r for r in csv.DictReader(fh)
                if r["status"] == "ok" and r["target_class"] in classes and (REPO / r["path"]).exists()]
    for r in rows:
        r["clip_id"] = f"{r['video_id']}_{r['start']}"
    rows.sort(key=lambda r: r["clip_id"])
    random.Random(seed).shuffle(rows)  # same order for every rater; mixes classes and splits
    return rows[:limit] if limit else rows


def spectrogram_png(wav: Path, out: Path) -> bytes:
    if out.exists():
        return out.read_bytes()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from scipy.signal import spectrogram

    x, sr = sf.read(wav, dtype="float32")
    f, t, S = spectrogram(x, fs=sr, nperseg=2048, noverlap=1536)
    db = 10 * np.log10(S + 1e-12)
    fig, (a0, a1) = plt.subplots(2, 1, figsize=(11, 4.6), height_ratios=[1, 3], sharex=True)
    a0.plot(np.arange(len(x)) / sr, x, lw=0.3, color="0.2")
    a0.set_ylabel("amp")
    a0.set_xlim(0, len(x) / sr)
    a1.pcolormesh(t, f / 1000, db, shading="auto", cmap="magma", vmin=db.max() - 90, vmax=db.max())
    a1.set_ylabel("kHz")
    a1.set_xlabel("s")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=90)
    plt.close(fig)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buf.getvalue())
    return buf.getvalue()


class Store:
    """This rater's answers. Last row per clip wins (so 'back' + re-save corrects an answer)."""

    def __init__(self, rater: str):
        self.path = VET_DIR / f"{rater}.csv"
        self.rater = rater
        self.lock = threading.Lock()
        self.answers: dict[str, dict] = {}
        if self.path.exists():
            with open(self.path, newline="") as fh:
                for r in csv.DictReader(fh):
                    self.answers[r["clip_id"]] = r

    def save(self, clip: dict, ans: dict) -> None:
        row = {"clip_id": clip["clip_id"], "video_id": clip["video_id"], "start": clip["start"],
               "target_class": clip["target_class"], "rater": self.rater, "rated_at": utc_now_iso(),
               "note": (ans.get("note") or "").replace("\n", " ")[:300]}
        for q, allowed in QUESTIONS.items():
            v = ans.get(q, "")
            if q == "is_detonation_knock" and clip["target_class"] != "knock":
                v = ""
            elif v not in allowed:
                raise ValueError(f"{q} must be one of {allowed}")
            row[q] = v
        with self.lock:
            new = not self.path.exists()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", newline="") as fh:
                w = csv.DictWriter(fh, FIELDS)
                if new:
                    w.writeheader()
                w.writerow(row)
            self.answers[clip["clip_id"]] = row


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>720 vetting</title>
<style>
body{font:15px system-ui,sans-serif;margin:0 auto;max-width:1040px;padding:16px;background:#f4f4f4;color:#222}
h1{font-size:18px;margin:0 0 4px} .meta{color:#555;margin-bottom:8px}
img{width:100%;background:#fff;border:1px solid #ccc} audio{width:100%;margin:8px 0}
.q{display:flex;gap:8px;align-items:center;margin:6px 0} .q b{width:200px}
.q label{padding:4px 10px;border:1px solid #aaa;border-radius:4px;background:#fff;cursor:pointer}
.q input{display:none} .q input:checked+span{font-weight:700} .q label:has(input:checked){background:#333;color:#fff}
button{font-size:15px;padding:6px 16px;margin-right:8px} #msg{color:#a00;margin-left:8px}
.knock{display:none} .kbd{color:#777;font-size:12px}
</style></head><body>
<h1>720 vetting · rater <span id="rater"></span></h1>
<div class="meta" id="meta">loading…</div>
<img id="spec" alt="spectrogram"><audio id="audio" controls></audio>
<form id="f">
<div class="q"><b>Passenger car?</b>
 <label><input type="radio" name="is_passenger_car" value="y"><span>yes [1]</span></label>
 <label><input type="radio" name="is_passenger_car" value="n"><span>no [2]</span></label>
 <label><input type="radio" name="is_passenger_car" value="unsure"><span>unsure [3]</span></label></div>
<div class="q"><b>Plausibly 4-cylinder?</b>
 <label><input type="radio" name="plausibly_4cyl" value="y"><span>yes [q]</span></label>
 <label><input type="radio" name="plausibly_4cyl" value="n"><span>no [w]</span></label>
 <label><input type="radio" name="plausibly_4cyl" value="unsure"><span>unsure [e]</span></label></div>
<div class="q"><b>Speech or music audible?</b>
 <label><input type="radio" name="speech_or_music" value="y"><span>yes [a]</span></label>
 <label><input type="radio" name="speech_or_music" value="n"><span>no [s]</span></label></div>
<div class="q knock"><b>Detonation knock?</b>
 <label><input type="radio" name="is_detonation_knock" value="y"><span>yes [z]</span></label>
 <label><input type="radio" name="is_detonation_knock" value="n"><span>no [x]</span></label>
 <label><input type="radio" name="is_detonation_knock" value="unsure"><span>unsure [c]</span></label></div>
<div class="q"><b>Note (optional)</b><input type="text" name="note" style="flex:1;display:block"></div>
<p><button type="submit">Save &amp; next [Enter]</button><button type="button" id="back">Back [b]</button>
<span id="msg"></span></p>
<p class="kbd">space = play/pause · detonation = sharp metallic ping a few kHz up, under load, not a steady
lifter tick or deep rod knock. Use "unsure" freely; only clips both raters mark "yes" survive.</p>
</form>
<script>
let clip=null, hist=[];
const f=document.getElementById('f'), A=document.getElementById('audio');
async function load(id){
  const r=await fetch('/api/clip'+(id?('?id='+encodeURIComponent(id)):''));const j=await r.json();
  document.getElementById('rater').textContent=j.rater;
  if(j.done){document.getElementById('meta').textContent='All '+j.total+' clips rated. Thank you!';clip=null;return;}
  clip=j.clip; f.reset();
  if(j.previous){for(const [k,v] of Object.entries(j.previous)){const el=f.elements[k]; if(el) el.value=v;}}
  document.querySelector('.knock').style.display=clip.target_class==='knock'?'flex':'none';
  document.getElementById('meta').innerHTML=`clip ${j.position} of ${j.total} · ${j.rated} rated · downloaded as <b>${clip.target_class}</b> · `+
    `<a href="https://www.youtube.com/watch?v=${clip.video_id}&t=${Math.floor(clip.start)}s" target="_blank">YouTube @ ${clip.start}s</a>`;
  document.getElementById('spec').src='/spec/'+encodeURIComponent(clip.clip_id)+'.png';
  A.src='/audio/'+encodeURIComponent(clip.clip_id)+'.wav'; A.play().catch(()=>{});
  document.getElementById('msg').textContent='';
}
f.onsubmit=async e=>{e.preventDefault(); if(!clip) return;
  const body=Object.fromEntries(new FormData(f)); body.clip_id=clip.clip_id;
  const r=await fetch('/api/rate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json(); if(!j.ok){document.getElementById('msg').textContent=j.error;return;}
  hist.push(clip.clip_id); load();};
document.getElementById('back').onclick=()=>{if(hist.length) load(hist.pop());};
const keys={'1':['is_passenger_car','y'],'2':['is_passenger_car','n'],'3':['is_passenger_car','unsure'],
 'q':['plausibly_4cyl','y'],'w':['plausibly_4cyl','n'],'e':['plausibly_4cyl','unsure'],
 'a':['speech_or_music','y'],'s':['speech_or_music','n'],
 'z':['is_detonation_knock','y'],'x':['is_detonation_knock','n'],'c':['is_detonation_knock','unsure']};
document.addEventListener('keydown',e=>{ if(e.target.name==='note') {if(e.key==='Enter'){e.preventDefault();f.requestSubmit();} return;}
  if(keys[e.key]){f.elements[keys[e.key][0]].value=keys[e.key][1];}
  else if(e.key===' '){e.preventDefault(); A.paused?A.play():A.pause();}
  else if(e.key==='Enter'){e.preventDefault(); f.requestSubmit();}
  else if(e.key==='b'){document.getElementById('back').click();}});
load();
</script></body></html>"""


def make_handler(clips: list[dict], store: Store):
    by_id = {c["clip_id"]: c for c in clips}
    id_ok = re.compile(r"^[A-Za-z0-9_\-.]+$")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def json(self, obj, code: int = 200) -> None:
            self.send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                return self.send(200, PAGE.encode(), "text/html; charset=utf-8")
            if u.path == "/api/clip":
                want = parse_qs(u.query).get("id", [None])[0]
                rated = sum(1 for c in clips if c["clip_id"] in store.answers)
                if want in by_id:
                    clip = by_id[want]
                else:
                    clip = next((c for c in clips if c["clip_id"] not in store.answers), None)
                if clip is None:
                    return self.json({"done": True, "total": len(clips), "rater": store.rater})
                prev = store.answers.get(clip["clip_id"])
                return self.json({
                    "rater": store.rater, "total": len(clips), "rated": rated,
                    "position": clips.index(clip) + 1,
                    "clip": {k: clip[k] for k in ("clip_id", "video_id", "start", "target_class")},
                    "previous": {k: prev[k] for k in (*QUESTIONS, "note")} if prev else None,
                })
            m = re.match(r"^/(audio|spec)/(.+)\.(wav|png)$", u.path)
            if m and id_ok.match(m.group(2)) and m.group(2) in by_id:
                clip = by_id[m.group(2)]
                wav = REPO / clip["path"]
                if m.group(1) == "audio":
                    return self.send(200, wav.read_bytes(), "audio/wav")
                return self.send(200, spectrogram_png(wav, SPEC_DIR / f"{clip['clip_id']}.png"), "image/png")
            self.send(404, b"not found", "text/plain")

        def do_POST(self):
            if urlparse(self.path).path != "/api/rate":
                return self.send(404, b"not found", "text/plain")
            try:
                ans = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                clip = by_id[ans["clip_id"]]
                store.save(clip, ans)
                return self.json({"ok": True})
            except (KeyError, ValueError) as e:
                return self.json({"ok": False, "error": str(e)}, 400)

    return H


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rater", required=True, help="your name, letters/digits only (file data/vetting/<rater>.csv)")
    ap.add_argument("--classes", default="idling,engine,knock")
    ap.add_argument("--manifest", type=Path, default=EVAL / "audioset_manifest.csv")
    ap.add_argument("--limit", type=int, help="only the first N clips (in the shared order)")
    ap.add_argument("--seed", type=int, default=720)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.rater):
        raise SystemExit("--rater must be letters, digits, _ or -")

    clips = load_clips(args.manifest, set(args.classes.split(",")), args.seed, args.limit)
    if not clips:
        raise SystemExit(f"no downloaded clips for {args.classes} in {args.manifest}")
    store = Store(args.rater)
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(clips, store))
    url = f"http://{args.host}:{args.port}/"
    print(f"rater {args.rater}: {len(clips)} clips, {sum(c['clip_id'] in store.answers for c in clips)} rated. "
          f"Answers -> {store.path.relative_to(REPO)}\nOpen {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
