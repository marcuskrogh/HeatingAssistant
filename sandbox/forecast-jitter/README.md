# Forecast jitter vs integrator substeps

Isolation tree for SWD-417. Wraps production `ControlEngine` (NMPC +
implicit Euler). Does not edit production.

```bash
python3 sandbox/forecast-jitter/harness.py --tag 01
python3 sandbox/forecast-jitter/harness.py --tag 02 --price peaked --smoothing 0.05,0.1,1.0,5.0
```

Inspectables land in `sandbox/forecast-jitter/inspect/`.
