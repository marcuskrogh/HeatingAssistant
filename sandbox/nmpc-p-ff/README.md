# Sandbox harness: NMPC period solve times

Wraps production `HouseThermalSDE` + `implicit_euler_substeps`.
Candidate NLP lives only here. No production edits.

```bash
python3 sandbox/nmpc-p-ff/harness.py
python3 sandbox/nmpc-p-ff/harness.py --only "2 h" --maxiter 80 --tag 03 --analytic --timeout 300
```

Writes `sandbox/nmpc-p-ff/inspect/`.
