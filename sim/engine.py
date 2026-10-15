"""Physics-informed engine simulator, v1 (plan section 6).

Scope of v1: crank + combustion dynamics, 60-2 trigger wheel, synthesised microphone, misfire.
Knock, injector clog, valve tick, the accelerometer and the ECU/OBD loops slot in later through
the same fault registry (`inject`) and the same per-segment integration loop.

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
with two basis curves precomputed once per engine: Pm the motored curve and Pc the pressure per
joule released. A misfire is Q = 0: the cylinder is motored and produces no combustion pulse.
Gas exchange strokes sit at exhaust back-pressure and manifold pressure (MAP) respectively,
which gives pumping losses for free.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace

import numpy as np
from scipy import signal as sps
from scipy.integrate import cumulative_trapezoid

SIM_VERSION = "1.0.0"
FS = 48_000

SUPPORTED_FAULTS = ("misfire",)
PLANNED_FAULTS = ("knock", "injector_clog", "valve_tick")

_UNCHANGED = object()


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
    soc_deg: float = -8.0               # start of combustion relative to firing TDC
    ccv_cov: float = 0.03               # cycle-to-cycle variation of heat release (coefficient of variation)
    cyl_imbalance_std: float = 0.015    # fixed per-cylinder heat-release offsets (engine property)

    # crank dynamics
    inertia_kgm2: float = 0.15
    friction_c0_nm: float = 12.0
    friction_c1_nms: float = 0.02       # friction torque = c0 + c1 * omega
    load_nm: float = 5.0                # accessory load

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

    @classmethod
    def randomised(cls, seed: int, region: str = "train", **overrides) -> "EngineParams":
        """Draw a random engine. `region="test"` draws from the held-out parameter region.

        The held-out region is the structural-mode frequency scale: train in [0.90, 1.05],
        test in (1.05, 1.15]. Models trained on "train" engines are tested on "test" engines.
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
        return replace(p, **overrides)


