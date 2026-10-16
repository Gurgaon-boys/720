"""Tests for the data/ scripts: python -m pytest data/tests -q"""
from __future__ import annotations

import string
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import carobd  # noqa: E402
import carobd_split  # noqa: E402
import hf_parquet  # noqa: E402
import make_pools  # noqa: E402
from vet_merge import cohen_kappa, survives  # noqa: E402


# ---------------------------------------------------------------- hf_parquet
class LocalFile:
    """Stands in for hf_parquet.RemoteFile."""

    def __init__(self, path: Path):
        self.data = path.read_bytes()

    def read(self, start: int, end: int) -> bytes:
        return self.data[start:] if start < 0 else self.data[start:end]


def _write(tmp_path, n=37, **kw) -> tuple[Path, list[bytes | None]]:
    rng = __import__("random").Random(0)
    vids = sorted("".join(rng.choices(string.ascii_letters, k=11)) for _ in range(n))
    blobs = [None if i == 5 else bytes(rng.getrandbits(8) for _ in range(rng.randint(10, 3000))) for i in range(n)]
    t = pa.table({
        "video_id": vids,
        "audio": pa.array([{"bytes": b, "path": f"{v}.flac"} for b, v in zip(blobs, vids)],
                          pa.struct([("bytes", pa.binary()), ("path", pa.string())])),
    })
    p = tmp_path / "t.parquet"
    pq.write_table(t, p, row_group_size=16, write_page_index=True, data_page_size=4000, **kw)
    return p, vids, blobs


@pytest.mark.parametrize("kw", [
    {"use_dictionary": False, "compression": "snappy"},
    {"use_dictionary": True, "compression": "snappy"},
    {"use_dictionary": False, "compression": "zstd", "data_page_version": "2.0"},
    {"use_dictionary": False, "compression": "none"},
])
def test_parquet_reader_matches_pyarrow(tmp_path, kw):
    p, vids, blobs = _write(tmp_path, **kw)
    f = LocalFile(p)
    rgs = hf_parquet.read_footer_index(f)
    assert sum(rg["num_rows"] for rg in rgs) == len(vids)
    for rg in rgs:
        got_vids = hf_parquet.read_column_chunk(f, rg["cols"]["video_id"], 1)
        assert [v.decode() for v in got_vids] == vids[rg["first_row"]: rg["first_row"] + rg["num_rows"]]
        assert rg["vid_min"] == got_vids[0].decode() and rg["vid_max"] == got_vids[-1].decode()
        col = rg["cols"]["audio.bytes"]
        for r in range(rg["num_rows"]):
            assert hf_parquet.read_cell(f, col, r, 2) == blobs[rg["first_row"] + r]
        # whole-chunk path (used for shards without a page index)
        assert hf_parquet.read_column_chunk(f, col, 2) == blobs[rg["first_row"]: rg["first_row"] + rg["num_rows"]]


# ---------------------------------------------------------------- carOBD
def test_carobd_split_by_file_deterministic():
    sizes, types = {}, {}
    for t, n in zip(carobd.TYPES, (10, 4, 7, 3, 2)):
        for i in range(n):
            f = f"{t}{i + 1}.csv"
            sizes[f], types[f] = 1000 + 37 * i, t
    a = carobd_split.split(sizes, types, seed=1, frac=0.3)
    assert a == carobd_split.split(sizes, types, seed=1, frac=0.3)
    assert a != carobd_split.split(sizes, types, seed=2, frac=0.3)
    for t in carobd.TYPES:
        files = [f for f in sizes if types[f] == t]
        held = [f for f in files if f in a]
        assert 1 <= len(held) < len(files)
    frac = sum(sizes[f] for f in a) / sum(sizes.values())
    assert 0.2 < frac < 0.45


def test_carobd_loader_fixes_column_shift(tmp_path):
    p = tmp_path / "idle1.csv"
    p.write_text("ENGINE_RUN_TINE (),ENGINE_RPM (),VEHICLE_SPEED ()\n"
                 "10.000000, 850.000000, 0.000000,\n11.000000, 860.000000, 0.000000,\n")
    df = carobd.load(p)
    assert list(df["ENGINE_RPM"]) == [850, 860]
    assert list(df["ENGINE_RUN_TIME"]) == [10, 11] and list(df["t_s"]) == [0, 1]
    assert carobd.file_type(p) == "idle" and carobd.duration_s(df) == 1


# ---------------------------------------------------------------- vetting
def test_cohen_kappa():
    assert cohen_kappa(list("yynn"), list("yynn")) == 1
    # po = 2/4; pe = (3*1 + 1*2) / 16 = 5/16
    assert cohen_kappa(list("yyny"), list("ynnu")) == pytest.approx((0.5 - 5 / 16) / (1 - 5 / 16))
    assert cohen_kappa([], []) is None
    assert cohen_kappa(list("yyy"), list("yyy")) is None  # no variation: undefined


def test_survival_rule():
    ok = {"is_passenger_car": "y", "plausibly_4cyl": "y", "speech_or_music": "n", "is_detonation_knock": ""}
    assert survives({**ok, "target_class": "idling"})
    assert not survives({**ok, "plausibly_4cyl": "unsure", "target_class": "idling"})
    assert not survives({**ok, "target_class": "knock"})
    assert survives({**ok, "is_detonation_knock": "y", "target_class": "knock"})


