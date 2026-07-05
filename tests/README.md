# Test Suite

Heating Assistant uses **pytest** with a three-tier layout:

| Tier | Directory | Marker | What it covers |
|------|-----------|--------|----------------|
| Unit | `tests/test_*.py` | `@pytest.mark.unit` (optional) | Pure physics, estimation math, parsers |
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

# Integration + system tiers only
python -m pytest tests/integration tests/system -v

# With coverage
python -m pytest tests/ -m "not slow" --cov=custom_components/heating_assistant --cov-report=term-missing

# Panel harnesses (also run in CI)
node tests/panel_watchdog.harness.mjs
```

## Shared helpers

Post-refactor coordinator stubs live in `tests/helpers/`:

- `coordinator_stubs.py` — `make_minimal_coordinator()`, `make_hass_stub()`, `wire_room_enablement()`
- `setup_patches.py` — `patch_setup_stores()` for `async_setup_entry` tests

## CI

GitHub Actions workflow `.github/workflows/tests.yml` runs on push/PR to `main`:
- pytest on Python 3.11 and 3.12
- coverage report
- Node.js panel harness smoke tests
