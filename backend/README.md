# backend/

**Owner:** Nishant

- **FastAPI**, with replay mode by default and a live mode on the same code path after the input stage
- **WebSocket** with 3 server→UI channels (`signal`, `state`, `obd`) plus `ack` from the UI; server-side downsampling. See `contract/`.
- **Deterministic rule layer:** two witnesses (physics + classifier), 2 consecutive windows, hysteresis, latch + operator ACK, NE 107 mapping. It is the single source of truth for alarm and ACK state.
- **Backend watchdog:** a stream stalled for 3 s sets that sensor to `FAILURE` under `health.sensors`
- **OPC UA** (`asyncua`): EngineAsset object, asset and sensor health, fault events with severity, inject/ACK methods; tested in UaExpert
