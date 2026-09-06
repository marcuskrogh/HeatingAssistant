# Sandbox: PE live optimisation popup

Identification overlay while a background PE job runs. Not production source.

```bash
python3 sandbox/pe-progress/harness.py --tag 05
python3 sandbox/pe-progress/harness.py --serve-only
PYTHONPATH=. python3 sandbox/pe-progress/bench_proxy.py
PYTHONPATH=. python3 sandbox/pe-progress/bench_jacobian.py
```

Query: `?mode=start|mid|late|timeout` (frozen stills) or `/?live=1` (animated replay).

Proxy: η = sqrt(J / n_obs) = RMSE / σ_R. Tolerance η ≤ 2 (1 °C at R_var=0.25).