# ---------------------------------------------------------------- pools
def test_pools_by_video_id():
    ids = [f"vid{i:05d}" for i in range(20000)]
    pools = [make_pools.pool_for(v, 720) for v in ids]
    assert pools == [make_pools.pool_for(v, 720) for v in ids]
    for name, frac in make_pools.POOLS:
        assert abs(pools.count(name) / len(ids) - frac) < 0.02
    clips = [{"clip_id": f"{v}_{s}", "video_id": v, "start": s, "target_class": "idling"}
             for v in ids[:300] for s in ("0", "30")]
    clips.append({"clip_id": "vid00007_90", "video_id": "vid00007", "start": "90", "target_class": "knock"})
    out, forced = make_pools.assign(clips, 720)
    make_pools.check_disjoint(out)  # raises on a violation
    where = {}
    for name in ("P1", "P2", "P3"):
        for e in out[name]:
            assert where.setdefault(e["video_id"], name) == name  # all segments of a video in one pool
    assert where["vid00007"] == "P3" and forced == 2  # shares a video with the knock check
    assert [e["clip_id"] for e in out["knock_check"]] == ["vid00007_90"]
    with pytest.raises(SystemExit):
        make_pools.check_disjoint({"P1": [{"video_id": "x"}], "knock_check": [{"video_id": "x"}]})


def _write_rater(path, rows):
    import csv as _csv
    from vet import FIELDS
    with open(path, "w", newline="") as fh:
        w = _csv.DictWriter(fh, FIELDS)
        w.writeheader()
        for clip, cls, ans in rows:
            w.writerow({"clip_id": clip, "video_id": clip.split("_")[0], "start": "0", "target_class": cls,
                        "rater": path.stem, "is_passenger_car": ans[0], "plausibly_4cyl": ans[1],
                        "speech_or_music": ans[2], "is_detonation_knock": ans[3] if cls == "knock" else "",
                        "note": "", "rated_at": "x"})


def test_vet_merge_modes(tmp_path, monkeypatch):
    import json as _json

    import vet_merge
    monkeypatch.setattr(vet_merge, "VET_DIR", tmp_path)
    monkeypatch.setattr(vet_merge, "OUT_CLIPS", tmp_path / "vetted_clips.csv")
    monkeypatch.setattr(vet_merge, "EVAL", tmp_path)
    good, bad = ("y", "y", "n", "y"), ("y", "n", "n", "n")
    _write_rater(tmp_path / "a.csv", [("v1_0", "idling", good), ("v2_0", "engine", good), ("v3_0", "knock", good),
                                      ("v4_0", "idling", bad)])
    _write_rater(tmp_path / "b.csv", [("v1_0", "idling", good), ("v2_0", "engine", bad)])

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["vet_merge.py", *argv])
        vet_merge.main()
        kept = (tmp_path / "vetted_clips.csv").read_text().splitlines()[1:]
        return sorted(r.split(",")[0] for r in kept), _json.loads((tmp_path / "vetting_agreement.json").read_text())

    kept, rep = run("--raters", "a")
    assert kept == ["v1_0", "v2_0", "v3_0"] and rep["mode"] == "single_rater" and rep["agreement"] is None
    kept, rep = run("--raters", "a", "b")
    assert kept == ["v1_0"] and rep["mode"] == "two_raters"
    kept, rep = run("--raters", "a", "--audit", "b")
    assert kept == ["v1_0", "v2_0", "v3_0"]  # the audit rater never removes clips
    assert rep["agreement"]["_audit"] == {"rater": "b", "overlap_clips": 2, "survival_agreement": 0.5}


def test_vet_merge_corrections_and_unsure_4cyl(tmp_path, monkeypatch):
    import json as _json

    import vet_merge
    monkeypatch.setattr(vet_merge, "VET_DIR", tmp_path)
    monkeypatch.setattr(vet_merge, "OUT_CLIPS", tmp_path / "vetted_clips.csv")
    monkeypatch.setattr(vet_merge, "EVAL", tmp_path)
    monkeypatch.setattr(vet_merge, "CORRECTIONS", {})
    # answered inverted: "y" on speech meant "clean"
    _write_rater(tmp_path / "a.csv", [("v1_0", "idling", ("y", "y", "y", "")), ("v2_0", "idling", ("y", "unsure", "y", "")),
                                      ("v3_0", "idling", ("y", "n", "y", "")), ("v4_0", "idling", ("y", "y", "n", ""))])
    (tmp_path / "a_corrections.json").write_text(_json.dumps(
        {"flip": {"speech_or_music": {"rated_at_lte": "x", "reason": "inverted"}}}))

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["vet_merge.py", "--raters", "a", *argv])
        vet_merge.main()
        rows = (tmp_path / "vetted_clips.csv").read_text().splitlines()
        return sorted(r.split(",")[0] for r in rows[1:]), rows[0]

    kept, header = run()
    assert kept == ["v1_0"] and header.endswith("plausibly_4cyl")  # v4 had real speech after the flip
    kept, _ = run("--accept-unsure-4cyl")
    assert kept == ["v1_0", "v2_0"]  # unsure accepted, "n" still rejected
