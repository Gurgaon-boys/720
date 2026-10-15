"""Fetch AudioSet "Idling", "Engine" and "Engine knocking" clips as 48 kHz mono WAV.

Source: the Hugging Face mirror agkphysics/AudioSet (FLAC, mostly 48 kHz,
~10% 44.1 kHz, downloaded from YouTube in March 2023, so there are no dead links
to chase). Only the parquet pages holding our clips are fetched (see
hf_parquet.py). Clips that the mirror lacks can optionally be tried on YouTube
with yt-dlp + ffmpeg (--youtube-fallback).

Class ids (mids) are looked up by name in ontology.json, never hard-coded.
Each clip gets exactly one target class, by priority:
    knock (has "Engine knocking") > idling (has "Idling") > engine (has "Engine").
Within a class, clips are shuffled with --seed and taken in that order until the
cap of clips available on the mirror is reached (--cap-knock 0 = all).

Which AudioSet splits are used (--curated-for, default "knock"):
  * knock:  eval + balanced_train + unbalanced_train (we want every clip).
  * idling, engine: unbalanced_train only. The mirror's eval/balanced shards have
    no page index and store a row group's audio as one ~140 MB page, so a single
    clip costs ~140 MB there versus ~3 MB in unbalanced_train. AudioSet's splits
    share one annotation process, so this does not lower label quality, and the
    caps (1500) are far below the unbalanced candidates (~6k idling, ~16k engine).

Outputs
    data/raw/audioset/<video_id>_<start>.wav   float32, 48 kHz, mono (git-ignored)
    eval/audioset_manifest.csv                 one row per clip that was tried
    data/raw/audioset/_meta/fetch.log          failures with tracebacks

Resumable: rerunning skips clips whose WAV exists and whose status is ok.

    python data/audioset_fetch.py                      # default caps 1500/1500/all
    python data/audioset_fetch.py --cap-idling 20 --cap-engine 20 --cap-knock 10
"""
from __future__ import annotations

import argparse
import bisect
import csv
import io
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import EVAL, RAW, REPO, utc_now_iso  # noqa: E402
from hf_parquet import RemoteFile, read_cell, read_column_chunk, read_footer_index, read_offset_index  # noqa: E402

HF_REPO = "agkphysics/AudioSet"
HF_SPLITS = {"eval": "data/eval", "bal_train": "data/bal_train", "unbal_train": "data/unbal_train"}
SEGMENT_CSVS = {
    "eval": "http://storage.googleapis.com/us_audioset/youtube_corpus/v1/csv/eval_segments.csv",
    "bal_train": "http://storage.googleapis.com/us_audioset/youtube_corpus/v1/csv/balanced_train_segments.csv",
    "unbal_train": "http://storage.googleapis.com/us_audioset/youtube_corpus/v1/csv/unbalanced_train_segments.csv",
}
CLASS_NAMES = {"knock": "Engine knocking", "idling": "Idling", "engine": "Engine"}
TARGET_SR = 48_000
OUT_DIR = RAW / "audioset"
META = OUT_DIR / "_meta"
MANIFEST = EVAL / "audioset_manifest.csv"
FIELDS = ["video_id", "start", "end", "split", "target_class", "labels", "label_names", "source",
          "original_sr", "original_channels", "duration_s", "status", "error", "path", "fetched_at"]
# audio.bytes sits in an optional struct -> max definition level 2; video_id is a top-level optional string -> 1
AUDIO_MAX_DEF, VID_MAX_DEF = 2, 1

log = logging.getLogger("audioset_fetch")


# --------------------------------------------------------------------------
# Metadata: ontology, segment CSVs, mirror index
# --------------------------------------------------------------------------
def download(url: str, dest: Path) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=300) as r, open(tmp, "wb") as fh:
            shutil.copyfileobj(r, fh)
        os.replace(tmp, dest)
    return dest


def lookup_mids() -> tuple[dict[str, str], dict[str, str]]:
    """Target class -> mid, and mid -> display name for every class."""
    path = download(f"https://huggingface.co/datasets/{HF_REPO}/resolve/main/ontology.json", META / "ontology.json")
    onto = json.loads(path.read_text())
    names = {e["id"]: e["name"] for e in onto}
    mids = {}
    for cls, name in CLASS_NAMES.items():
        hits = [mid for mid, n in names.items() if n == name]
        if len(hits) != 1:
            raise SystemExit(f"ontology: expected exactly one class named {name!r}, found {hits}")
        mids[cls] = hits[0]
    return mids, names


