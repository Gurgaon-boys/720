"""Physics-informed engine simulator, v2 (plan section 6).

v1: crank + combustion dynamics, 60-2 trigger wheel, synthesised microphone, misfire.
v2 adds: the block accelerometer (it IS the knock sensor), the slow ECU/OBD layer (spark advance,
knock control, closed-loop fuel trim on a lambda sensor, coolant, throttle, load), the faults knock,
injector clog and valve tick (held out), and optional harder regimes (tooth jitter, crank torsional
flex, sensor dropouts) for evaluation. All v2 additions default to OFF or to neutral behaviour,
so v1 callers keep working; see sim/README.md.

Conventions
-----------
- Crank angle 0 deg = cylinder 1 firing TDC. One engine cycle = 720 deg. Firing order 1-3-4-2,
  so firing TDCs are at 0 (cyl 1), 180 (cyl 3), 360 (cyl 4), 540 (cyl 2).
- Combustion event e (e = 0, 1, 2, ...) fires at absolute crank angle 180*e on cylinder
  firing_order[e % 4]; it belongs to engine cycle e // 4.
- The engine is integrated in the crank-angle domain, one 180 deg firing segment at a time, and
  resampled to a 48 kHz time grid. Within a segment the four cylinders are, in order:
  expansion (event s), compression (event s+1), exhaust (event s-1) and intake (event s+2).
- Units are SI (Pa, m, s, rad/s) unless a field name says otherwise.

Cylinder pressure model
-----------------------
Closed cycle (IVC at BDC to EVO at BDC) uses polytropic compression/expansion with Wiebe heat
release. The first law  dp/dphi = (k-1)/V dQ/dphi - k p/V dV/dphi  is linear in p, so
    p(phi) = p_ivc * Pm(phi) + Q * Pc(phi)
with the motored curve Pm and the pressure per joule Pc precomputed per engine; v2 keeps one Pc per
start-of-combustion angle (spark advance and knock retard move it). A misfire is Q = 0: the cylinder
is motored and produces no combustion pulse. Gas exchange strokes sit at exhaust back-pressure and
manifold pressure (MAP), which gives pumping losses for free.

Fuel and lambda
---------------
Air per cylinder is equal; fuel to cylinder k is (bank trim) x (1 - clog_k). Its equivalence ratio
phi_k sets the heat released (fuel-limited when lean, air-limited when rich: x min(phi_k, 1)). The
lambda sensor sees the exhaust mix (phi averaged over arriving slugs, after a transport delay), so
the closed loop drives the bank's mean phi to 1: with one cylinder clogged by s the steady-state
trim is (1 + T)(1 - s/4) = 1, i.e. T = s / (4 - s), about s/4.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace

import numpy as np
from scipy import signal as sps
from scipy.integrate import cumulative_trapezoid

from sim.params import HELD_OUT_FAULTS, SLOW_SENSORS, EcuParams, Regimes

SIM_VERSION = "2.1.1"   # 2.1.1 = 2.1.0 + cam pulse + 3 slow OBD sensors; every 2.1.0 channel bit-identical (tested)
ACCEL_RMS_TAU_S = 1.0     # running RMS behind the accelerometer's noise level
FS = 48_000

SUPPORTED_FAULTS = ("misfire", "knock", "injector_clog", "valve_tick")
PLANNED_FAULTS: tuple = ()
PURPOSES = ("general", "demo", "eval", "calibration", "training")
# OBD-II (SAE J1979) PID resolution: trims 100/128 %, MAP 1 kPa, coolant 1 degC, timing 0.5 deg, throttle/load 100/255 %
OBD_RESOLUTION = {"rpm": 0.25, "load_pct": 100 / 255, "throttle_pct": 100 / 255, "map_kpa": 1.0, "coolant_c": 1.0,
                  "stft_pct": 100 / 128, "ltft_pct": 100 / 128, "spark_adv_deg": 0.5}
OBD_CHANNELS = ("rpm", "load_pct", "throttle_pct", "map_kpa", "coolant_c", "stft_pct", "ltft_pct",
                "spark_adv_deg", "lambda")

# L3 additions. Cam: one pulse per 720 deg at this crank angle (cylinder 1 firing TDC = 0); DSP derives the
# cycle phase from it (dsp/windows.py). Slow OBD sensors: own round-robin stagger and own random stream,
# so the 2.1.0 channels are unchanged.
CAM_OFFSET_DEG = 90.0
BLOCK_S = 0.5             # with regimes on, the stream is generated in blocks of this length (see EngineSim.step)
SLOW_CHANNELS = ("intake_air_temp_c", "battery_v", "baro_kpa")
# every variable, by kind: 11 physical sensors + 4 ECU-computed values = 15. "real_calibrated" = constants
# measured on carOBD calibration files; "sim_only" = no real counterpart in carOBD (simulator physics only)
SENSORS = {
    "mic": ("physical", "sim_only"), "accel": ("physical", "sim_only"), "crank": ("physical", "sim_only"),
    "cam": ("physical", "sim_only"), "map_kpa": ("physical", "real_calibrated"),
    "throttle_pct": ("physical", "real_calibrated"), "coolant_c": ("physical", "real_calibrated"),
    "lambda": ("physical", "sim_only"), "intake_air_temp_c": ("physical", "real_calibrated"),
    "battery_v": ("physical", "real_calibrated"), "baro_kpa": ("physical", "real_calibrated"),
    "load_pct": ("ecu_computed", "real_calibrated"), "stft_pct": ("ecu_computed", "real_calibrated"),
    "ltft_pct": ("ecu_computed", "real_calibrated"), "spark_adv_deg": ("ecu_computed", "real_calibrated"),
}
# rpm is derived from the crank sensor; reported on OBD, calibrated on carOBD idle
CHANNEL_CALIBRATION = {"rpm": "real_calibrated", **{k: v[1] for k, v in SENSORS.items()}}

_UNCHANGED = object()


class HeldOutFaultError(RuntimeError):
    """A held-out fault (valve_tick) was requested where it must never appear (calibration, training)."""


@dataclass(frozen=True)
class EngineParams:
    # geometry
    bore_m: float = 0.080
    stroke_m: float = 0.080
    conrod_m: float = 0.140
    compression_ratio: float = 10.0
    firing_order: tuple = (1, 3, 4, 2)

    # thermodynamics and combustion
    kappa: float = 1.30                 # polytropic exponent
    p_exhaust_pa: float = 1.05e5
    p_crankcase_pa: float = 1.0e5
    q_per_pa: float = 0.0055            # heat released per Pa of MAP (charge x fuel energy x efficiency), J/Pa
    wiebe_a: float = 5.0
    wiebe_m: float = 2.0
    wiebe_duration_deg: float = 50.0
    soc_deg: float = -8.0               # start of combustion relative to firing TDC at idle timing
    ccv_cov: float = 0.03               # cycle-to-cycle variation of heat release (coefficient of variation)
    cyl_imbalance_std: float = 0.015    # fixed per-cylinder heat-release offsets (engine property)

    # crank dynamics
    recip_mass_kg: float = 0.45         # reciprocating mass per cylinder (piston, pin, rings, small end); v2
    inertia_kgm2: float = 0.15
    friction_c0_nm: float = 12.0
    friction_c1_nms: float = 0.02       # friction torque = c0 + c1 * omega
    load_nm: float = 5.0                # accessory load
    fuel_bias: float = 0.0              # injector-flow / airflow error the ECU's LTFT has learned out (v2.1)

    # idle-speed PI controller acting on throttle (MAP)
    rpm_target: float = 800.0
    kp_pa_per_rpm: float = 60.0
    ki_pa_per_rpm_s: float = 120.0
    map_tau_s: float = 0.05             # manifold filling time constant
    map_min_pa: float = 0.20e5
    map_max_pa: float = 1.00e5
    map_init_pa: float = 0.34e5

    # 60-2 trigger wheel
    wheel_teeth: int = 60
    wheel_missing: int = 2
    wheel_first_tooth_deg: float = -84.0   # first tooth after the gap, relative to cyl 1 TDC
    tooth_error_std_deg: float = 0.05      # fixed per-tooth spacing errors

    # identity of the physical engine: tooth errors and cylinder imbalance come from this, not the run seed
    engine_seed: int = 720

    # synthesised microphone
    mode_freqs_hz: tuple = (1400.0, 2500.0, 3700.0, 4900.0, 5900.0)  # held-out region still < 7 kHz
    mode_taus_s: tuple = (0.006, 0.004, 0.003, 0.0025, 0.002)
    mode_gains: tuple = (1.0, 0.8, 0.6, 0.45, 0.3)
    mode_jitter: float = 0.15           # per-event random amplitude jitter of each mode
    blowdown_freq_hz: float = 110.0
    blowdown_tau_s: float = 0.008
    blowdown_gain: float = 0.25
    intake_freq_hz: float = 210.0
    intake_tau_s: float = 0.005
    intake_gain: float = 0.05
    mic_gain: float = 0.05              # overall scale: output units per MPa of peak pressure rise
    snr_db: float = 25.0                # coloured noise relative to the clean mic RMS of each chunk
    noise_pole: float = 0.97            # AR(1) pole of the coloured part of the noise
    bg_highpass_hz: float = 1500.0      # background texture is high-passed before mixing
    bg_gain: float = 1.0

    # numerics
    dtheta_deg: float = 0.25

    # ---- v2: block accelerometer (the knock sensor), structure-borne combustion response
    accel_mode_freqs_hz: tuple = (1100.0, 2300.0, 3600.0, 5200.0, 8000.0)
    accel_mode_taus_s: tuple = (0.004, 0.003, 0.002, 0.0015, 0.001)
    accel_mode_gains: tuple = (1.0, 0.9, 0.7, 0.5, 0.35)
    accel_gain: float = 0.05            # output units per MPa of peak pressure rise
    accel_snr_db: float = 25.0

    # ---- v2: knock = chamber acoustic modes (Draper: f = rho_mn c / (pi bore))
    knock_sound_speed_mps: float = 870.0  # burnt-gas sound speed; bore 80 mm -> first mode ~6.4 kHz
    knock_mode_rho: tuple = (1.841, 3.054, 3.832)   # (1,0), (2,0), (0,1) modes -> ~6.4, 10.6, 13.3 kHz
    knock_mode_gains: tuple = (1.0, 0.55, 0.35)
    knock_mode_taus_s: tuple = (0.0009, 0.0006, 0.0005)
    knock_gain: float = 0.08            # accel units per unit knock intensity
    knock_mic_ratio: float = 0.15       # knock is much weaker in the airborne mic
    knock_delay_deg: float = 3.0        # after peak heat release
    knock_jitter_deg: float = 2.0

    # ---- v2: valve closing impacts (intake valve closing), present on a healthy engine too
    valve_ivc_deg: float = -140.0       # relative to that cylinder's firing TDC (40 deg ABDC)
    valve_modes_hz: tuple = (6500.0, 9500.0, 13000.0)
    valve_tau_s: float = 0.0002
    valve_gain: float = 0.004           # accel units, healthy seat impact
    valve_mic_ratio: float = 0.3
    valve_tick_gain: float = 10.0       # excess lash: amplitude x (1 + gain x severity)
    valve_tick_advance_deg: float = 6.0  # and the valve closes earlier by up to this

    @classmethod
    def randomised(cls, seed: int, region: str = "train", **overrides) -> "EngineParams":
        """Draw a random engine. `region="test"` draws from the held-out parameter region.

        The held-out region is the structural-mode frequency scale (train [0.90, 1.05], test
        (1.05, 1.15]) and, from v2, a heavier flywheel (train 0.12-0.20, test 0.22-0.26 kg m^2).
        Train engines are identical to v1's. Models trained on "train" engines are tested on "test".
        """
        if region not in ("train", "test"):
            raise ValueError("region must be 'train' or 'test'")
        rng = np.random.default_rng(np.random.SeedSequence([seed, 0x720]))
        u = rng.uniform
        mode_scale = u(0.90, 1.05) if region == "train" else u(1.0501, 1.15)
        base = cls()
        p = replace(
            base,
            bore_m=u(0.076, 0.084),
            stroke_m=u(0.076, 0.088),
            conrod_m=u(0.130, 0.150),
            compression_ratio=u(9.5, 11.0),
            soc_deg=u(-12.0, -5.0),
            wiebe_duration_deg=u(40.0, 60.0),
            ccv_cov=u(0.02, 0.04),
            inertia_kgm2=u(0.12, 0.20),
            friction_c0_nm=u(10.0, 14.0),
            load_nm=u(3.0, 8.0),
            mode_freqs_hz=tuple(float(f * mode_scale * u(0.97, 1.03)) for f in base.mode_freqs_hz),
            engine_seed=int(rng.integers(0, 2**31 - 1)),
        )
        if region == "test":
            p = replace(p, inertia_kgm2=u(0.22, 0.26))   # drawn last, so train engines match v1
        # LTFT spread across carOBD calibration files (sim/ecu_calibration.json); drawn after everything else
        p = replace(p, fuel_bias=float(rng.normal(0.0, EcuParams().ltft_engine_sd)))
        return replace(p, **overrides)


class _ModeBank:
    """A fixed set of damped modes, each Im(c * exp((i w - 1/tau) t)) for t >= 0, cut at 7 tau
    (0.09 % residual). `onset` multiplies the response by (1 - exp(-t / onset)), done as a second
    pole per mode with the opposite sign."""

    def __init__(self, freqs, taus, gains=None, onset: float = 0.0):
        s = np.array([2j * math.pi * f - 1.0 / tau for f, tau in zip(freqs, taus)])
        g = np.ones(len(s)) if gains is None else np.asarray(gains, dtype=float)
        if onset:
            s, g = np.concatenate([s, s - 1.0 / onset]), np.concatenate([g, -g])
        self.n_modes = len(freqs)
        self.s, self.gains = s, g
        self.length = int(7 * max(taus) * FS) + 1
        self.table = np.exp(np.outer(s, np.arange(self.length) / FS))     # poles x samples

    def coef(self, amp, phases=None, jitter=None):
        """Per-pole complex amplitudes for one event."""
        c = amp * np.ones(self.n_modes) * (1.0 if jitter is None else np.asarray(jitter))
        c = c * np.exp(1j * (0.0 if phases is None else np.asarray(phases)))
        return self.gains * np.tile(c, len(self.s) // self.n_modes)


class _Renderer:
    """Sum of modal events on a 48 kHz stream produced chunk by chunk. All events of one mode bank
    in a chunk are rendered with one matrix product and one scatter-add; tails carry over."""

    def __init__(self):
        self.events: list = []   # (t_event, bank, coef)
        self.tail = np.zeros(0)

    def add(self, t_e: float, bank: _ModeBank, coef) -> None:
        self.events.append((float(t_e), bank, coef))

    def render(self, n0: int, n_samples: int) -> np.ndarray:
        n_end = n0 + n_samples
        groups: dict = {}
        keep = []
        for ev in self.events:
            start = max(math.ceil(ev[0] * FS), n0)
            if start >= n_end:
                keep.append(ev)
            else:
                groups.setdefault(ev[1], []).append((start, ev[0], ev[2]))
        self.events = keep
        longest = max([b.length for b in groups], default=0)
        size = n_samples + max(longest, len(self.tail))
        acc = np.zeros(size)
        acc[: len(self.tail)] += self.tail
        for bank, evs in groups.items():
            starts = np.array([e[0] for e in evs])
            eps = starts / FS - np.array([e[1] for e in evs])          # sub-sample delay of the first sample
            coefs = np.array([e[2] for e in evs]) * np.exp(np.outer(eps, bank.s))
            y = (coefs @ bank.table).imag                               # events x samples
            pos = (starts - n0)[:, None] + np.arange(bank.length)[None, :]
            acc += np.bincount(pos.ravel(), weights=y.ravel(), minlength=size)[:size]
        self.tail = acc[n_samples:]
        return acc[:n_samples]


class EngineSim:
    """Steppable 4-cylinder engine simulator.

    >>> sim = EngineSim(EngineParams(), seed=0)
    >>> sim.inject("misfire", cylinder=3, severity=1.0)
    >>> chunk = sim.step(0.5)

    v2 keyword arguments (all optional; omitting them gives v1 behaviour plus the new channels):
      ecu       EcuParams for the ECU/OBD layer
      regimes   Regimes (tooth jitter, torsional flex, dropouts); default all off
      purpose   "general" | "demo" | "eval" | "calibration" | "training". Calibration and training
                sims refuse held-out faults (valve_tick) with HeldOutFaultError.
    """

    def __init__(self, params: EngineParams | None = None, seed: int = 0, *, ecu: EcuParams | None = None,
                 regimes: Regimes | None = None, purpose: str = "general"):
        self.params = p = params or EngineParams()
        self.ecu = ecu or EcuParams()
        self.regimes = regimes or Regimes()
        if purpose not in PURPOSES:
            raise ValueError(f"purpose must be one of {PURPOSES}")
        self.purpose = purpose
        self.seed = seed
        if sorted(p.firing_order) != [1, 2, 3, 4]:
            raise ValueError("the simulator supports 4 cylinders only")

        # Independent random streams, so adding a feature to one never shifts another.
        # The first three are v1's; v2 streams are spawned after them.
        ss = np.random.SeedSequence(seed)
        s_comb, s_mic, s_noise = ss.spawn(3)
        self._rng_comb = np.random.default_rng(s_comb)
        self._rng_mic = np.random.default_rng(s_mic)
        self._rng_noise = np.random.default_rng(s_noise)
        s_knock, s_valve, s_accel, s_ecu, s_regime, s_drop = ss.spawn(6)
        self._rng_knock = np.random.default_rng(s_knock)
        self._rng_valve = np.random.default_rng(s_valve)
        self._rng_accel = np.random.default_rng(s_accel)
        self._rng_ecu = np.random.default_rng(s_ecu)
        self._rng_regime = np.random.default_rng(s_regime)
        self._rng_drop = np.random.default_rng(s_drop)
        s_an_red, s_an_white = ss.spawn(2)             # 2.1: accel background noise, one stream per term
        (s_slow,) = ss.spawn(1)                         # 2.1.1: slow OBD sensors (spawned last: earlier streams unchanged)
        self._slow_key = [int(x) for x in s_slow.generate_state(2)]
        self._slow = self._slow_sensor_setup(np.random.default_rng(s_slow))
        self._cam_last = None
        self._rng_accel_red = np.random.default_rng(s_an_red)
        self._rng_accel_white = np.random.default_rng(s_an_white)

        self._build_geometry()
        self._build_engine_identity()
        self._build_audio_filters()

        # crank state
        self._seg = 0
        self._omega = p.rpm_target * 2 * math.pi / 60
        self._t = 0.0
        self.stalled = False

        # controller state
        self._map = p.map_init_pa
        self._i_term = p.map_init_pa
        self._rpm_target = p.rpm_target
        self._load_nm = p.load_nm

        # faults: committed state (one entry per fault type) + changes applied at the next cycle decision
        self._faults: dict[str, dict] = {}
        self._last_fault: str | None = None
        self._pending: list = []

        self._cycles = {}       # cycle index -> decision dict
        self._events = {}       # event index -> per-event state
        self._labels = []       # cycle labels with known t_start, not yet emitted

        # angle-domain buffer (nodes not yet consumed by the 48 kHz grid)
        self._buf = None
        self._mic = _Renderer()
        self._accel = _Renderer()
        self._n = 0             # samples emitted so far
        self._chunk_index = 0

        # ECU state
        self._stft = 0.0
        self._ltft = 1.0 / (1.0 + p.fuel_bias) - 1.0       # already learned: this engine's fuel bias is trimmed out
        self._dither = 0.0                                 # STFT limit cycle (switching O2 sensor), fraction
        self._retard = np.zeros(4)                    # knock retard per cylinder, deg
        self._phi_mix = 1.0
        self._lambda_meas = 1.0
        self._slugs: list = []                        # (t_arrival, phi) exhaust slugs on their way to the sensor
        self._coolant = self.ecu.coolant_start_c
        self._fast = [self._ecu_record(0.0, p.rpm_target)]
        self._obd_next = 0                            # index of the next OBD sample time

        # regimes
        self._tors_zi = np.zeros(2)
        self._load_dist = 0.0
        self._dropouts: list = []                     # (channel, t0, t1)
        self._blocks: list = []                       # generated BLOCK_S chunks not yet fully served (regimes on)
        self._served = 0                              # samples of _blocks[0] already returned

    # ------------------------------------------------------------------ public API

    def inject(self, fault: str, cylinder: int, severity: float) -> None:
        """Queue a fault. It takes effect from the next engine cycle that has not started yet.

        misfire        severity = probability per cycle that `cylinder` gets zero combustion
        knock          severity = probability per cycle of a knock burst on `cylinder` (before the
                       ECU's retard suppresses it); intensity grows with severity
        injector_clog  severity = fraction s of that cylinder's fuel lost (it runs lean)
        valve_tick     severity = excess valve lash, 0..1 (HELD OUT: never in calibration/training)
        One entry per fault type: injecting the same type again replaces it; different types combine.
        """
        if fault not in SUPPORTED_FAULTS:
            raise ValueError(f"unknown fault '{fault}'; supported: {SUPPORTED_FAULTS}")
        if fault in HELD_OUT_FAULTS and self.purpose in ("calibration", "training"):
            raise HeldOutFaultError(f"'{fault}' is held out and can't be injected into a {self.purpose} sim")
        if cylinder not in (1, 2, 3, 4):
            raise ValueError("cylinder must be 1..4")
        if not 0.0 <= severity <= 1.0:
            raise ValueError("severity must be in [0, 1]")
        self._pending.append(("set", {"fault": fault, "cylinder": int(cylinder), "severity": float(severity)}))

    def clear_faults(self) -> None:
        """Return to healthy from the next engine cycle."""
        self._pending.append(("clear", None))

    def set_operating_point(self, rpm_target: float | None = None, load_nm: float | None = None) -> None:
        """Change the speed-controller target and/or the accessory load (takes effect immediately)."""
        if rpm_target is not None:
            self._rpm_target = float(rpm_target)
        if load_nm is not None:
            self._load_nm = float(load_nm)

    @property
    def held_out(self) -> bool:
        return any(f in self._faults for f in HELD_OUT_FAULTS)

    def step(self, duration_s: float = 0.5, background: np.ndarray | None = None) -> dict:
        """Advance the simulation by `duration_s` and return one chunk on the 48 kHz grid.

        With hard regimes on, the regime noise (load torque, torsional flex, tooth jitter, dropouts) is
        drawn per generated chunk, so its realisation would depend on the step size. To keep one stream
        whatever the caller's step, the simulator then always generates BLOCK_S blocks (aligned to t = 0)
        and serves other steps as slices of them: step(0.5) is unchanged, step(0.05) returns the same
        samples. A fault or operating-point change requested mid-block takes effect from the next block
        (at most BLOCK_S later). With regimes off the stream is chunk-independent already and nothing changes.
        """
        n_block = int(round(BLOCK_S * FS))
        if not self.regimes.any_on or (not self._blocks and abs(duration_s - BLOCK_S) < 1e-12 and self._n % n_block == 0):
            return self._step(duration_s, background)
        if background is not None:
            raise ValueError("background texture needs step(BLOCK_S) when regimes are on")
        return self._sliced(int(round(duration_s * FS)))

    def _sliced(self, n_want: int) -> dict:
        """Serve the next n_want samples from whole BLOCK_S blocks."""
        while sum(len(b["t"]) for b in self._blocks) - self._served < n_want:
            self._blocks.append(self._step(BLOCK_S))
        ta = self._blocks[0]["t"][self._served]
        parts, need, off = [], n_want, self._served
        for b in self._blocks:
            n = len(b["t"])
            if off >= n:
                off -= n
                continue
            k = min(n - off, need)
            parts.append((b, off, off + k))
            need -= k
            off = 0
            if need == 0:
                break
        last, _, i1 = parts[-1]
        tb = last["t"][i1 - 1] + 1.0 / FS
        cat = lambda key: np.concatenate([b[key][..., i0:i1] for b, i0, i1 in parts], axis=-1)
        sel = lambda x: (x >= ta) & (x < tb)
        # events go with the block that produced them: crank regimes (torsional flex, jitter) can move a
        # tooth timestamp just outside its block, so a block's first / last slice takes those too
        def own(b, i0, i1, x):
            lo = -np.inf if i0 == 0 else b["t"][i0]
            hi = np.inf if i1 == len(b["t"]) else b["t"][i1 - 1] + 1.0 / FS
            return (x >= lo) & (x < hi)
        tt = np.concatenate([b["tooth_times"][own(b, i0, i1, b["tooth_times"])] for b, i0, i1 in parts])
        ti = np.concatenate([b["tooth_ids"][own(b, i0, i1, b["tooth_times"])] for b, i0, i1 in parts])
        cam = np.concatenate([b["cam_times"][own(b, i0, i1, b["cam_times"])] for b, i0, i1 in parts])
        fast = {}
        for key in parts[0][0]["ecu_fast"]:
            v = np.concatenate([b["ecu_fast"][key] for b, _, _ in parts])
            ft = np.concatenate([b["ecu_fast"]["t"] for b, _, _ in parts])
            fast[key] = v[sel(ft)]
        blocks_used = {id(b): b for b, _, _ in parts}.values()
        labels = [lab for b in blocks_used for lab in b["labels"] if not lab.get("_served")]
        out_labels = []
        for lab in labels:
            if lab["t_start"] < tb:
                lab["_served"] = True
                out_labels.append({k: v for k, v in lab.items() if k != "_served"})
        chunk = {
            "t": cat("t"), "crank_angle_deg": cat("crank_angle_deg"), "rpm": cat("rpm"),
            "tooth_times": tt, "tooth_ids": ti, "cam_times": cam,
            "mic": cat("mic"), "accel": cat("accel"), "cylinder_pressure": cat("cylinder_pressure"),
            "ecu_fast": fast,
            "obd": [o for b in blocks_used for o in b["obd"] if ta <= o["t"] < tb],
            "dropouts": [{"channel": d["channel"], "t0": max(d["t0"], ta), "t1": min(d["t1"], tb)}
                         for b in blocks_used for d in b["dropouts"] if d["t0"] < tb and d["t1"] > ta],
            "labels": out_labels,
            "meta": last["meta"],
        }
        self._served += n_want
        while self._blocks and self._served >= len(self._blocks[0]["t"]):
            self._served -= len(self._blocks[0]["t"])
            self._blocks.pop(0)
        return chunk

    def _step(self, duration_s: float = 0.5, background: np.ndarray | None = None) -> dict:
        """Generate the next `duration_s` (see step).

        background: optional real texture (e.g. an AudioSet P1 clip), length = number of samples
        in this chunk. It is high-passed and mixed into the mic as texture only; the engine's
        firing always comes from the simulator itself.
        """
        n0 = self._n
        n_samples = int(round(duration_s * FS))
        n1 = n0 + n_samples
        t = np.arange(n0, n1) / FS
        t_start, t_end = n0 / FS, n1 / FS

        # integrate whole firing segments until the buffer covers the chunk
        segs = [] if self._buf is None else [self._buf]
        while self._t <= t_end:
            segs.append(self._integrate_segment(include_first=not segs))
        buf = {k: np.concatenate([s[k] for s in segs], axis=-1) for k in segs[0]}

        tn, th, om = buf["t"], buf["theta"], buf["omega"]
        theta = np.interp(t, tn, th)
        rpm = np.interp(t, tn, om) * 60 / (2 * math.pi)
        pressure = np.vstack([np.interp(t, tn, buf["p"][i]) for i in range(4)])

        # keep nodes from the last one at or before t_end
        k = max(int(np.searchsorted(tn, t_end, side="right")) - 1, 0)
        self._buf = {key: v[..., k:] for key, v in buf.items()}

        theta_a = float(np.interp(t_start, tn, th))
        theta_b = float(np.interp(t_end, tn, th))
        tooth_times, tooth_ids = self._teeth_between(theta_a, theta_b, tn, th)
        cam_times = self._cam_pulses(t, theta)
        tooth_times = self._apply_crank_regimes(tooth_times, t, rpm)

        mic = self._render_mic(n0, n_samples, background)
        accel = self._render_accel(n0, n_samples)
        obd = self._obd_samples(t_end)

        drops = self._dropouts_for_chunk(t_start, t_end)
        if drops:
            mic, accel = mic.copy(), accel.copy()
            for ch, a, b in drops:
                if ch in ("mic", "accel"):
                    arr = mic if ch == "mic" else accel
                    arr[(t >= a) & (t < b)] = np.nan
                elif ch == "crank":
                    keep = ~((tooth_times >= a) & (tooth_times < b))
                    tooth_times, tooth_ids = tooth_times[keep], tooth_ids[keep]
                elif ch == "obd":
                    obd = [o for o in obd if not a <= o["t"] < b]

        labels = [lab for lab in self._labels if lab["t_start"] < t_end]
        self._labels = [lab for lab in self._labels if lab["t_start"] >= t_end]
        fast = [r for r in self._fast if t_start <= r["t"] < t_end]

        self._n = n1
        self._chunk_index += 1
        return {
            "t": t,
            "crank_angle_deg": np.mod(theta, 720.0),
            "rpm": rpm,
            "tooth_times": tooth_times,
            "tooth_ids": tooth_ids,
            "cam_times": cam_times,
            "mic": mic,
            "accel": accel,
            "cylinder_pressure": pressure,
            "ecu_fast": {**{key: np.array([r[key] for r in fast]) for key in ("t",) + OBD_CHANNELS},
                         "retard_deg": np.array([r["retard_deg"] for r in fast]).reshape(-1, 4)},
            "obd": obd,
            "dropouts": [{"channel": ch, "t0": a, "t1": b} for ch, a, b in drops],
            "labels": labels,
            "meta": self._meta(),
        }

    # ------------------------------------------------------------------ setup

    def _build_geometry(self):
        p = self.params
        self._r = p.stroke_m / 2
        self._l = p.conrod_m
        self._area = math.pi * p.bore_m**2 / 4
        vd = self._area * p.stroke_m
        self._vc = vd / (p.compression_ratio - 1)

        nseg = int(round(180 / p.dtheta_deg))
        self._nseg = nseg
        self._dth = 180 / nseg                                    # deg
        delta = np.arange(nseg + 1) * self._dth
        self._dxA = self._dxdalpha(np.radians(delta))             # TDC -> BDC strokes (>= 0)
        self._dxB = self._dxdalpha(np.radians(180 + delta))       # BDC -> TDC strokes (<= 0)
        # reciprocating inertia: each cylinder adds -m x''(a) x'(a) omega^2 (exact slider-crank kinematics);
        # two cylinders sit at phase delta and two at 180 + delta in every firing segment
        h = 1e-5
        d2 = lambda a: (self._dxdalpha(a + h) - self._dxdalpha(a - h)) / (2 * h)   # noqa: E731
        self._inertia_shape = -p.recip_mass_kg * 2.0 * (d2(np.radians(delta)) * self._dxA
                                                       + d2(np.radians(180 + delta)) * self._dxB)

        # closed part of the cycle: local phase 180 (IVC) .. 540 (EVO), firing TDC at 360
        self._phi = 180 + np.arange(2 * nseg + 1) * self._dth
        self._v = self._volume(self._phi)
        self._pm = (self._v[0] / self._v) ** p.kappa
        self._bases = {}
        self._pc, self._pc_max, self._hrr_peak_deg = self._basis(p.soc_deg)

    def _basis(self, soc_deg: float):
        """Pressure-per-joule curve for a start of combustion `soc_deg` (cached)."""
        hit = self._bases.get(soc_deg)
        if hit is not None:
            return hit
        p = self.params
        phi, v = self._phi, self._v
        z = np.clip((phi - (360 + soc_deg)) / p.wiebe_duration_deg, 0, None)
        dxb = p.wiebe_a * (p.wiebe_m + 1) / p.wiebe_duration_deg * z**p.wiebe_m * np.exp(-p.wiebe_a * z ** (p.wiebe_m + 1))
        integrand = (p.kappa - 1) * v ** (p.kappa - 1) * dxb
        pc = v ** (-p.kappa) * cumulative_trapezoid(integrand, phi, initial=0)   # Pa per J
        hrr_peak = float(phi[np.argmax(dxb)] - 360)                             # deg after TDC
        if not 0 <= hrr_peak < 180:
            raise ValueError("peak heat release must fall within 0..180 deg after TDC")
        self._bases[soc_deg] = (pc, float(pc.max()), hrr_peak)
        return self._bases[soc_deg]

    def _volume(self, phi_deg):
        return self._vc + self._area * self._piston_x(np.radians(phi_deg))

    def _piston_x(self, a):
        r, l = self._r, self._l
        return r * (1 - np.cos(a)) + l - np.sqrt(l**2 - (r * np.sin(a)) ** 2)

    def _dxdalpha(self, a):
        r, l = self._r, self._l
        return r * np.sin(a) * (1 + r * np.cos(a) / np.sqrt(l**2 - (r * np.sin(a)) ** 2))

    def _build_engine_identity(self):
        p = self.params
        s_wheel, s_cyl = np.random.SeedSequence([p.engine_seed, 0xE6]).spawn(2)
        pitch = 360 / p.wheel_teeth
        n_present = p.wheel_teeth - p.wheel_missing
        err = np.random.default_rng(s_wheel).normal(0, p.tooth_error_std_deg, n_present)
        err -= err.mean()
        self._tooth_angles = p.wheel_first_tooth_deg + pitch * np.arange(n_present) + err
        self._tooth_ids = np.arange(n_present)
        imb = np.random.default_rng(s_cyl).normal(0, p.cyl_imbalance_std, 4)
        self._cyl_factor = 1 + imb - imb.mean()                   # indexed by cylinder - 1

    def _build_audio_filters(self):
        p = self.params
        self._noise_zi = np.zeros(1)
        self._accel_noise_zi = np.zeros(1)
        self._accel_ms_zi = None
        self._bg_sos = sps.butter(4, p.bg_highpass_hz, btype="highpass", fs=FS, output="sos")
        self._bg_zi = np.zeros((self._bg_sos.shape[0], 2))
        self._knock_freqs = tuple(rho * p.knock_sound_speed_mps / (math.pi * p.bore_m) for rho in p.knock_mode_rho)
        self._bank_mic = _ModeBank(p.mode_freqs_hz, p.mode_taus_s, p.mode_gains, onset=2e-4)
        self._bank_blowdown = _ModeBank((p.blowdown_freq_hz,), (p.blowdown_tau_s,))
        self._bank_intake = _ModeBank((p.intake_freq_hz,), (p.intake_tau_s,))
        self._bank_accel = _ModeBank(p.accel_mode_freqs_hz, p.accel_mode_taus_s, p.accel_mode_gains, onset=2e-4)
        self._bank_knock = _ModeBank(self._knock_freqs, p.knock_mode_taus_s, p.knock_mode_gains)
        self._bank_valve = _ModeBank(p.valve_modes_hz, (p.valve_tau_s,) * len(p.valve_modes_hz))

    # ------------------------------------------------------------------ cycle / event bookkeeping

    def _apply_pending(self):
        for op, f in self._pending:
            if op == "clear":
                self._faults = {}
                self._last_fault = None
            else:
                self._faults[f["fault"]] = f
                self._last_fault = f["fault"]
        self._pending = []

    def _decide_cycle(self, c):
        self._apply_pending()
        F = self._faults
        # always draw the same randoms, so severity 0 is bit-identical to healthy
        u = self._rng_comb.random(4)
        n = self._rng_comb.standard_normal(4)
        u_knock = self._rng_knock.random(4)
        knock_int = self._rng_knock.lognormal(0.0, 0.4, 4)
        knock_jit = self._rng_knock.normal(0.0, 1.0, 4)

        misfired, knocked, clog, lash = [False] * 4, [False] * 4, [0.0] * 4, [0.0] * 4
        if "misfire" in F:
            i = F["misfire"]["cylinder"] - 1
            misfired[i] = bool(u[i] < F["misfire"]["severity"])
        if "knock" in F:
            i, sev = F["knock"]["cylinder"] - 1, F["knock"]["severity"]
            p_knock = sev * math.exp(-self._retard[i] / self.ecu.knock_soften_deg)
            knocked[i] = bool(u_knock[i] < p_knock) and not misfired[i]
        if "injector_clog" in F:
            clog[F["injector_clog"]["cylinder"] - 1] = F["injector_clog"]["severity"]
        if "valve_tick" in F:
            lash[F["valve_tick"]["cylinder"] - 1] = F["valve_tick"]["severity"]

        lead = F.get(self._last_fault) if self._last_fault else None
        self._cycles[c] = {
            "cycle": c,
            "fault": lead["fault"] if lead else "healthy",
            "cylinder": lead["cylinder"] if lead else 0,
            "severity": lead["severity"] if lead else 0.0,
            "faults": [dict(f) for f in F.values()],
            "misfired": misfired,
            "knocked": knocked,
            "knock_intensity": [float(knock_int[i]) if knocked[i] else 0.0 for i in range(4)],
            "clog": clog,
            "valve_lash": lash,
            "held_out": any(f in F for f in HELD_OUT_FAULTS),
            "q_scale": [float(x) for x in np.clip(self._cyl_factor + self.params.ccv_cov * n, 0, None)],
            "_n": n,
            "_knock_jit": knock_jit,
        }

    def _spark_advance(self, rpm: float) -> float:
        """Commanded spark advance (deg BTDC) with no knock retard: start of combustion at the engine's
        timing plus the ignition delay, which takes more crank degrees at higher rpm."""
        return min(-self.params.soc_deg + self.ecu.ignition_delay_s * rpm * 6.0, self.ecu.adv_max_deg)

    def _event(self, e):
        ev = self._events.get(e)
        if ev is None:
            c = e // 4
            if c not in self._cycles:
                self._decide_cycle(c)
            cyc = self._cycles[c]
            cyl = self.params.firing_order[e % 4]
            i = cyl - 1
            p = self.params
            # IVC happens at the start of this event's compression: trapped charge follows current MAP
            p_ivc = self._map
            # equivalence ratio: bank trim x this cylinder's injector
            phi = (1 + self._stft + self._dither) * (1 + self._ltft) * (1 + p.fuel_bias) * (1 - cyc["clog"][i])
            lean = max(0.0, 1.0 - phi)
            q_scale = max(0.0, self._cyl_factor[i] + p.ccv_cov * cyc["_n"][i] * (1 + self.ecu.lean_cov_gain * lean))
            # combustion starts at the engine's timing, later by this cylinder's knock retard
            # (exactly v1's timing with no retard; a 0.25 deg grid keeps the basis cache small)
            r = self._retard[i]
            soc = p.soc_deg + (round(r * 4) / 4 if r else 0.0)
            pc, pc_max, hrr = self._basis(soc)
            misfired = cyc["misfired"][i]
            q = 0.0 if misfired else p.q_per_pa * p_ivc * q_scale * min(phi, 1.0)
            ev = {"cyl": cyl, "p_ivc": p_ivc, "Q": q, "misfired": misfired, "phi": phi, "pc": pc,
                  "pc_max": pc_max, "hrr": hrr, "knocked": cyc["knocked"][i],
                  "knock_int": cyc["knock_intensity"][i], "knock_jit": float(cyc["_knock_jit"][i])}
            self._events[e] = ev
        return ev

    # ------------------------------------------------------------------ crank-angle integration

    def _integrate_segment(self, include_first):
        p = self.params
        s = self._seg
        n = self._nseg
        ev_exp, ev_cmp = self._event(s), self._event(s + 1)
        fo = p.firing_order

        p_exp = ev_exp["p_ivc"] * self._pm[n:] + ev_exp["Q"] * ev_exp["pc"][n:]
        p_cmp = ev_cmp["p_ivc"] * self._pm[: n + 1] + ev_cmp["Q"] * ev_cmp["pc"][: n + 1]
        p_exh = np.full(n + 1, p.p_exhaust_pa)
        p_int = np.full(n + 1, self._map)
        pcc = p.p_crankcase_pa
        t_gas = self._area * ((p_exp - pcc) * self._dxA + (p_cmp - pcc) * self._dxB
                              + (p_exh - pcc) * self._dxB + (p_int - pcc) * self._dxA)
        t_inertia = self._inertia_shape * self._omega**2           # N m; zero mean over the segment
        t_net = t_gas + t_inertia - self._load_nm - self._load_disturbance() - (p.friction_c0_nm + p.friction_c1_nms * self._omega)

        dth_rad = math.radians(self._dth)
        J = p.inertia_kgm2
        energy = 0.5 * J * self._omega**2 + cumulative_trapezoid(t_net, dx=dth_rad, initial=0)
        e_min = 0.5 * J * (50 * 2 * math.pi / 60) ** 2           # floor at 50 rpm: flag a stall
        if energy.min() < e_min:
            self.stalled = True
            energy = np.maximum(energy, e_min)
        omega = np.sqrt(2 * energy / J)
        dt = dth_rad / (0.5 * (omega[1:] + omega[:-1]))
        tn = self._t + np.concatenate([[0.0], np.cumsum(dt)])
        theta = 180.0 * s + np.arange(n + 1) * self._dth

        pres = np.empty((4, n + 1))
        pres[fo[s % 4] - 1] = p_exp
        pres[fo[(s + 1) % 4] - 1] = p_cmp
        pres[fo[(s - 1) % 4] - 1] = p_exh
        pres[fo[(s + 2) % 4] - 1] = p_int

        if s % 4 == 0:
            c = s // 4
            lab = {k: v for k, v in self._cycles[c].items() if not k.startswith("_")}
            lab["t_start"] = float(tn[0])
            self._labels.append(lab)

        self._acoustic_events(s, ev_exp, ev_cmp, theta, tn, p_exp)

        # idle-speed PI controller on MAP, updated once per firing
        dt_seg = tn[-1] - tn[0]
        rpm_mean = 30.0 / dt_seg
        err = self._rpm_target - rpm_mean
        i_new = self._i_term + p.ki_pa_per_rpm_s * err * dt_seg
        cmd = p.kp_pa_per_rpm * err + i_new
        if p.map_min_pa <= cmd <= p.map_max_pa:
            self._i_term = i_new                                   # anti-windup: freeze when saturated
        cmd = min(max(cmd, p.map_min_pa), p.map_max_pa)
        self._map += (cmd - self._map) * (1 - math.exp(-dt_seg / p.map_tau_s))

        self._ecu_update(ev_exp, float(tn[-1]), dt_seg, rpm_mean)

        # advance
        self._omega = float(omega[-1])
        self._t = float(tn[-1])
        self._seg = s + 1
        for old in [k for k in self._events if k < s - 1]:
            del self._events[old]
        for old in [k for k in self._cycles if k < s // 4 - 1]:
            del self._cycles[old]

        sl = slice(0, None) if include_first else slice(1, None)
        return {"t": tn[sl], "theta": theta[sl], "omega": omega[sl], "p": pres[:, sl]}

    def _acoustic_events(self, s, ev_exp, ev_cmp, theta, tn, p_exp):
        p = self.params
        at = lambda ang: float(np.interp(ang, theta, tn))  # noqa: E731

        # microphone (v1 events, same order and randoms as v1)
        self._add_mic_event(tn[0], "intake", p.intake_gain * max(1e5 - self._map, 0) / 1e5)
        if ev_exp["Q"] > 0:
            rise_mpa = ev_exp["Q"] * ev_exp["pc_max"] / 1e6
            t_comb = at(theta[0] + ev_exp["hrr"])
            self._add_mic_event(t_comb, "combustion", rise_mpa)
        self._add_mic_event(tn[-1], "blowdown", p.blowdown_gain * max(p_exp[-1] - p.p_exhaust_pa, 0) / 1e5)

        # accelerometer: structure-borne combustion response (always drawn, so the stream stays aligned)
        m = len(p.accel_mode_freqs_hz)
        ph = self._rng_accel.uniform(0, 2 * math.pi, m)
        jit = 1 + p.mode_jitter * self._rng_accel.standard_normal(m)
        if ev_exp["Q"] > 0:
            amp = p.accel_gain * ev_exp["Q"] * ev_exp["pc_max"] / 1e6
            self._accel.add(at(theta[0] + ev_exp["hrr"]), self._bank_accel, self._bank_accel.coef(amp, ph, jit))

        # knock: chamber-mode burst after peak heat release; the ECU's knock sensor hears it and retards
        kph = self._rng_knock.uniform(0, 2 * math.pi, len(p.knock_mode_rho))
        if ev_exp["knocked"] and ev_exp["Q"] > 0:
            ang = theta[0] + ev_exp["hrr"] + p.knock_delay_deg + p.knock_jitter_deg * ev_exp["knock_jit"]
            sev = self._faults.get("knock", {}).get("severity", 0.0)
            amp = p.knock_gain * ev_exp["knock_int"] * (0.5 + sev)
            t_k = at(ang)
            bk = self._bank_knock
            self._accel.add(t_k, bk, bk.coef(amp, kph))
            self._mic.add(t_k, bk, bk.coef(amp * p.knock_mic_ratio * p.mic_gain / p.accel_gain, kph))
            i = ev_exp["cyl"] - 1
            self._retard[i] = min(self._retard[i] + self.ecu.knock_retard_step_deg, self.ecu.knock_retard_max_deg)

        # valve closing impact of the cylinder starting compression (IVC = TDC_{s+1} - 140 deg)
        vph = self._rng_valve.uniform(0, 2 * math.pi, len(p.valve_modes_hz))
        cyc = self._cycles.get((s + 1) // 4)
        lash = cyc["valve_lash"][ev_cmp["cyl"] - 1] if cyc else 0.0
        ang = theta[0] + 180.0 + p.valve_ivc_deg - p.valve_tick_advance_deg * lash
        amp = p.valve_gain * (1 + p.valve_tick_gain * lash)
        t_v = at(ang)
        bv = self._bank_valve
        self._accel.add(t_v, bv, bv.coef(amp, vph))
        self._mic.add(t_v, bv, bv.coef(amp * p.valve_mic_ratio * p.mic_gain / p.accel_gain, vph))

    def _teeth_between(self, theta_a, theta_b, tn, th):
        k0 = math.floor((theta_a - self._tooth_angles.max()) / 360)
        k1 = math.ceil((theta_b - self._tooth_angles.min()) / 360)
        ang = (360.0 * np.arange(k0, k1 + 1)[:, None] + self._tooth_angles[None, :]).ravel()
        ids = np.tile(self._tooth_ids, k1 - k0 + 1)
        keep = (ang >= theta_a) & (ang < theta_b)
        return np.interp(ang[keep], th, tn), ids[keep]

    # ------------------------------------------------------------------ ECU / OBD

    def _ecu_update(self, ev_exp, t_now, dt, rpm):
        e = self.ecu
        # exhaust slug of the event that just finished expanding; a misfire passes unburnt air (reads lean)
        phi_slug = 0.0 if ev_exp["misfired"] else ev_exp["phi"]
        self._slugs.append((t_now + e.lambda_transport_s, phi_slug))
        while self._slugs and self._slugs[0][0] <= t_now:
            _, ph = self._slugs.pop(0)
            self._phi_mix += (ph - self._phi_mix) * (1 - math.exp(-dt / e.lambda_mix_tau_s))
        noise = e.lambda_noise * self._rng_ecu.standard_normal()
        a = math.exp(-dt / e.stft_dither_tau_s)
        self._dither = a * self._dither + math.sqrt(1 - a * a) * e.stft_dither_sd * self._rng_ecu.standard_normal()
        self._lambda_meas = 1.0 / max(self._phi_mix, 0.05) + noise
        # STFT integrates the fuelling error 1 - 1/lambda (lean -> add fuel). It is linear in the
        # equivalence ratio, so the per-cylinder ripple averages out and the bank settles at mean phi = 1
        # (integrating lambda - 1 instead would over-fuel a rippling bank: mean(1/phi) > 1/mean(phi)).
        # LTFT slowly takes over the STFT mean.
        err = 1.0 - 1.0 / self._lambda_meas
        self._stft = float(np.clip(self._stft + err * dt / e.stft_tau_s, -e.stft_limit, e.stft_limit))
        moved = self._stft * dt / e.ltft_tau_s
        self._ltft = float(np.clip(self._ltft + moved, -e.ltft_limit, e.ltft_limit))
        self._stft -= moved
        # knock retard recovers slowly
        self._retard = np.maximum(self._retard - e.knock_recover_deg_s * dt, 0.0)
        # coolant towards a load-dependent thermostat target
        load_frac = self._map / e.baro_pa
        target = e.coolant_target_c + e.coolant_load_gain_c * load_frac
        self._coolant += (target - self._coolant) * (1 - math.exp(-dt / e.coolant_tau_s))
        self._fast.append(self._ecu_record(t_now, rpm))
        if len(self._fast) > 4000:
            self._fast = self._fast[-2000:]

    def _ecu_record(self, t, rpm):
        e = self.ecu
        x = max(self._map - e.throttle_map0_pa, 0.0) / (e.baro_pa - e.throttle_map0_pa)
        return {
            "t": t,
            "rpm": rpm,
            "load_pct": 100.0 * self._map / e.baro_pa * e.volumetric_eff,
            "throttle_pct": min(100.0, e.throttle_idle_pct + (100.0 - e.throttle_idle_pct) * x ** e.throttle_exp),
            "map_kpa": self._map / 1e3,
            "coolant_c": self._coolant,
            "stft_pct": 100.0 * (self._stft + self._dither),
            "ltft_pct": 100.0 * self._ltft,
            "spark_adv_deg": self._spark_advance(rpm) - float(self._retard.mean()),
            "lambda": self._lambda_meas,
            "retard_deg": self._retard.copy(),                # per cylinder (ECU knock control), sim-internal
        }

    def _obd_samples(self, t_end):
        """OBD-II rows every obd_period_s. As in carOBD, each PID is polled round-robin and refreshes
        only every 1 / pid_refresh_hz (staggered per PID), so a row holds each PID's last polled value,
        quantised to the OBD-II resolution."""
        out = []
        e = self.ecu
        period, hz, n = e.obd_period_s, e.pid_refresh_hz, len(OBD_CHANNELS)
        times = np.array([r["t"] for r in self._fast])
        while self._obd_next * period < t_end:
            ts = self._obd_next * period
            row = {"t": ts}
            for k, ch in enumerate(OBD_CHANNELS):
                off = k / (n * hz)
                tp = max(math.floor((ts - off) * hz + 1e-9) / hz + off, 0.0) if ts >= off else 0.0
                j = max(int(np.searchsorted(times, tp + 1e-12, side="right")) - 1, 0)
                q = OBD_RESOLUTION.get(ch)
                v = float(self._fast[j][ch])
                row[ch] = round(v / q) * q if q else v
            for k, ch in enumerate(SLOW_CHANNELS):
                off = (k + 0.5) / (len(SLOW_CHANNELS) * hz)
                idx = math.floor((ts - off) * hz + 1e-9) if ts >= off else 0
                row[ch] = self._slow_value(k, ch, max(idx / hz + off, 0.0) if ts >= off else 0.0, idx)
            out.append(row)
            self._obd_next += 1
        return out

    def _cam_pulses(self, t, theta):
        """Cam sensor: times where the crank angle crosses 720 k + CAM_OFFSET_DEG, linear between 48 kHz
        samples (the previous chunk's last sample included, so the pulses don't depend on chunk size)."""
        tt = np.concatenate([[self._cam_last[0]], t]) if self._cam_last else t
        th = np.concatenate([[self._cam_last[1]], theta]) if self._cam_last else theta
        self._cam_last = (float(t[-1]), float(theta[-1]))
        cyc = np.floor((th - CAM_OFFSET_DEG) / 720.0)
        i = np.flatnonzero(np.diff(cyc) > 0)
        target = 720.0 * cyc[i + 1] + CAM_OFFSET_DEG
        return tt[i] + (target - th[i]) / (th[i + 1] - th[i]) * (tt[i + 1] - tt[i])

    def _slow_sensor_setup(self, rng) -> dict:
        """Per engine run: a level per slow sensor (across-file spread) and a smooth wander (3 slow
        sinusoids with random periods 300-1800 s, total sd = wander_sd)."""
        out = {}
        for ch in SLOW_CHANNELS:
            c = SLOW_SENSORS[ch]
            out[ch] = {"level": c["level"] + c["level_sd"] * rng.standard_normal(),
                       "periods": rng.uniform(300.0, 1800.0, 3), "phases": rng.uniform(0, 2 * math.pi, 3),
                       "amp": c["wander_sd"] * math.sqrt(2.0 / 3.0)}
        return out

    def _slow_value(self, k: int, ch: str, tp: float, idx: int) -> float:
        """The value polled at time tp (refresh number idx): level + wander + noise, at the reporting step.
        The noise is keyed by (channel, refresh number), so it doesn't depend on chunk size."""
        c, s = SLOW_SENSORS[ch], self._slow[ch]
        wander = s["amp"] * float(np.sum(np.sin(2 * math.pi * tp / s["periods"] + s["phases"])))
        noise = c["noise_sd"] * float(np.random.default_rng(self._slow_key + [k, idx]).standard_normal())
        v = s["level"] + wander + noise
        return round(round(v / c["step"]) * c["step"], 3)

    # ------------------------------------------------------------------ regimes

    def _load_disturbance(self) -> float:
        """Accessory-torque disturbance (AR(1) per firing), N m; 0 unless the regime is on."""
        r = self.regimes
        if not r.load_noise_nm:
            return 0.0
        dt = 30.0 / max(self._omega * 60 / (2 * math.pi), 1.0)          # duration of one firing, s
        a = math.exp(-dt / r.load_noise_tau_s)
        self._load_dist = a * self._load_dist + math.sqrt(1 - a * a) * r.load_noise_nm * self._rng_regime.standard_normal()
        return self._load_dist

    def _apply_crank_regimes(self, tooth_times, t, rpm):
        r = self.regimes
        if r.torsional_deg_at_1000 and tooth_times.size:
            # crank torsional flex: a lightly damped resonance excited by broadband torque, seen at the wheel
            w0 = 2 * math.pi * r.torsional_freq_hz / FS
            rad = math.exp(-r.torsional_zeta * w0)
            a1, a2 = -2 * rad * math.cos(w0 * math.sqrt(1 - r.torsional_zeta**2)), rad * rad
            var = (1 + a2) / ((1 - a2) * ((1 + a2) ** 2 - a1 * a1))
            white = self._rng_regime.standard_normal(t.size)
            y, self._tors_zi = sps.lfilter([1.0], [1.0, a1, a2], white, zi=self._tors_zi)
            amp = r.torsional_deg_at_1000 * (float(rpm.mean()) / 1000.0) ** r.torsional_rpm_exp
            dtheta = amp * y / math.sqrt(var)                              # deg
            omega_deg = np.interp(tooth_times, t, rpm) * 6.0               # deg/s
            tooth_times = tooth_times - np.interp(tooth_times, t, dtheta) / omega_deg
        if r.tooth_jitter_s and tooth_times.size:
            tooth_times = tooth_times + r.tooth_jitter_s * self._rng_regime.standard_normal(tooth_times.size)
        return tooth_times

    def _dropouts_for_chunk(self, t_start, t_end):
        r = self.regimes
        if r.dropout_per_min > 0:
            lam = r.dropout_per_min * (t_end - t_start) / 60.0
            for ch in r.dropout_channels:
                for _ in range(self._rng_drop.poisson(lam)):
                    a = self._rng_drop.uniform(t_start, t_end)
                    self._dropouts.append((ch, a, a + self._rng_drop.uniform(*r.dropout_s)))
        self._dropouts = [d for d in self._dropouts if d[2] > t_start]
        return [(ch, max(a, t_start), min(b, t_end)) for ch, a, b in self._dropouts if a < t_end]

    # ------------------------------------------------------------------ microphone and accelerometer

    def _add_mic_event(self, t_e, kind, amp):
        if amp <= 0:
            return
        p = self.params
        if kind == "combustion":
            m = len(p.mode_freqs_hz)
            phases = self._rng_mic.uniform(0, 2 * math.pi, m)
            jit = 1 + p.mode_jitter * self._rng_mic.standard_normal(m)
            self._mic.add(t_e, self._bank_mic, self._bank_mic.coef(p.mic_gain * amp, phases, jit))
        elif kind == "blowdown":
            self._mic.add(t_e, self._bank_blowdown, self._bank_blowdown.coef(p.mic_gain * amp))
        else:
            self._mic.add(t_e, self._bank_intake, self._bank_intake.coef(p.mic_gain * amp))

    def _coloured_noise(self, rng, zi_attr, n):
        a = self.params.noise_pole
        white = rng.standard_normal(n)
        red, zi = sps.lfilter([math.sqrt(1 - a * a)], [1, -a], white, zi=getattr(self, zi_attr))
        setattr(self, zi_attr, zi)
        return 0.8 * red + 0.6 * rng.standard_normal(n)              # unit variance

    def _render_mic(self, n0, n_samples, background):
        p = self.params
        clean = self._mic.render(n0, n_samples)
        # coloured noise at a fixed SNR relative to this chunk's clean signal
        noise = self._coloured_noise(self._rng_noise, "_noise_zi", n_samples)
        rms = float(np.sqrt(np.mean(clean**2))) if n_samples else 0.0
        mic = clean + noise * rms * 10 ** (-p.snr_db / 20)
        if background is not None:
            bg = np.asarray(background, dtype=float)
            if bg.shape != (n_samples,):
                raise ValueError(f"background must have shape ({n_samples},), got {bg.shape}")
            hp, self._bg_zi = sps.sosfilt(self._bg_sos, bg, zi=self._bg_zi)
            mic = mic + p.bg_gain * hp
        return mic.astype(np.float32)

    def _render_accel(self, n0, n_samples):
        """Clean modal response + coloured noise at accel_snr_db against a 1 s running RMS of the clean
        signal (state kept across chunks), so the output doesn't depend on the chunk size (2.1; 2.0
        scaled by each chunk's own RMS, which at 50 ms chunks made the noise follow the firing envelope)."""
        clean = self._accel.render(n0, n_samples)
        a_red = self.params.noise_pole
        red, self._accel_noise_zi = sps.lfilter([math.sqrt(1 - a_red * a_red)], [1, -a_red],
                                                self._rng_accel_red.standard_normal(n_samples), zi=self._accel_noise_zi)
        noise = 0.8 * red + 0.6 * self._rng_accel_white.standard_normal(n_samples)      # unit variance
        if not n_samples:
            return clean.astype(np.float32)
        a = math.exp(-1.0 / (ACCEL_RMS_TAU_S * FS))
        sq = clean.astype(float) ** 2
        if self._accel_ms_zi is None:
            self._accel_ms_zi = np.array([a * float(np.mean(sq[: int(0.01 * FS)]))])
        ms, self._accel_ms_zi = sps.lfilter([1 - a], [1, -a], sq, zi=self._accel_ms_zi)
        return (clean + noise * np.sqrt(ms) * 10 ** (-self.params.accel_snr_db / 20)).astype(np.float32)

    # ------------------------------------------------------------------ metadata

    def _meta(self):
        p = self.params
        lead = self._faults.get(self._last_fault) if self._last_fault else None
        return {
            "sim_version": SIM_VERSION,
            "seed": self.seed,
            "params": asdict(p),
            "ecu": asdict(self.ecu),
            "regimes": asdict(self.regimes),
            "purpose": self.purpose,
            "fs_hz": FS,
            "chunk_index": self._chunk_index,
            "firing_order": list(p.firing_order),
            "tdc_deg": {cyl: 180 * i for i, cyl in enumerate(p.firing_order)},
            "wheel": {"teeth": p.wheel_teeth, "missing": p.wheel_missing,
                      "first_tooth_deg": p.wheel_first_tooth_deg, "pitch_deg": 360 / p.wheel_teeth},
            "fault": dict(lead) if lead else None,
            "faults": [dict(f) for f in self._faults.values()],
            "held_out": self.held_out,
            "rpm_target": self._rpm_target,
            "load_nm": self._load_nm,
            "map_pa": self._map,
            "knock_retard_deg": [float(x) for x in self._retard],
            "knock_freqs_hz": [round(f, 1) for f in self._knock_freqs],
            "stalled": self.stalled,
            "source": "simulated",
            "sources": {"ecu_fast": "sim_internal", "obd": "simulator",
                        "slow_sensors": "simulator; level, wander, noise and step from carOBD calibration files",
                        "cam": "simulator: one pulse per 720 deg at CAM_OFFSET_DEG",
                        "lambda": "simulator only: carOBD logs commanded equivalence ratio, not O2"},
            "units": {"t": "s", "crank_angle_deg": "deg", "rpm": "rev/min", "tooth_times": "s",
                      "mic": "arbitrary (float32)", "accel": "arbitrary (float32), block accelerometer = knock sensor",
                      "cylinder_pressure": "Pa", "map_pa": "Pa", "load_pct": "%", "throttle_pct": "%",
                      "map_kpa": "kPa", "coolant_c": "degC", "stft_pct": "%", "ltft_pct": "%",
                      "spark_adv_deg": "deg BTDC", "lambda": "1", "cam_times": "s",
                      "intake_air_temp_c": "degC", "battery_v": "V", "baro_kpa": "kPa"},
            "calibration": dict(CHANNEL_CALIBRATION),
        }
