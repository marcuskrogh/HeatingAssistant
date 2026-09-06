# Implementation plan: Next Compute timers show computing

## Summary
- NEXT CONTROL and NEXT NMPC stay idle (countdown numbers, static ring) while
  the matching solver is running. Operators expect the SWD-430 overlay: a
  spinning track and a computing label on the busy ring.
- Room-view capture just after a coincident slot wrap (~14:45 / ~59:45)
  shows neither chrome. The overlay today waits on a 5 s Ingress poll of
  `nmpc_computing` / `control_computing`; P finishes in milliseconds, and
  NMPC can starve or outrun that poll. KPI expand also strips inner-card
  border, and the spin targets an SVG circle transform that may not move.

## Scope / Decisions / Constraints
**In**
- Overview and room NEXT CONTROL / NEXT NMPC rings.
- `countdown.tick` (1 Hz) decides overlay from live remaining time plus
  `mpc_performance` flags and result stamps — no extra poll cadence.
- Paint `countdown--computing` on the inner card and the `.kpi-expand` wrap.
- Spin a SVG `<g>` around the track, not CSS transform on the circle.
- Independent rings: NMPC overlay does not force CONTROL overlay.
- Tests, CalVer, changelog, App package sync.

**Out**
- Changing NMPC / P periods, NLP, accept/reject, or ticker order.
- Overlay on live KPI gauges (HEATING POWER, ENERGY PRICE, …).
- WebSocket push or a new HTTP endpoint for compute flags.

**Decisions**
- Class is **bug**: shipped overlay is missing; expected behaviour is known
  (SWD-430 / SWD-426).
- Primary signal remains the runtime flags. Fallback: after a period wrap,
  keep the overlay until `nmpc_result_ts` / `last_control_ran_ts` is in the
  current slot, capped so a skipped worker cannot stick forever.
- Stopped system (`system_enabled` false): no overlay.

**Constraints**
- Dual tree: edit `heatingassistant/`, then `scripts/sync-ha-app-package.sh`.
- Product copy must not include tracker keys.

## Classification
- Class: bug
- Confidence: high
- Why: overlay missing while solvers run; expected SWD-430 behaviour is known

## Workflow
- Template: fix-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - test.mode: dedicated
  - harden.mode: dedicated
  - review.mode: single
  - review.depth: focused
  - review.lasers: sequential
  - side_paths: none
  - sandbox: none
- Chain: architect → implement → test → restructure → review → ship
- Rationale: localized countdown overlay; tests lock wrap vs flags; test/harden are the floor

## Inputs
- Research: none
- Model: none
- Sandbox: docs/agents/SANDBOX-kpi-loading.md (promote look)
- Prior: SWD-430 overlay; SWD-426 flags; SWD-469 kpi-expand wrap

## Pass criteria
- With `nmpc_computing` true, NEXT NMPC has `countdown--computing` and the
  label suffix computing; NEXT CONTROL does not unless its own signal is true.
- With `control_computing` true, NEXT CONTROL shows the overlay independently.
- When remaining wraps to a full period and the slot result stamp is still
  from the previous slot, the matching ring shows the overlay on the next
  tick without a new Ingress poll.
- Overlay clears once the matching result stamp is in the current slot and
  the flag is false.
- Spin CSS targets `countdown__spin`; `.kpi-expand.countdown--computing`
  carries the visible card chrome.
- Focused tests pass; CalVer bump; changelog; App package in sync.

## Work packages
1. Wrap-aware overlay + visible spin on Overview and room rings (SWD-495)
2. Tests, CalVer, changelog, App sync (SWD-496)

## Open items
- None — P overlay may still be brief; that is accepted (milliseconds of work
  plus a short wrap cap).

## Tracker
- Provider: jira
- Task: SWD-494
- Sub-tasks: SWD-495, SWD-496
- Branch: cursor/next-compute-timers-computing-febc
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/662
- Classification: bug
- Workflow: fix-fast

## Next
`/ship SWD-494` — Closeout merge after CI green