def read_segments() -> list[dict]:
    segs = []
    for split, url in SEGMENT_CSVS.items():
        path = download(url, META / Path(url).name)
        with open(path) as fh:
            for line in fh:
                if line.startswith("#"):
                    continue
                ytid, start, end, labels = [x.strip() for x in line.split(",", 3)]
                segs.append({"video_id": ytid, "start": float(start), "end": float(end), "split": split,
                             "labels": labels.strip('"').split(",")})
    return segs


def build_mirror_index(split: str, workers: int) -> list[dict]:
    """Row-group index of one mirror split (cached): shard, rg, first_row, vid_min, vid_max, cols."""
    cache = META / f"hf_index_{split}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    from huggingface_hub import HfApi

    files = sorted(e.path for e in HfApi().list_repo_tree(HF_REPO, path_in_repo=HF_SPLITS[split], repo_type="dataset")
                   if e.path.endswith(".parquet"))
    log.info("indexing %d shards of %s", len(files), split)
    out: dict[str, list] = {}
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(read_footer_index, RemoteFile(HF_REPO, p)): p for p in files}
        for i, fut in enumerate(as_completed(futs), 1):
            out[futs[fut]] = fut.result()
            if i % 100 == 0:
                log.info("  %d/%d footers", i, len(files))
    rows = []
    for shard in files:
        for k, rg in enumerate(out[shard]):
            rows.append({"shard": shard, "rg": k, **rg})
    rows.sort(key=lambda r: r["vid_min"])
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows))
    return rows


class Mirror:
    """Finds clips in the HF mirror and fetches their FLAC bytes."""

    def __init__(self, workers: int):
        self.idx = {s: build_mirror_index(s, workers) for s in HF_SPLITS}
        self.mins = {s: [r["vid_min"] for r in rows] for s, rows in self.idx.items()}
        # bisect lookup needs disjoint, sorted row-group ranges; otherwise scan linearly
        self.disjoint = {s: all(a["vid_max"] < b["vid_min"] for a, b in zip(rows, rows[1:]))
                         for s, rows in self.idx.items()}
        for s, ok in self.disjoint.items():
            if not ok:
                log.warning("mirror split %s has overlapping row-group ranges; using linear lookup", s)
        self._files: dict[str, RemoteFile] = {}
        self._vids: dict[tuple, list[str]] = {}
        self._oidx: dict[tuple, list] = {}
        self._rg_rows: dict[tuple, dict[int, bytes]] = {}
        self._rg_locks: dict[tuple, threading.Lock] = {}
        self._lock = threading.Lock()
        self.wanted: set[str] = set()  # video ids we may fetch (kept from whole-row-group downloads)

    def file(self, shard: str) -> RemoteFile:
        with self._lock:
            if shard not in self._files:
                self._files[shard] = RemoteFile(HF_REPO, shard)
            return self._files[shard]

    def locate(self, split: str, vid: str):
        """(row_group_entry, row_in_group) or None if the mirror lacks the clip."""
        if self.disjoint[split]:
            i = bisect.bisect_right(self.mins[split], vid) - 1
            groups = self.idx[split][max(0, i): i + 1]
        else:
            groups = self.idx[split]
        for rg in groups:
            if not rg["vid_min"] <= vid <= rg["vid_max"]:
                continue
            key = (rg["shard"], rg["rg"])
            if key not in self._vids:
                vids = read_column_chunk(self.file(rg["shard"]), rg["cols"]["video_id"], VID_MAX_DEF)
                self._vids[key] = [v.decode() if v else "" for v in vids]
            if vid in self._vids[key]:
                return rg, self._vids[key].index(vid)
        return None

    def fetch(self, rg: dict, row: int) -> bytes:
        f = self.file(rg["shard"])
        col = rg["cols"]["audio.bytes"]
        key = (rg["shard"], rg["rg"])
        if col["offset_index_offset"]:
            if key not in self._oidx:
                self._oidx[key] = read_offset_index(f, col)
            data = read_cell(f, col, row, AUDIO_MAX_DEF, self._oidx[key])
        else:
            # eval/bal_train shards: no page index, and the whole row group's audio is one
            # ~140 MB dictionary page. Download it once and keep only the rows we want.
            with self._lock:
                rg_lock = self._rg_locks.setdefault(key, threading.Lock())
            with rg_lock:
                if key not in self._rg_rows:
                    rows = read_column_chunk(f, col, AUDIO_MAX_DEF)
                    self._rg_rows[key] = {i: rows[i] for i, v in enumerate(self._vids[key]) if v in self.wanted}
                    del rows
            data = self._rg_rows[key].get(row)
        if not data or data[:4] != b"fLaC":
            raise ValueError("cell is not FLAC")
        return data


