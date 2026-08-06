# Bug: HA Core freezes on startup from cyipopt/Ipopt install

## Summary
- On HAOS, Heating Assistant setup tried to resolve/install `cyipopt` (Ipopt) when no compatible wheel existed, blocking Core startup and wedging the UI.
- Fix: **remove the Ipopt/`cyipopt` dependency entirely**. Non-linear MPC and parameter estimation use **SciPy only**.

## Repro
1. Install Heating Assistant on HAOS and restart Core.
2. `:8123` comes up briefly, then dies while “processing”; Observer stays healthy.
3. Last Core log before hang mentions no matching wheel for cyipopt.
4. Disabling this integration restores a stable UI; re-enabling alone reproduces the outage.

## Expected
- HA Core stays usable with the integration installed.
- Non-linear mode uses SciPy NLP; no pip/build of native Ipopt deps at startup.
- Integration sets up (or fails its own path cleanly) without hanging Core.

## Actual
- Startup path pip-installed / resolved cyipopt; missing wheels blocked the Core process.

## Impact
- HAOS hosts with this integration loaded could lose the Core UI until the integration was removed.

## Suspected area
- `controller/ipopt_deps.py` (runtime pip/vendor install)
- `controller/ipopt_probe.py` + coordinator/`__init__` startup probe
- `controller/facade.py` / `estimation/kalman_ml.py` IpoptNLPBackend usage
- Vendored `vendor/cyipopt_wheels/` + musllinux build workflow

## Acceptance criteria
- No `cyipopt` / Ipopt install, vendored wheels, or pip/subprocess install path in the integration.
- Non-linear MPC and system-ID NLP use `ScipyNLPBackend` only.
- Startup never blocks on native wheel resolution; no blocking pip/network/subprocess on the event loop for this path.
- With SciPy available (normal HA deps), integration works; non-linear mode gated only on the SciPy NLP probe.
- Regression: setup path does not reference `ensure_cyipopt_installed` / cyipopt wheels; HA remains usable without cyipopt.

## Out of scope
- Separate add-on/service for Ipopt
- Reintroducing optional cyipopt later
- UpdateEntityFeature cleanup (not present in this integration)

## Work packages
1. Remove Ipopt deps, vendor wheels, and install/probe plumbing; SciPy-only NLP.
2. Update tests and packaging docs for SciPy-only.

## Tracker
- Task: SWD-253
- Relates: SWD-247
- Branch: `cursor/swd-253-cyipopt-startup-hang-af84`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/543

## Shipped
- Removed Ipopt/cyipopt entirely; SciPy-only NLP for non-linear MPC and estimation
- Deleted vendored wheels + runtime pip install path that hung HAOS Core
- review-fix CLEAN; merged via PR #543

## Next
Done
