# Test Suite

Heating Assistant uses **pytest** (Python) plus **Node harnesses** (panel JS)
with a three-tier layout:

| Tier | Marker | What it covers |
|------|--------|----------------|
| Unit | `unit` (auto-applied to top-level `tests/test_*.py`) | Pure physics, estimation math, parsers — no coordinator/HA wiring |
| Integration | `integration` | Multi-module tests using coordinator stubs or mocked HA. Applied via module-level `pytestmark` to top-level files that build coordinators, and per-test in `tests/integration/` |
| System | `system` | Full-stack smoke across package boundaries (`tests/system/`) |

Files whose tests construct a coordinator (via `tests/helpers/coordinator_stubs`
or `object.__new__(HeatingAssistantCoordinator)`) carry an explicit
`pytestmark = pytest.mark.integration`, so `-m unit` really does select only
pure-logic tests. If you add a coordinator-based test to a new top-level file,
add that marker.

The orthogonal `slow` marker tags multi-start estimation and benchmark tests
(~17 tests, minutes of wall time); everything else runs in seconds. The
`ondemand` marker tags heavy analysis grids (not CI); run with
`pytest -m ondemand`.

## Quick start

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests/ -m "not slow and not ondemand"      # fast tier, ~20 s
```

`mbc` installs from the SHA pinned in `requirements.txt`. Do not install a
floating `mbc@main` on top — CI deliberately tests the pinned version that
Home Assistant deploys.

## Useful commands

```bash
# Fast default (excludes slow estimation/benchmark tests and on-demand grids)
python3 -m pytest tests/ -m "not slow and not ondemand"

# Slow tier (multi-start estimation regressions and MPC benchmarks)
python3 -m pytest tests/ -m slow

# On-demand analysis grids (not CI; e.g. SWD-329 PE robustness factorial)
python3 -m pytest tests/ -m ondemand

# Faster benchmarks (3 MPC reps instead of 15)
FAST_TESTS=1 python3 -m pytest tests/test_performance.py -m slow -v -s

# Single tiers
python3 -m pytest tests/ -m unit
python3 -m pytest tests/ -m integration
python3 -m pytest tests/ -m system

# With coverage (fast + slow combined, same as CI)
python3 -m pytest tests/ -m "not slow and not ondemand" --cov=custom_components/heating_assistant --cov-report=
python3 -m pytest tests/ -m slow --cov=custom_components/heating_assistant --cov-append --cov-report=term-missing

# Panel harnesses (all of them, also run in CI)
for h in tests/panel_*.harness.mjs; do node "$h"; done
```

## Panel JS harnesses

The panel frontend (`custom_components/heating_assistant/www`) is tested by
self-contained Node scripts (`tests/panel_*.harness.mjs`) that stub the DOM/HA
surface and load the **real** source files. Notable:

- `panel_syntax.harness.mjs` — parse gate over every non-vendor `www` JS file,
  so a syntax error anywhere fails CI even if no behaviour harness loads the
  file.
- `panel_overview_page.harness.mjs`, `panel_ha_services.harness.mjs`,
  `panel_kpi_engine.harness.mjs` — behaviour coverage for the overview page,
  the service/WebSocket wrappers (including their `{}` vs `null` failure
  contracts), and the KPI math.

New harnesses dropped into `tests/` matching `panel_*.harness.mjs` are picked
up by CI automatically. Keep each under ~2 s.

## Parameter estimation solver

Parameter estimation uses SciPy L-BFGS-B via `ScipyNLPBackend` only (no
IPOPT / `cyipopt` path).

## Shared helpers

Coordinator stubs and estimation fixtures live in `tests/helpers/` — import
them instead of copy-pasting local stubs:

- `coordinator_stubs.py` — `make_minimal_coordinator()`, `make_hass_stub()`, `wire_room_enablement()`
- `setup_patches.py` — `patch_setup_stores()` for `async_setup_entry` tests
- `estimation_fixtures.py` — `make_single_room()`, `make_electric_heaters()`,
  `generate_history()`, `make_kalman_ml_estimator()`, plus module-scoped
  pytest fixtures

## CI

GitHub Actions workflow `.github/workflows/tests.yml` runs on **pull requests
to `main`** (plus manual `workflow_dispatch`):

| Job | Purpose |
|-----|---------|
| `pytest-fast` | `pytest tests/ -m "not slow and not ondemand"` with a coverage fragment (~20 s of test time) |
| `pytest-slow` (×2 shards) | Slow tier split across two runners: the heaviest file (`test_estimator_stability.py`) alone, and `-m slow --ignore=` for everything else — new slow tests are picked up automatically |
| `coverage` | Combines all fragments, reports, checks `scripts/coverage_baseline.json` and package floors |
| `panel-harness` | Runs every `tests/panel_*.harness.mjs` serially (~3 s; Node is preinstalled on runners) |

Stale runs are cancelled when a PR is force-pushed (`concurrency`), and every
job has a `timeout-minutes` so a hung estimation can't burn hours.

**Coverage baseline:** `scripts/check_coverage.py` enforces the floor recorded
in `scripts/coverage_baseline.json` (captured from a **combined** fast+slow
run) plus package-level floors in `scripts/coverage_package_floors.json`
(2 pp tolerance). To regenerate after a deliberate change:

```bash
python3 -m pytest tests/ -m "not slow and not ondemand" --cov=custom_components/heating_assistant --cov-report=
python3 -m pytest tests/ -m slow --cov=custom_components/heating_assistant --cov-append --cov-report=
coverage report -m | tee coverage_report.txt
python3 scripts/update_coverage_baseline.py coverage_report.txt
```

## Known gaps (deliberate)

- No true end-to-end test drives `async_setup_entry` → real coordinator
  refresh → entity values; the system tier smoke-tests the mpc_cycle
  orchestration with a real controller but a stubbed coordinator. Building the
  real-coordinator scaffold is tracked follow-up work.
- `lovelace_setup.py` is dormant and untested; add tests before reactivating.
- Legacy `@pytest.mark.asyncio` decorators and `sys.path.insert` boilerplate
  remain in many older files (harmless — `asyncio_mode = auto` covers the
  former); sweep opportunistically when touching a file.
