# Sandbox: PE live optimisation popup

Identification overlay while a background PE job runs.

```bash
python3 sandbox/pe-progress/harness.py --tag 04
python3 sandbox/pe-progress/harness.py --serve-only
python3 sandbox/pe-progress/bench_window.py
PYTHONPATH=. python3 sandbox/pe-progress/bench_jacobian.py
```

Query: `?mode=start|mid|late|timeout` (frozen stills) or `/?live=1` (animated replay).

Jacobian / convergence bench writes `inspect/04_jacobian.*` and `inspect/04_convergence.png`.
