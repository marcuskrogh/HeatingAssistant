# Architecture: Background PE progress session

## Shape
- Lives: `heatingassistant/app/static/js/identification/pe-session.js` (panel
  UI session) plus `pe-progress.js` markup and `HaConnection.getPeJob`.
- Depends on: `HaConnection.getPeJob`, `cancelParameterEstimation`, overlay CSS
  in `identification.css`, nav chrome in `industrial-dashboard.js`, PE snaps
  publishing `eta` / `eta_tol` and `rel_red` / `ftol`.
- Seams: `attachPeSession({ overlayHost, chips, connection, getHass })` is the
  injectable panel session; pages call `mountPeRunningBanner(container, session)`
  and `connection.peSession.isRunning()` / `show()` / `waitUntilSettled()`.
- Will not add: new WebSocket types, a second job queue, a global window
  singleton, a HA toolbar chip, or a new CSS framework.

## Neighbourhood
- Opened modules: Identification overlay host (moved from `sysid-detail.js` to
  the panel shadow root), `industrial-dashboard.js` boot/teardown, Overview /
  Tuning / PE page renderers, `kalman_ml._record_pe_progress` snap fields.
- Overlay plots share one log-canvas painter: η vs η-tol and SciPy
  `(f^k−f^{k+1})/max(|f^k|,|f^{k+1}|,1)` vs `PE_LBFGS_FTOL`.
- Major refinement: overlay ownership moves to the panel so route changes do
  not unmount the dialog. Pages keep start/apply logic; they do not own the
  overlay DOM.
- Backend `start_estimate_parameters_ml` already refuses a second worker; UI
  start-guard is the operator-facing exclusive lock, not a new engine API.

## Tracker
- Task: SWD-573
- Branch: cursor/swd-573-pe-background-progress-6f70

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/694 (`843739e4`)
