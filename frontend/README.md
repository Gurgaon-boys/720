# frontend/

**Owner:** Livana

React dashboard in ISA-101 style: grey by default, colour only where attention is needed (plan section 12).

**Livana: move `engine-fingerprint-ui` here** (`git mv engine-fingerprint-ui frontend/`).

Panels: header (NE 107 health + REAL / SIMULATED / INJECTED badge), 720° fingerprint (simulator/injected only), signals + OBD panel, per-cylinder bars, alarm banner with ACK, evidence panel, injection console (marked SIMULATED).

The UI watchdog shows **"diagnostics offline"** after 3 s with no message (checked against `sent_at`). The 3D view is optional and has a hard cut-off at hour 20.
