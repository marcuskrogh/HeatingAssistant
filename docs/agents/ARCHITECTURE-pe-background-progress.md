# Architecture: Background PE progress session

## Shape
- Lives: `heatingassistant/app/static/js/identification/pe-session.js` (panel
  UI session) plus existing `pe-progress.js` markup and `HaConnection.getPeJob`.
- Depends on: `HaConnection.getPeJob`, `cancelParameterEstimation`, overlay CSS
  in `identification.css`, nav chrome in `industrial-dashboard.js`.
- Seams: `attachPeSession({ overlayHost, chips, connection, getHass })` is the
  injectable panel session; pages call `mountPeRunningBanner(container, session)`
  and `connection.peSession.isRunning()` / `show()` / `waitUntilSettled()`.
- Will not add: new WebSocket types, a second job queue, a global window
  singleton, or a new CSS framework.

## Neighbourhood
- Opened modules: Identification overlay host (moved from `sysid-detail.js` to
  the panel shadow root), `industrial-dashboard.js` boot/teardown, Overview /
  Tuning / PE page renderers.
- Major refinement: overlay ownership moves to the panel so route changes do
  not unmount the dialog. Pages keep start/apply logic; they do not own the
  overlay DOM.
- Backend `start_estimate_parameters_ml` already refuses a second worker; UI
  start-guard is the operator-facing exclusive lock, not a new engine API.

## Tracker
- Task: SWD-573
- Branch: cursor/swd-573-pe-background-progress-6f70

## Next
`/implement SWD-573` — Build to this shape