class EngineSim:
    """Steppable 4-cylinder engine simulator.

    >>> sim = EngineSim(EngineParams(), seed=0)
    >>> sim.inject("misfire", cylinder=3, severity=1.0)
    >>> chunk = sim.step(0.5)
    """

    def __init__(self, params: EngineParams | None = None, seed: int = 0):
        self.params = p = params or EngineParams()
        self.seed = seed
        if sorted(p.firing_order) != [1, 2, 3, 4]:
            raise ValueError("v1 supports 4 cylinders only")

        # Independent random streams, so adding a feature to one never shifts another.
        ss = np.random.SeedSequence(seed)
        s_comb, s_mic, s_noise = ss.spawn(3)
        self._rng_comb = np.random.default_rng(s_comb)
        self._rng_mic = np.random.default_rng(s_mic)
        self._rng_noise = np.random.default_rng(s_noise)

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

        # faults: committed state + a pending change applied at the next cycle decision
        self._fault = None
        self._pending_fault = _UNCHANGED

        self._cycles = {}       # cycle index -> decision dict
        self._events = {}       # event index -> per-event state
        self._labels = []       # cycle labels with known t_start, not yet emitted

        # angle-domain buffer (nodes not yet consumed by the 48 kHz grid)
        self._buf = None
        self._mic_events = []   # pending acoustic events (t, kind, amplitude, randoms)
        self._mic_tail = np.zeros(0)
        self._n = 0             # samples emitted so far
        self._chunk_index = 0

    # ------------------------------------------------------------------ public API

    def inject(self, fault: str, cylinder: int, severity: float) -> None:
        """Queue a fault. It takes effect from the next engine cycle that has not started yet.

        misfire: `severity` is the probability per cycle that `cylinder` gets zero combustion.
        v1 holds one fault at a time; a new inject replaces the previous one.
        """
        if fault in PLANNED_FAULTS:
            raise NotImplementedError(f"fault '{fault}' is planned but not in sim v1")
        if fault not in SUPPORTED_FAULTS:
            raise ValueError(f"unknown fault '{fault}'; supported: {SUPPORTED_FAULTS}")
        if cylinder not in (1, 2, 3, 4):
            raise ValueError("cylinder must be 1..4")
        if not 0.0 <= severity <= 1.0:
            raise ValueError("severity must be in [0, 1]")
        self._pending_fault = {"fault": fault, "cylinder": int(cylinder), "severity": float(severity)}

    def clear_faults(self) -> None:
        """Return to healthy from the next engine cycle."""
        self._pending_fault = None

    def set_operating_point(self, rpm_target: float | None = None, load_nm: float | None = None) -> None:
        """Change the speed-controller target and/or the accessory load (takes effect immediately)."""
        if rpm_target is not None:
            self._rpm_target = float(rpm_target)
        if load_nm is not None:
            self._load_nm = float(load_nm)

    def step(self, duration_s: float = 0.5, background: np.ndarray | None = None) -> dict:
        """Advance the simulation by `duration_s` and return one chunk on the 48 kHz grid.

        background: optional real texture (e.g. an AudioSet P1 clip), length = number of samples
        in this chunk. It is high-passed and mixed into the mic as texture only; the engine's
        firing always comes from the simulator itself.
        """
        p = self.params
        n0 = self._n
        n_samples = int(round(duration_s * FS))
        n1 = n0 + n_samples
        t = np.arange(n0, n1) / FS
        t_end = n1 / FS

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

        theta_a = float(np.interp(n0 / FS, tn, th))
        theta_b = float(np.interp(t_end, tn, th))
        tooth_times, tooth_ids = self._teeth_between(theta_a, theta_b, tn, th)

        mic = self._render_mic(n0, n_samples, background)

        labels = [lab for lab in self._labels if lab["t_start"] < t_end]
        self._labels = [lab for lab in self._labels if lab["t_start"] >= t_end]

        self._n = n1
        self._chunk_index += 1
        return {
            "t": t,
            "crank_angle_deg": np.mod(theta, 720.0),
            "rpm": rpm,
            "tooth_times": tooth_times,
            "tooth_ids": tooth_ids,
            "mic": mic,
            "cylinder_pressure": pressure,
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

        # closed part of the cycle: local phase 180 (IVC) .. 540 (EVO), firing TDC at 360
        phi = 180 + np.arange(2 * nseg + 1) * self._dth
        v = self._volume(phi)
        self._pm = (v[0] / v) ** p.kappa
        phi0 = 360 + p.soc_deg
        z = np.clip((phi - phi0) / p.wiebe_duration_deg, 0, None)
        dxb = p.wiebe_a * (p.wiebe_m + 1) / p.wiebe_duration_deg * z**p.wiebe_m * np.exp(-p.wiebe_a * z ** (p.wiebe_m + 1))
        integrand = (p.kappa - 1) * v ** (p.kappa - 1) * dxb
        self._pc = v ** (-p.kappa) * cumulative_trapezoid(integrand, phi, initial=0)   # Pa per J
        self._pc_max = float(self._pc.max())
        self._hrr_peak_deg = float(phi[np.argmax(dxb)] - 360)    # angle of peak heat release rate, ATDC
        if not 0 <= self._hrr_peak_deg < 180:
            raise ValueError("peak heat release must fall within 0..180 deg after TDC")

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
        self._bg_sos = sps.butter(4, p.bg_highpass_hz, btype="highpass", fs=FS, output="sos")
        self._bg_zi = np.zeros((self._bg_sos.shape[0], 2))
        self._kernel_len = int(5 * max(p.mode_taus_s + (p.blowdown_tau_s, p.intake_tau_s)) * FS)

    # ------------------------------------------------------------------ cycle / event bookkeeping

    def _decide_cycle(self, c):
        if self._pending_fault is not _UNCHANGED:
            self._fault = self._pending_fault
            self._pending_fault = _UNCHANGED
        f = self._fault
        # always draw the same randoms, so p = 0 is bit-identical to healthy
        u = self._rng_comb.random(4)
        n = self._rng_comb.standard_normal(4)
        misfired = [False] * 4
        if f is not None and f["fault"] == "misfire":
            i = f["cylinder"] - 1
            misfired[i] = bool(u[i] < f["severity"])
        q_scale = np.clip(self._cyl_factor + self.params.ccv_cov * n, 0, None)
        self._cycles[c] = {
            "cycle": c,
            "fault": f["fault"] if f else "healthy",
            "cylinder": f["cylinder"] if f else 0,
            "severity": f["severity"] if f else 0.0,
            "misfired": misfired,
            "q_scale": [float(x) for x in q_scale],
        }

    def _event(self, e):
        ev = self._events.get(e)
        if ev is None:
            c = e // 4
            if c not in self._cycles:
                self._decide_cycle(c)
            cyc = self._cycles[c]
            cyl = self.params.firing_order[e % 4]
            # IVC happens at the start of this event's compression: trapped charge follows current MAP
            p_ivc = self._map
            misfired = cyc["misfired"][cyl - 1]
            q = 0.0 if misfired else self.params.q_per_pa * p_ivc * cyc["q_scale"][cyl - 1]
            ev = {"cyl": cyl, "p_ivc": p_ivc, "Q": q, "misfired": misfired}
            self._events[e] = ev
        return ev

    # ------------------------------------------------------------------ crank-angle integration

    def _integrate_segment(self, include_first):
        p = self.params
        s = self._seg
        n = self._nseg
        ev_exp, ev_cmp = self._event(s), self._event(s + 1)
        fo = p.firing_order

        p_exp = ev_exp["p_ivc"] * self._pm[n:] + ev_exp["Q"] * self._pc[n:]
        p_cmp = ev_cmp["p_ivc"] * self._pm[: n + 1] + ev_cmp["Q"] * self._pc[: n + 1]
        p_exh = np.full(n + 1, p.p_exhaust_pa)
        p_int = np.full(n + 1, self._map)
        pcc = p.p_crankcase_pa
        t_gas = self._area * ((p_exp - pcc) * self._dxA + (p_cmp - pcc) * self._dxB
                              + (p_exh - pcc) * self._dxB + (p_int - pcc) * self._dxA)
        t_net = t_gas - self._load_nm - (p.friction_c0_nm + p.friction_c1_nms * self._omega)

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

        # acoustic events in this segment
        if s % 4 == 0:
            c = s // 4
            lab = dict(self._cycles[c])
            lab["t_start"] = float(tn[0])
            self._labels.append(lab)
        self._add_mic_event(tn[0], "intake", p.intake_gain * max(1e5 - self._map, 0) / 1e5)
        if ev_exp["Q"] > 0:
            t_comb = float(np.interp(theta[0] + self._hrr_peak_deg, theta, tn))
            rise_mpa = ev_exp["Q"] * self._pc_max / 1e6
            self._add_mic_event(t_comb, "combustion", rise_mpa)
        self._add_mic_event(tn[-1], "blowdown", p.blowdown_gain * max(p_exp[-1] - p.p_exhaust_pa, 0) / 1e5)

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

    def _teeth_between(self, theta_a, theta_b, tn, th):
        k0 = math.floor((theta_a - self._tooth_angles.max()) / 360)
        k1 = math.ceil((theta_b - self._tooth_angles.min()) / 360)
        ang = (360.0 * np.arange(k0, k1 + 1)[:, None] + self._tooth_angles[None, :]).ravel()
        ids = np.tile(self._tooth_ids, k1 - k0 + 1)
        keep = (ang >= theta_a) & (ang < theta_b)
        return np.interp(ang[keep], th, tn), ids[keep]

    # ------------------------------------------------------------------ microphone

    def _add_mic_event(self, t_e, kind, amp):
        if amp <= 0:
            return
        p = self.params
        if kind == "combustion":
            m = len(p.mode_freqs_hz)
            rnd = (self._rng_mic.uniform(0, 2 * math.pi, m),
                   1 + p.mode_jitter * self._rng_mic.standard_normal(m))
        else:
            rnd = None
        self._mic_events.append((float(t_e), kind, float(amp), rnd))

    def _render_mic(self, n0, n_samples, background):
        p = self.params
        total = n_samples + self._kernel_len
        acc = np.zeros(total)
        acc[: len(self._mic_tail)] += self._mic_tail
        n_end = n0 + n_samples
        keep = []
        for ev in self._mic_events:
            t_e, kind, amp, rnd = ev
            start = math.ceil(t_e * FS)
            if start >= n_end:
                keep.append(ev)
                continue
            idx = np.arange(max(start, n0), start + self._kernel_len)
            tr = idx / FS - t_e
            if kind == "combustion":
                phases, jit = rnd
                y = np.zeros_like(tr)
                for f, tau, g, ph, j in zip(p.mode_freqs_hz, p.mode_taus_s, p.mode_gains, phases, jit):
                    y += g * j * np.exp(-tr / tau) * np.sin(2 * math.pi * f * tr + ph)
                y *= 1 - np.exp(-tr / 2e-4)                       # sub-ms onset, no click
            elif kind == "blowdown":
                y = np.exp(-tr / p.blowdown_tau_s) * np.sin(2 * math.pi * p.blowdown_freq_hz * tr)
            else:
                y = np.exp(-tr / p.intake_tau_s) * np.sin(2 * math.pi * p.intake_freq_hz * tr)
            acc[idx - n0] += p.mic_gain * amp * y
        self._mic_events = keep
        clean = acc[:n_samples]
        self._mic_tail = acc[n_samples:]

        # coloured noise at a fixed SNR relative to this chunk's clean signal
        a = p.noise_pole
        white = self._rng_noise.standard_normal(n_samples)
        red, self._noise_zi = sps.lfilter([math.sqrt(1 - a * a)], [1, -a], white, zi=self._noise_zi)
        w2 = self._rng_noise.standard_normal(n_samples)
        noise = (0.8 * red + 0.6 * w2)                            # unit variance
        rms = float(np.sqrt(np.mean(clean**2))) if n_samples else 0.0
        mic = clean + noise * rms * 10 ** (-p.snr_db / 20)

        if background is not None:
            bg = np.asarray(background, dtype=float)
            if bg.shape != (n_samples,):
                raise ValueError(f"background must have shape ({n_samples},), got {bg.shape}")
            hp, self._bg_zi = sps.sosfilt(self._bg_sos, bg, zi=self._bg_zi)
            mic = mic + p.bg_gain * hp
        return mic.astype(np.float32)

    # ------------------------------------------------------------------ metadata

    def _meta(self):
        p = self.params
        return {
            "sim_version": SIM_VERSION,
            "seed": self.seed,
            "params": asdict(p),
            "fs_hz": FS,
            "chunk_index": self._chunk_index,
            "firing_order": list(p.firing_order),
            "tdc_deg": {cyl: 180 * i for i, cyl in enumerate(p.firing_order)},
            "wheel": {"teeth": p.wheel_teeth, "missing": p.wheel_missing,
                      "first_tooth_deg": p.wheel_first_tooth_deg, "pitch_deg": 360 / p.wheel_teeth},
            "fault": dict(self._fault) if self._fault else None,
            "rpm_target": self._rpm_target,
            "load_nm": self._load_nm,
            "map_pa": self._map,
            "stalled": self.stalled,
            "source": "simulated",
            "units": {"t": "s", "crank_angle_deg": "deg", "rpm": "rev/min", "tooth_times": "s",
                      "mic": "arbitrary (float32)", "cylinder_pressure": "Pa", "map_pa": "Pa"},
        }
