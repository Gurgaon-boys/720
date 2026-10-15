# sim/

**Owner:** Dipen

Physics-informed engine simulator (plan section 6). It is the test bench and the healthy reference. It is not a "digital twin".

- 4-cylinder, 4-stroke, firing order 1-3-4-2, Wiebe combustion, crank dynamics on a 60-2 tooth wheel
- Realism: cycle-to-cycle variation, per-tooth spacing errors, idle-speed controller, randomised parameters with one held-out region, noise, RPM jitter and dropouts
- Streams: 48 kHz mic (synthesised firing plus P1 background texture only) and 48 kHz block accelerometer; ECU loops (fuel trim, knock retard, misfire counters)
- Faults (severity 0–1): misfire, knock, injector_clog; valve_tick is held out of ML training
- Steppable in ~0.5 s chunks with live commands (fault, cylinder, severity, sensor on/off)

OBD output ranges are calibrated against **carOBD calibration files only** (see `data/splits/`).
