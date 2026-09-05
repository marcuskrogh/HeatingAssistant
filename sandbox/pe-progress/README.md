# Sandbox: PE live optimisation popup

Identification overlay while a background PE job runs. Not production source.

```bash
python3 sandbox/pe-progress/harness.py --tag 01
python3 sandbox/pe-progress/harness.py --serve-only
```

Query: `?mode=start|mid|late|timeout` (frozen stills) or `?live=1` (animated replay).
