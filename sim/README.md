# sim/

**Owner:** Dipen

Physics-informed engine simulator (plan section 6). It is the test bench and the healthy reference. It is not a "digital twin".

- 4-cylinder, 4-stroke, firing order 1-3-4-2, Wiebe combustion, crank dynamics on a 60-2 tooth wheel
- Realism: cycle-to-cycle variation, per-tooth spacing errors, idle-speed controller, randomised parameters with one held-out region, noise, RPM jitter and dropouts
- Streams: 48 kHz mic (synthesised firing plus P1 background texture only) and 48 kHz block accelerometer; ECU loops (fuel trim, knock retard, misfire counters)
- Faults (severity 0–1): misfire, knock, injector_clog; valve_tick is held out of ML training
- Steppable in ~0.5 s chunks with live commands (fault, cylinder, severity, sensor on/off)

OBD output ranges are calibrated against **carOBD calibration files only** (see `data/splits/`).

## v1 (`sim_version` 1.0.0): crank, combustion, microphone, misfire

```python
from sim import EngineSim, EngineParams

sim = EngineSim(EngineParams(), seed=0)       # or EngineParams.randomised(seed, region="train" | "test")
sim.inject("misfire", cylinder=3, severity=1.0)   # p(misfire) per cycle; applies from the next cycle
chunk = sim.step(0.5)                         # dict, see below
sim.clear_faults()
```

`step(duration_s, background=None)` returns:

| Key | Shape | Meaning |
|---|---|---|
| `t` | (N,) | time, s, on the 48 kHz grid |
| `crank_angle_deg` | (N,) | 0–720, 0 = cylinder 1 firing TDC |
| `rpm` | (N,) | instantaneous crank speed (ground truth) |
| `tooth_times`, `tooth_ids` | (M,) | 60-2 wheel crossings (ids 0–57; the gap shows as a 3-pitch interval) |
| `mic` | (N,) float32 | synthesised microphone |
| `cylinder_pressure` | (4, N) | Pa, ground truth, row i = cylinder i+1 |
| `labels` | list | one dict per engine cycle that started in this chunk: `cycle`, `t_start`, `fault`, `cylinder`, `severity`, `misfired` (4 bools), `q_scale` |
| `meta` | dict | `sim_version`, `seed`, `params`, TDC angles, wheel layout, active fault, MAP, `stalled`, units |

**Model:** slider-crank geometry; polytropic compression/expansion + Wiebe heat release (precomputed linear basis, so a cycle costs two array multiplies); gas torque through the crank; `J dω/dθ·ω = T_gas − T_load − T_friction(ω)` integrated in 0.25° steps and resampled to 48 kHz; idle PI controller on throttle (MAP, 800 rpm).
**Realism:** cycle-to-cycle heat-release variation (CoV 3%), fixed per-cylinder imbalance (1.5%), fixed per-tooth spacing errors, randomisable engines with a held-out region (structural-mode frequency scale).
**Mic:** each combustion excites 5 damped structural modes (1.2–7 kHz) scaled by the peak pressure rise; exhaust blowdown and intake pulses at firing frequency; coloured noise at a set SNR. A misfire creates **no combustion event**, so the pulse is genuinely missing. `background` mixes high-passed real texture (P1 pool) and is never used for firing.

```bash
python -m pytest            # sim/tests
python -m sim.demo_misfire  # writes docs/figures/sim_v1_misfire.png, prints half-order ratio and timing
```

*(v1 left knock, clog, valve tick, the accelerometer, the ECU/OBD loops and the harder regimes for v2, below.)*

## v2 (`sim_version` 2.0.0): accelerometer, ECU/OBD, all four faults, harder regimes

The v1 API is unchanged (`EngineSim(params, seed)`, `step`, `inject`, `clear_faults`, `set_operating_point`); v2 adds keyword arguments and output keys.

```python
from sim import EngineSim, EngineParams
from sim.params import Regimes

sim = EngineSim(EngineParams(), seed=0, regimes=Regimes.eval(), purpose="eval")   # regimes default OFF
sim.inject("knock", cylinder=2, severity=0.8)        # misfire | knock | injector_clog | valve_tick (held out)
chunk = sim.step(0.5)
```

**New outputs:** `accel` (48 kHz block accelerometer, which is also the knock sensor), `obd` (1 Hz samples of rpm, load_pct, throttle_pct, map_kpa, coolant_c, stft_pct, ltft_pct, spark_adv_deg and lambda), `ecu_fast` (the same values once per firing, `sim_internal`), `dropouts`, and per-cycle labels `knocked`, `knock_intensity`, `clog`, `valve_lash` and `held_out`. With mic and the crank, that covers the plan's 12 quantities.

| Piece | Model |
|---|---|
| Accelerometer | Structure-borne combustion response (5 modes, 1.1–8 kHz), knock bursts, valve-closing impacts, coloured noise at `accel_snr_db` |
| Knock | Damped chamber modes after peak heat release (Draper: ~6.4 / 10.6 / 13.3 kHz for an 80 mm bore), probability = severity × exp(−retard / 4°). The ECU retards that cylinder 3° per knock (max 12°) and recovers at 0.5°/s, so knock suppresses itself and shows in `spark_adv_deg` |
| Spark | Combustion starts at the engine's timing (+ knock retard); reported advance adds the ignition delay (1.2 ms of crank angle) |
| Injector clog | That cylinder's fuel × (1 − s): lean, less heat, higher CoV. The lambda sensor sees the exhaust mix (80 ms transport, 20 ms mixing), so STFT integrates 1 − 1/λ and LTFT takes over slowly. Steady trim = s/(4 − s) ≈ s/4 (tested); lambda ripples once per 720° (simulator only: carOBD has no O2) |
| Valve tick | Louder (× (1 + 10 s)) and earlier (up to 6°) intake-valve seat impact once per 720°. **Held out:** labelled `held_out`, refused by `purpose="calibration"/"training"` and by `sim/guard.py` |
| Reciprocating inertia | −m x″ x′ ω² per cylinder (0.45 kg), exact slider-crank. It dominates the 2nd order at high rpm, as on a real inline-4 |
| Regimes (`Regimes.eval()`) | Tooth jitter 2 µs; crank torsional flex 0.02° × (rpm/1000)^1.5 at the wheel; accessory-torque noise 1 N m; sensor dropouts 0.3/min per channel (NaN audio, missing teeth or OBD) |

Every new random source has its own seeded stream, drawn whether or not a fault is active, so v1's streams are untouched and every fault at severity 0 is bit-identical to healthy (tested). ECU/OBD values marked `# TODO carOBD-calib` in `sim/params.py` are engineering defaults: `data/splits/` has no carOBD split yet.

```bash
python -m sim.demo_v2     # docs/figures/sim_v2_knock.png, sim_v2_clog_trim.png, sim_v2_valve_tick.png
```