# --------------------------------------------------------------------------
# Audio conversion
# --------------------------------------------------------------------------
def to_48k_mono(x: np.ndarray, sr: int) -> np.ndarray:
    x = x.mean(axis=1) if x.ndim == 2 else x
    if sr != TARGET_SR:
        g = gcd(sr, TARGET_SR)
        x = resample_poly(x, TARGET_SR // g, sr // g)
    return x.astype(np.float32)


def youtube_fetch(vid: str, start: float, end: float) -> tuple[np.ndarray, int, int]:
    """Download only [start, end) of a YouTube video. Returns (audio, original_sr, channels)."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "clip.%(ext)s"
        cmd = [sys.executable, "-m", "yt_dlp", "-q", "--no-warnings", "-f", "bestaudio/best",
               "--download-sections", f"*{start}-{end}", "-o", str(out), f"https://www.youtube.com/watch?v={vid}"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        got = list(Path(td).glob("clip.*"))
        if r.returncode or not got:
            raise RuntimeError((r.stderr or r.stdout or "yt-dlp failed").strip().splitlines()[-1][:300])
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                                "stream=sample_rate,channels", "-of", "csv=p=0", str(got[0])],
                               capture_output=True, text=True)
        sr, ch = (int(v) for v in probe.stdout.strip().split(",")[:2])
        wav = Path(td) / "clip.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(got[0]), "-ac", "1", "-ar", str(TARGET_SR),
                        "-c:a", "pcm_f32le", str(wav)], check=True)
        x, _ = sf.read(wav, dtype="float32")
        return x, sr, ch


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------
def start_tag(start: float) -> str:
    return f"{start:.3f}".rstrip("0").rstrip(".")


def load_manifest() -> dict[str, dict]:
    if not MANIFEST.exists():
        return {}
    with open(MANIFEST, newline="") as fh:
        return {f"{r['video_id']}_{r['start']}": r for r in csv.DictReader(fh)}


def save_manifest(rows: dict[str, dict]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".tmp")
    order = {"knock": 0, "idling": 1, "engine": 2}
    with open(tmp, "w", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS)
        w.writeheader()
        for r in sorted(rows.values(), key=lambda r: (order.get(r["target_class"], 9), r["video_id"], r["start"])):
            w.writerow({k: r.get(k, "") for k in FIELDS})
    os.replace(tmp, MANIFEST)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def candidates_by_class(segs: list[dict], mids: dict[str, str], seed: int,
                        curated_for: set[str]) -> dict[str, list[dict]]:
    out = {c: [] for c in CLASS_NAMES}
    for s in segs:
        labs = set(s["labels"])
        cls = next((c for c in ("knock", "idling", "engine") if mids[c] in labs), None)
        if cls:
            out[cls].append(s)
    rng = random.Random(seed)
    for cls, lst in out.items():
        lst.sort(key=lambda s: (s["split"], s["video_id"], s["start"]))
        curated = [s for s in lst if s["split"] != "unbal_train"] if cls in curated_for else []
        unbal = [s for s in lst if s["split"] == "unbal_train"]
        rng.shuffle(curated)
        rng.shuffle(unbal)
        out[cls] = curated + unbal
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cap-idling", type=int, default=1500)
    ap.add_argument("--cap-engine", type=int, default=1500)
    ap.add_argument("--cap-knock", type=int, default=0, help="0 = all")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=720)
    ap.add_argument("--youtube-fallback", action="store_true",
                    help="try yt-dlp for selected clips the mirror lacks (knock only by default)")
    ap.add_argument("--youtube-classes", default="knock")
    ap.add_argument("--curated-for", default="knock",
                    help="classes that also use the eval/balanced splits (expensive, see docstring)")
    ap.add_argument("--order", default="idling,engine,knock",
                    help="class processing order (eval/balanced knock row groups are the slow part)")
    ap.add_argument("--retry-failed", action="store_true", help="retry rows with status failed")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    META.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(META / "fetch.log")])

    mids, names = lookup_mids()
    log.info("mids: %s", {CLASS_NAMES[c]: m for c, m in mids.items()})
    cands = candidates_by_class(read_segments(), mids, args.seed, set(args.curated_for.split(",")))
    log.info("candidates in segment CSVs: %s", {c: len(v) for c, v in cands.items()})
    caps = {"knock": args.cap_knock or 10**9, "idling": args.cap_idling, "engine": args.cap_engine}

    mirror = Mirror(args.workers)
    mirror.wanted = {s["video_id"] for lst in cands.values() for s in lst}
    manifest = load_manifest()
    mlock = threading.Lock()
    done_since_save = [0]

    def record(seg: dict, cls: str, **kw) -> None:
        key = f"{seg['video_id']}_{start_tag(seg['start'])}"
        row = {"video_id": seg["video_id"], "start": start_tag(seg["start"]), "end": start_tag(seg["end"]),
               "split": seg["split"], "target_class": cls, "labels": ";".join(seg["labels"]),
               "label_names": ";".join(names.get(m, m) for m in seg["labels"]), "fetched_at": utc_now_iso(), **kw}
        with mlock:
            manifest[key] = row
            done_since_save[0] += 1
            if done_since_save[0] >= 25:
                save_manifest(manifest)
                done_since_save[0] = 0

    def is_done(seg) -> bool:
        r = manifest.get(f"{seg['video_id']}_{start_tag(seg['start'])}")
        if not r:
            return False
        if r["status"] == "ok":
            return (REPO / r["path"]).exists()
        if r["status"] == "failed":
            return not args.retry_failed
        return r["status"] == "missing_on_hf"  # over_cap clips may be needed again if the cap grows

    def work(seg: dict, cls: str) -> str:
        """Returns the status written to the manifest."""
        vid, tag = seg["video_id"], start_tag(seg["start"])
        dest = OUT_DIR / f"{vid}_{tag}.wav"
        rel = str(dest.relative_to(REPO))
        source = "hf"
        try:
            hit = mirror.locate(seg["split"], vid)
            if hit is None:
                if not (args.youtube_fallback and cls in args.youtube_classes.split(",")):
                    record(seg, cls, source="hf", status="missing_on_hf", error="not in mirror (Mar 2023 snapshot)")
                    return "missing_on_hf"
                source = "youtube"
                x, sr, ch = youtube_fetch(vid, seg["start"], seg["end"])
            else:
                data = mirror.fetch(*hit)
                raw, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
                ch = raw.shape[1]
                x = to_48k_mono(raw, sr)
            if len(x) < TARGET_SR:  # under 1 s of audio is useless
                raise ValueError(f"too short: {len(x)} samples")
            tmp = dest.with_suffix(".part.wav")
            sf.write(tmp, x, TARGET_SR, subtype="FLOAT")
            os.replace(tmp, dest)
            record(seg, cls, source=source, original_sr=sr, original_channels=ch,
                   duration_s=f"{len(x) / TARGET_SR:.3f}", status="ok", path=rel)
            return "ok"
        except Exception as e:  # noqa: BLE001 - every failure is logged and recorded
            log.warning("failed %s_%s: %s\n%s", vid, tag, e, traceback.format_exc(limit=3))
            record(seg, cls, source=source, status="failed", error=str(e)[:300])
            return "failed"

    t0 = time.time()
    with ThreadPoolExecutor(args.workers) as ex:
        for cls in args.order.split(","):
            ok = sum(1 for r in manifest.values() if r["target_class"] == cls and r["status"] == "ok"
                     and (REPO / r["path"]).exists())
            pending = [s for s in cands[cls] if not is_done(s)]
            if caps[cls] >= 10**9:  # uncapped: order can't change the selection, so do cheap clips first
                pending.sort(key=lambda s: s["split"] != "unbal_train")
            log.info("%s: %d ok already, cap %s, %d candidates left", cls, ok,
                     "all" if caps[cls] >= 10**9 else caps[cls], len(pending))
            pos = 0
            while ok < caps[cls] and pos < len(pending):
                need = caps[cls] - ok
                batch = pending[pos: pos + max(int(need * 1.25) + 8, 16)]
                pos += len(batch)
                results = list(ex.map(lambda s: work(s, cls), batch))
                ok += results.count("ok")
                log.info("%s: %d ok (%d/%d candidates tried, %.0f s)", cls, ok, pos, len(pending), time.time() - t0)
            # trim overshoot so the cap is exact and reproducible: keep the first `cap` ok clips in priority order
            if ok > caps[cls]:
                order = {f"{s['video_id']}_{start_tag(s['start'])}": i for i, s in enumerate(cands[cls])}
                oks = sorted((k for k, r in manifest.items() if r["target_class"] == cls and r["status"] == "ok"),
                             key=lambda k: order.get(k, 10**9))
                for k in oks[caps[cls]:]:
                    (REPO / manifest[k]["path"]).unlink(missing_ok=True)
                    manifest[k].update(status="over_cap", path="")
    save_manifest(manifest)

    summary: dict[str, dict[str, int]] = {}
    for r in manifest.values():
        summary.setdefault(r["target_class"], {}).setdefault(r["status"], 0)
        summary[r["target_class"]][r["status"]] += 1
    log.info("done in %.0f s. manifest %s", time.time() - t0, MANIFEST)
    for cls, st in summary.items():
        log.info("  %-7s %s", cls, st)


if __name__ == "__main__":
    main()
