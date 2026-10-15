# eval/

**Owner:** Diljit

Results JSON and charts (plan section 13). **Every number on a slide must trace to a file here, produced by code in this repo.**

- Tables **A** (simulated), **B** (injected: knock + valve tick only) and **C** (fully real), never mixed
- Random vs grouped split gap; models on identical splits across seeds
- Per-fault sensor ablation; held-out-class AUROC and partial AUROC; share sent to "unclassified"
- False positives per hour **at every level** with 95% upper bounds (held-out carOBD files are the headline)
- Noise and RPM sweeps, shortcut probe, injector-clog detectability curve, window-to-alarm latency

Every result states its n, data source and split. Detection rates carry Wilson 95% intervals.
