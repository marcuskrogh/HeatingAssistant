# Sandbox: PE live optimisation popup

Identification overlay while a background PE job runs. Not production source.

```bash
python3 sandbox/pe-progress/harness.py --tag 02
python3 sandbox/pe-progress/harness.py --serve-only
python3 sandbox/pe-progress/bench_window.py
```

Query: `?mode=start|mid|late|timeout` (frozen stills) or `?live=1` (animated replay).

Window-scale bench writes `inspect/03_window_runtime.{json,csv,png}`.
