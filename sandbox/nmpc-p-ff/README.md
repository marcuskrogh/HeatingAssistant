# Sandbox harness: NMPC period solve times

Wraps production `HouseThermalSDE` + `implicit_euler_substeps`.
Candidate NLP lives only here. No production edits.

```bash
python3 sandbox/nmpc-p-ff/harness.py
```

Writes `sandbox/nmpc-p-ff/inspect/`.
