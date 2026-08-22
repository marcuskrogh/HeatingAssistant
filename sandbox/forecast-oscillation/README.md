# Room-view Forecast oscillation vs smooth Planned Power

Isolation tree for SWD-432. Wraps production `ControlEngine` /
`roll_fast_air_path` / `step_hold`. Does not edit production.

```bash
python3 sandbox/forecast-oscillation/harness.py --tag 01
python3 sandbox/forecast-oscillation/harness.py --tag 03
```

Inspectables land in `sandbox/forecast-oscillation/inspect/`.
