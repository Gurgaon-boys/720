"""Sim v2 parameter sets beside EngineParams: the ECU/OBD layer and the harder evaluation regimes.

carOBD calibration (sim 2.1): ECU/OBD constants marked `# carOBD` were measured on the 90 carOBD
CALIBRATION files only (29.1 h; never the held-out files) by `python -m sim.calibrate_ecu`, which
writes sim/ecu_calibration.json. Values carOBD can't identify are marked `# not in carOBD` with why.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EcuParams:
    """Slow ECU layer. The loops run once per firing (sim-internal, fast); OBD samples them at ~1 Hz."""

    obd_period_s: float = 1.0                 # OBD row interval (the backend's obd channel is 1 Hz)
    pid_refresh_hz: float = 0.55              # each PID's own refresh, polled round-robin  # carOBD: median 0.45-0.55 Hz

    # spark advance (deg BTDC). The ECU fires the plug early enough that combustion starts at the
    # engine's own timing (EngineParams.soc_deg): spark advance = -soc_deg + ignition delay in crank
    # degrees, which grows with rpm. Knock retard moves combustion later from there.
    # carOBD: timing advance 5th-95th percentile -4..25 deg (sim: 14 deg at idle .. 28 deg at 2800 rpm).
    # Its idle median is 1 deg (ECU idle spark reserve); matching it would move combustion phasing and
    # every crank-angle threshold, so the sim keeps MBT-like idle timing (DECISIONS: OBD realism).
    ignition_delay_s: float = 0.0012          # spark to start of combustion  # not in carOBD (range checked)
    adv_max_deg: float = 40.0

    # knock control: retard per detected knock event, slow recovery, per cylinder
    knock_retard_step_deg: float = 3.0
    knock_retard_max_deg: float = 12.0
    knock_recover_deg_s: float = 0.5          # "recovers slowly"
    knock_soften_deg: float = 4.0             # knock probability x exp(-retard / this): retard suppresses knock

    # closed-loop fuel trim on the exhaust lambda sensor (bank of 4 cylinders)
    lambda_transport_s: float = 0.08          # exhaust valve -> sensor  # not in carOBD (no O2 PID)
    lambda_mix_tau_s: float = 0.02            # exhaust mixing ahead of the sensor; sets the per-cylinder ripple
    lambda_noise: float = 0.003               # sensor noise (lambda units)
    stft_tau_s: float = 1.0                   # STFT integrator time constant  # not in carOBD (0.5 Hz polls can't resolve it)
    stft_dither_sd: float = 0.021             # O2 limit-cycle dither on STFT (fraction)  # carOBD: idle STFT sd 1.76 % per 30 s
    stft_dither_tau_s: float = 0.5
    stft_limit: float = 0.25                  # +-25 %
    ltft_tau_s: float = 60.0                  # LTFT learns the STFT mean  # not in carOBD (drift is driving-dominated)
    ltft_engine_sd: float = 0.0156            # per-engine learned fuel bias  # carOBD: LTFT file means sd 1.56 %
    ltft_limit: float = 0.25
    lean_cov_gain: float = 10.0               # combustion CoV x (1 + gain * leanness): lean cylinders burn less steadily

    # coolant (thermostat-controlled first-order)
    coolant_start_c: float = 87.0             # warm start  # carOBD: warm median 87 degC
    coolant_target_c: float = 86.0            # 86 + 3 x idle MAP fraction ~ 87  # carOBD: warm median 87 degC
    coolant_load_gain_c: float = 3.0          # target + gain x load fraction  # not in carOBD (fan-controlled, flat)
    coolant_tau_s: float = 183.0              # carOBD: warm-up 11.3 degC/min, first-order tau 183 s

    # throttle and calculated load from MAP
    baro_pa: float = 101_325.0
    volumetric_eff: float = 0.985             # calculated load = MAP / baro x VE  # carOBD: idle load 28.2 % at MAP 29 kPa
    throttle_idle_pct: float = 16.5           # carOBD: idle throttle median 16.5 %
    throttle_map0_pa: float = 30_000.0
    throttle_exp: float = 1.3


@dataclass(frozen=True)
class Regimes:
    """Harder conditions for evaluation. Every field defaults to OFF (the v1 behaviour); use
    `Regimes.eval()` for the evaluation settings. Each draws from its own random stream."""

    tooth_jitter_s: float = 0.0               # timing jitter on every tooth edge (capture noise), std in s
    torsional_deg_at_1000: float = 0.0        # rms crank torsional flex seen at the wheel at 1000 rpm, deg
    torsional_rpm_exp: float = 1.5            # flex grows as (rpm / 1000) ** this
    torsional_freq_hz: float = 280.0          # first torsional mode of the crank train
    torsional_zeta: float = 0.04
    load_noise_nm: float = 0.0                # rms accessory-torque disturbance (alternator, steering, AC), N m
    load_noise_tau_s: float = 0.15            # its correlation time (AR(1))
    dropout_per_min: float = 0.0              # dropout starts per minute, per channel
    dropout_s: tuple = (0.5, 4.0)             # dropout length range, s
    dropout_channels: tuple = ("mic", "accel", "crank", "obd")

    @classmethod
    def eval(cls, **overrides) -> "Regimes":
        """The evaluation regime (DECISIONS.md D72): jitter, torsional flex and dropouts all on."""
        base = dict(tooth_jitter_s=2.0e-6, torsional_deg_at_1000=0.02, load_noise_nm=1.0, dropout_per_min=0.3)
        base.update(overrides)
        return cls(**base)

    @property
    def any_on(self) -> bool:
        return bool(self.tooth_jitter_s or self.torsional_deg_at_1000 or self.dropout_per_min or self.load_noise_nm)


HELD_OUT_FAULTS = ("valve_tick",)
HELD_OUT_RPM = (3000.0, 3500.0)               # never used for calibration or training


# Slow OBD sensors added in L3, from the carOBD CALIBRATION files (python -m sim.calibrate_sensors ->
# sim/sensor_calibration.json; 90 files, engine running). Per channel: level mean, level sd across files
# (one level drawn per engine run), slow wander sd (30 s means around the file level), fast noise sd
# (inside 30 s windows) and the reporting step. No physics coupling: statistics only.
SLOW_SENSORS = {
    "intake_air_temp_c": {"level": 45.5, "level_sd": 7.57, "wander_sd": 3.88, "noise_sd": 0.49, "step": 1.0},
    "battery_v": {"level": 13.77, "level_sd": 0.149, "wander_sd": 0.111, "noise_sd": 0.048, "step": 0.019},
    "baro_kpa": {"level": 100.84, "level_sd": 0.82, "wander_sd": 0.234, "noise_sd": 0.0, "step": 1.0},
}

