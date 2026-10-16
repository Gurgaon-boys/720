# Pending decisions · branch `feature/livana-3d`

To be numbered and appended to `docs/DECISIONS.md` at merge time.

## 2026-10-16 · 3D engine explain view (frontend only, contract 1.5 unchanged)

| # | Decision | Why |
|---|---|---|
| D? | **The 3D engine view is a separate, lazy-loaded tab ("2D dashboard \| 3D engine", URL hash `#3d`)**, not a panel on the main screen. three.js, `@react-three/fiber` 8 and `@react-three/drei` 9 load only when the tab first opens (separate chunk, about 246 kB gzipped). The main bundle grows by about 3 kB | Keeps D8 (no 3D on the main screen): the 2D dashboard stays the operator view and pays nothing for the 3D view |
| D? | **The 3D view has its own error boundaries** (one around the lazy chunk, one around the WebGL canvas). It checks for WebGL before mounting and handles a lost context with a restart button. The 2D dashboard stays mounted while the 3D tab is open | A WebGL or chunk-load failure must never take the 2D dashboard down |
| D? | **It reads the same `state` stream as the dashboard** (mock transport when `VITE_API_URL` is empty, live WebSocket otherwise). It computes no diagnosis, adds no contract field and makes no backend change | D16: the rule layer is the single source of truth |
| D? | **Pistons follow `operating_point.rpm`**, firing order 1-3-4-2, slowed by an operator-chosen factor of 10×, 30× (default, the same as the 2D cylinder bank) or 120×, always labelled with the real cycle time. Motion freezes when offline (UI watchdog), when `rpm` is null and under prefers-reduced-motion | "Nothing moves unless the data says so" (frontend CLAUDE.md §9) |
| D? | **Per-cylinder glow comes from `anomaly.per_location` only when `localisation === "cylinder"`, and only above `threshold`.** On real clips (`"none"`) there is no glow and no cylinder numbers: the state badge applies to the whole engine and the block is marked illustrative | D12: no localisation on real clips. ISA-101: no glow on healthy |
| D? | **3D colours follow `decision.state`, not the fault:** grey NORMAL, amber ADVISORY, red ALARM, blue UNCLASSIFIED (`lib/faults.js` `STATE_3D`), always shown with the state name as text. This intentionally differs from the 2D panels (fault colour on ALARM, magenta UNCLASSIFIED). **Open: align the two palettes?** Blue UNCLASSIFIED is also close to the NE 107 MAINTENANCE_REQUIRED chip colour | Requested for the 3D view. The difference is recorded so it isn't mistaken for a bug |
| D? | **On ALARM with a cylinder, the camera eases to that cylinder** and labels it "Cyl N · <fault>". ADVISORY gets the same label without the camera move. `decision.guidance` is shown under the label **if a message carries it**. It is not in schema 1.5, so today it never appears; the mock doesn't invent it | ISA-18.2: only ALARM is an alarm. No new contract fields on this branch |
| D? | **Rendering pauses** (`frameloop="never"`) while the 3D tab isn't selected, the browser tab is hidden, or the view is scrolled off-screen. Resolution drops to DPR 1 if the frame rate declines. Measured on an Intel Iris Xe: 59 draw calls, about 4,500 triangles, about 0.6 ms per frame | 60 fps target on the demo laptop |
