"""Sim v2 parameter sets beside EngineParams: the ECU/OBD layer and the harder evaluation regimes.

carOBD calibration status: data/splits/ holds no carOBD split yet, so no file has been used.
Every value marked `# TODO carOBD-calib` is a documented engineering default, to be replaced
from the carOBD CALIBRATION files only (never the held-out files) once the split exists.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EcuParams:
    """Slow ECU layer. The loops run once per firing (sim-internal, fast); OBD samples them at ~1 Hz."""

    obd_period_s: float = 1.0                 # OBD-II reporting interval (carOBD logs at ~1 Hz)

    # spark advance (deg BTDC). The ECU fires the plug early enough that combustion starts at the
    # engine's own timing (EngineParams.soc_deg): spark advance = -soc_deg + ignition delay in crank
    # degrees, which grows with rpm. Knock retard moves combustion later from there.
    ignition_delay_s: float = 0.0012          # spark to start of combustion  # TODO carOBD-calib
    adv_max_deg: float = 40.0

    # knock control: retard per detected knock event, slow recovery, per cylinder
    knock_retard_step_deg: float = 3.0
    knock_retard_max_deg: float = 12.0
    knock_recover_deg_s: float = 0.5          # "recovers slowly"
    knock_soften_deg: float = 4.0             # knock probability x exp(-retard / this): retard suppresses knock

    # closed-loop fuel trim on the exhaust lambda sensor (bank of 4 cylinders)
    lambda_transport_s: float = 0.08          # exhaust valve -> sensor  # TODO carOBD-calib
    lambda_mix_tau_s: float = 0.02            # exhaust mixing ahead of the sensor; sets the per-cylinder ripple
    lambda_noise: float = 0.003               # sensor noise (lambda units)
    stft_tau_s: float = 1.0                   # STFT integrator time constant  # TODO carOBD-calib
    stft_limit: float = 0.25                  # +-25 %
    ltft_tau_s: float = 60.0                  # LTFT learns the STFT mean  # TODO carOBD-calib
    ltft_limit: float = 0.25
    lean_cov_gain: float = 10.0               # combustion CoV x (1 + gain * leanness): lean cylinders burn less steadily

    # coolant (thermostat-controlled first-order)
    coolant_start_c: float = 88.0             # TODO carOBD-calib
    coolant_target_c: float = 90.0            # TODO carOBD-calib
    coolant_load_gain_c: float = 3.0          # target + gain x load fraction  # TODO carOBD-calib
    coolant_tau_s: float = 120.0              # TODO carOBD-calib

    # throttle and calculated load from MAP
    baro_pa: float = 101_325.0
    volumetric_eff: float = 0.90              # calculated load = MAP / baro x VE  # TODO carOBD-calib
    throttle_idle_pct: float = 12.0           # TODO carOBD-calib
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
        """The evaluation regime (DECISIONS.md D61): jitter, torsional flex and dropouts all on."""
        base = dict(tooth_jitter_s=2.0e-6, torsional_deg_at_1000=0.02, load_noise_nm=1.0, dropout_per_min=0.3)
        base.update(overrides)
        return cls(**base)

    @property
    def any_on(self) -> bool:
        return bool(self.tooth_jitter_s or self.torsional_deg_at_1000 or self.dropout_per_min or self.load_noise_nm)


HELD_OUT_FAULTS = ("valve_tick",)
HELD_OUT_RPM = (3000.0, 3500.0)               # never used for calibration or training
