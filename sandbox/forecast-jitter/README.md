# Forecast jitter vs integrator substeps

Isolation tree for SWD-417. Wraps production `ControlEngine` (NMPC +
implicit Euler). Does not edit production.

```bash
python3 sandbox/forecast-jitter/harness.py --tag 01
```

Inspectables land in `sandbox/forecast-jitter/inspect/`.
