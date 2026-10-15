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

**Not in v1 (API ready):** knock, injector clog, valve tick (`inject` raises `NotImplementedError`), the block accelerometer, ECU/OBD loops, RPM jitter and sensor dropouts.
