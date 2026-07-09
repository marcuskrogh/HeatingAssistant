# Test Suite

Heating Assistant uses **pytest** with a three-tier layout:

| Tier | Directory | Marker | What it covers |
|------|-----------|--------|----------------|
| Unit | `tests/test_*.py` | `@pytest.mark.unit` (auto-applied) | Pure physics, estimation math, parsers |
| Integration | `tests/integration/` | `@pytest.mark.integration` | Package boundaries: `mpc_cycle`, `controller/factory`, `services/` |
| System | `tests/system/` | `@pytest.mark.system` | Full stack smoke: model → MPC → forecast payload |

## Quick start

```bash
pip install -r requirements-dev.txt
pip install "mbc @ git+https://github.com/marcuskrogh/mbc.git"
python -m pytest tests/ -v -m "not slow"
```

## Useful commands

```bash
# Fast default (excludes slow Nelder-Mead benchmarks)
python -m pytest tests/ -m "not slow"

# Even faster benchmarks (3 MPC reps instead of 15)
FAST_TESTS=1 python -m pytest tests/test_performance.py -m "not slow" -v -s

# Unit tier only (auto-tagged for tests/test_*.py)
python -m pytest tests/ -m unit

# Integration + system tiers only
python -m pytest tests/integration tests/system -v

# With coverage
python -m pytest tests/ -m "not slow" --cov=custom_components/heating_assistant --cov-report=term-missing

# Panel harnesses (also run in CI)
node tests/panel_watchdog.harness.mjs
```

## Parallel CI shards

Four balanced shards are defined in `tests/shards.json` (≈ by runtime weight):

| Shard | Focus |
|-------|--------|
| 1 | estimation / sysid (heavy) |
| 2 | controller / MPC / integration / system |
| 3 | heat sources, thermal, solar, weather |
| 4 | coordinator, services, dashboard, KPI, misc |

```bash
chmod +x scripts/test_shards.sh

# Run one shard (pass through extra pytest flags)
./scripts/test_shards.sh 1 -m "not slow"
./scripts/test_shards.sh 2 -m "not slow"
./scripts/test_shards.sh 3 -m "not slow"
./scripts/test_shards.sh 4 -m "not slow"

# GitHub Actions matrix example (shard-id: 1..4)
# ./scripts/test_shards.sh ${{ matrix.shard }} -m "not slow" --cov=...
```

## Shared helpers

Post-refactor coordinator stubs live in `tests/helpers/`:

- `coordinator_stubs.py` — `make_minimal_coordinator()`, `make_hass_stub()`, `wire_room_enablement()`
- `setup_patches.py` — `patch_setup_stores()` for `async_setup_entry` tests
- `estimation_fixtures.py` — `make_single_room()`, `generate_history()`, `make_kalman_ml_estimator()`, plus module-scoped pytest fixtures

## CI

GitHub Actions workflow `.github/workflows/tests.yml` runs on pull requests to `main`:

- **4 parallel fast shards** (`-m "not slow"`) via `scripts/test_shards.sh`
- **Slow tier** job (`-m slow`) for IPOPT estimation regressions and MPC benchmarks
- **Coverage combine** across shards with regression check (`scripts/check_coverage.py`)
- **Panel harnesses** run in parallel with `xargs -P 4`
- **Nightly** (`cron`) full suite including all markers

```bash
# Local parallel run (pytest-xdist)
python3 -m pytest tests/ -m "not slow" -n auto
```
