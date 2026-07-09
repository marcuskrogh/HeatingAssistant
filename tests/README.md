# Test Suite

Heating Assistant uses **pytest** with a three-tier layout:

| Tier | Directory | Marker | What it covers |
|------|-----------|--------|----------------|
| Unit | `tests/test_*.py` | `@pytest.mark.unit` (auto-applied) | Pure physics, estimation math, parsers |
| Integration | `tests/integration/` | `@pytest.mark.integration` | Package boundaries: `mpc_cycle`, `controller/factory`, `services/` |
| System | `tests/system/` | `@pytest.mark.system` | Full stack smoke: model → MPC → forecast payload |

**New in this pass (coverage + tier gaps):**

| Module | Tests |
|--------|-------|
| `coordinator/mpc_cycle.py` | `tests/integration/test_mpc_cycle.py` — disturbances, compute, finalize, history |
| `config_schema.py` | `tests/test_config_schema.py` |
| `estimation/{sensitivity,warmstart,model_build}.py` | `tests/test_estimation_internals.py` |
| `services/{context,diagnostics}.py` | `tests/test_services_diagnostics.py` |
| Full mpc_cycle orchestration | `tests/system/test_control_loop_smoke.py` |
| Identification service handlers | `tests/test_identification_services.py` |
| History store / records / restore | `tests/test_history_store.py`, `test_history_records.py`, `test_history_startup_restore.py` |
| Coordinator update orchestration | `tests/integration/test_coordinator_update_cycle.py` |

## Quick start

```bash
pip install -r requirements-dev.txt
pip install "mbc @ git+https://github.com/marcuskrogh/mbc.git"
python3 -m pytest tests/ -v -m "not slow"
```

## Useful commands

```bash
# Fast default (excludes slow IPOPT/MPC benchmarks)
python3 -m pytest tests/ -m "not slow"

# Slow tier (IPOPT estimation regressions and MPC benchmarks)
python3 -m pytest tests/ -m slow

# Even faster benchmarks (3 MPC reps instead of 15)
FAST_TESTS=1 python3 -m pytest tests/test_performance.py -m slow -v -s

# Unit tier only (auto-tagged for tests/test_*.py)
python3 -m pytest tests/ -m unit

# Integration + system tiers only
python3 -m pytest tests/integration tests/system -v

# With coverage (fast + slow, same as CI)
python3 -m pytest tests/ -m "not slow" --cov=custom_components/heating_assistant --cov-report=term-missing
python3 -m pytest tests/ -m slow --cov=custom_components/heating_assistant --cov-append --cov-report=term-missing

# Panel harnesses (also run in CI)
node tests/panel_watchdog.harness.mjs
```

## Shared helpers

Post-refactor coordinator stubs live in `tests/helpers/`:

- `coordinator_stubs.py` — `make_minimal_coordinator()`, `make_hass_stub()`, `wire_room_enablement()`
- `setup_patches.py` — `patch_setup_stores()` for `async_setup_entry` tests
- `estimation_fixtures.py` — `make_single_room()`, `generate_history()`, `make_kalman_ml_estimator()`, plus module-scoped pytest fixtures

## CI

GitHub Actions workflow `.github/workflows/tests.yml` runs on **pull requests to `main` only**:

| Job | Purpose |
|-----|---------|
| `pytest-fast` | `pytest tests/ -m "not slow"` with per-job coverage fragment |
| `pytest-slow` | `pytest tests/ -m slow` with per-job coverage fragment |
| `coverage` | Combines fast + slow fragments, reports, checks `scripts/coverage_baseline.json` |
| `panel-harness` | Serial Node smoke scripts (`tests/panel_*.harness.mjs`) |

**Fast/slow split:** slow tests (multi-start Nelder-Mead estimation, IPOPT/MPC benchmarks) dominate wall time. Running them in a separate parallel job keeps PR feedback fast without skipping coverage.

**Efficiency:** Python jobs use `actions/setup-python` with `cache: pip` (keyed off `requirements-dev.txt`). Node.js is installed only in `panel-harness`; Python jobs do not need it.

**Coverage baseline:** `scripts/check_coverage.py` enforces the floor recorded in `scripts/coverage_baseline.json`. Package-level floors for `services/`, `history/`, and `coordinator/` are defined in `scripts/coverage_package_floors.json` (2 pp tolerance). Maintainers can regenerate the baseline from a combined local report:

```bash
coverage report -m | tee coverage_report.txt
python3 scripts/update_coverage_baseline.py coverage_report.txt
```

Optional flags: `--tests-passed`, `--tests-skipped`, `--wall-time-seconds`, `--dry-run`.
