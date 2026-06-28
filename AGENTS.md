# AGENTS.md

## Cursor Cloud specific instructions

Heating Assistant is a **Home Assistant custom integration** (under
`custom_components/heating_assistant/`); it is not a standalone runnable app — it is
loaded by a full Home Assistant install. The math core (nonlinear CD models, CD-EKF
estimation, MPC) lives in the sibling `mbc` library, which the Cursor Cloud update script
installs editable from `/agent/repos/mbc` (so `mbc` resolves to that local checkout, not
the pinned GitHub archive in `requirements.txt`). `numpy`/`scipy`/`highspy`/`osqp`,
`pytest`, `pytest-asyncio` and `homeassistant` are also installed by the update script.

- **Tests** (from repo root; required so `custom_components.heating_assistant.*` imports):
  `python3 -m pytest tests/ -m "not slow"`. The `slow` mark covers multi-start Nelder-Mead
  parameter estimation. `tests/conftest.py` stubs `homeassistant`/`voluptuous`, so the
  suite does not need a real HA runtime. `pytest` is not on `PATH` — use `python3 -m pytest`.
- There is **no lint/type-check tooling** and no CI in this repo.
- **Standalone demos** (stub the HA layer themselves, use the real `mbc`), good for
  exercising the MPC core without HA: `python3 benchmarks/bench_controller.py` and
  `python3 benchmarks/bench_model.py`.
- `tests/test_performance.py` (run with `-s`) **rewrites `BENCHMARKS.md`** as a side
  effect; `git checkout -- BENCHMARKS.md` afterwards unless you intend to commit it.
- **Known pre-existing failures (not environment-related):** ~50 tests fail on `main`,
  concentrated in `tests/test_visualisation*.py`, `tests/test_weather_module.py` and
  `tests/test_window_data_quality.py`. These are code-vs-test drift (e.g. a coordinator
  test stub lacks `is_room_enabled`, and a weather fallback returns `None` instead of the
  expected default), not dependency problems. Do not "fix" these as part of env setup.
