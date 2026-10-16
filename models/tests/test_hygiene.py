"""Split hygiene for the L2 models: seeds, the held-out fault, the held-out carOBD files."""

import json

import pytest

from eval.common import SEEDS
from models.make_dataset import CLASSES, MANIFEST, plan
from sim import EngineParams, EngineSim
from sim.engine import HeldOutFaultError


def test_no_engine_seed_in_two_ranges():
    used = [e for engines, _ in SEEDS.values() for e in engines]
    assert len(used) == len(set(used))


def test_dataset_plan_splits_by_engine_and_never_holds_valve_tick():
    jobs = plan()
    by_split = {}
    for j in jobs:
        by_split.setdefault(j["split"], set()).add(j["engine"])
        assert j["fault"] in CLASSES and j["fault"] != "valve_tick"
    assert by_split["train"].isdisjoint(by_split["val"]) and by_split["train"].isdisjoint(by_split["test"])
    assert by_split["val"].isdisjoint(by_split["test"])
    assert by_split["test_heldout_rpm"] <= by_split["test"]                 # held-out rpm only on test engines
    assert all(3000 <= j["rpm"] <= 3500 for j in jobs if j["split"] == "test_heldout_rpm")
    assert all(j["rpm"] < 3000 for j in jobs if j["split"] != "test_heldout_rpm")
    runs = [j["run_seed"] for j in jobs]
    assert len(runs) == len(set(runs))


@pytest.mark.skipif(not MANIFEST.exists(), reason="run python -m models.make_dataset")
def test_committed_manifest_matches_the_plan():
    man = json.loads(MANIFEST.read_text())
    assert "valve_tick" not in {r["fault"] for r in man["runs"]}
    assert {r["run_id"] for r in man["runs"]} == {j["run_id"] for j in plan()}


def test_training_purpose_refuses_valve_tick():
    with pytest.raises(HeldOutFaultError):
        EngineSim(EngineParams(), 0, purpose="training").inject("valve_tick", 1, 0.5)


def test_carobd_calibration_code_never_reads_heldout_files(monkeypatch, tmp_path):
    from data.carobd import CAROBD_DIR
    if not CAROBD_DIR.exists():
        pytest.skip("carOBD not cloned (see data/README.md)")
    import dsp.calibrate_trim as ct
    import sim.calibrate_ecu as ce
    split = json.loads((ce.SPLIT).read_text())
    heldout, calib = set(split["heldout"]), set(split["calibration"])
    assert heldout.isdisjoint(calib)
    read = []
    real_load = ce.load

    def spy(path):
        read.append(path.name)
        return real_load(path)

    monkeypatch.setattr(ce, "load", spy)
    monkeypatch.setattr(ct, "load", spy)
    monkeypatch.setattr(ce, "OUT", tmp_path / "ecu.json")
    ce.main()
    for f in ce.calibration_files()[:10]:
        ct.carobd_windows(f)
    assert read and set(read) <= calib and not (set(read) & heldout)
