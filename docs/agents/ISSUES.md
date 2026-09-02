# Issues

Continuity mirror for Jira (`SWD`). Upsert rows on create / transition / handoff.

| Key | Type | Title | Status | Parent | Artifact | Next |
|-----|------|-------|--------|--------|----------|------|
| SWD-469 | Task | [Feature] Expandable KPI detail cards on Overview and room view | In Review | — | docs/agents/PLAN-kpi-expand-detail.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/652 |
| SWD-470 | Sub-task | Expand host + CSS for KPI detail cards | To Do | SWD-469 | docs/agents/PLAN-kpi-expand-detail.md | — |
| SWD-471 | Sub-task | KPI detail copy catalog + last NMPC duration | To Do | SWD-469 | docs/agents/PLAN-kpi-expand-detail.md | — |
| SWD-472 | Sub-task | Wire Overview and room-view KPI expansion | To Do | SWD-469 | docs/agents/PLAN-kpi-expand-detail.md | — |
| SWD-473 | Sub-task | Tests, CalVer, changelog, App sync for KPI expand | To Do | SWD-469 | docs/agents/PLAN-kpi-expand-detail.md | — |
| SWD-465 | Task | [Bug] P-controller must track original NMPC trajectory for the 2 h window | Done | — | docs/agents/PLAN-p-frozen-nmpc-tref.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/651 (`3719368`) |
| SWD-466 | Sub-task | Keep P T_ref frozen at the last accepted NMPC plan | Done | SWD-465 | docs/agents/PLAN-p-frozen-nmpc-tref.md | — |
| SWD-467 | Sub-task | Tests, CalVer, changelog, App sync for frozen P T_ref | Done | SWD-465 | docs/agents/PLAN-p-frozen-nmpc-tref.md | — |
| SWD-468 | Sub-task | Room view Forecast stays on original NMPC T_ref | Done | SWD-465 | docs/agents/PLAN-p-frozen-nmpc-tref.md | — |
| SWD-462 | Task | [Bug] Solar gain history is unattenuated clear-sky on cloudy days | Done | — | docs/agents/PLAN-solar-gain-cloud-cover.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/650 (`27ccf06`) |
| SWD-463 | Sub-task | Cloud-cover now + k=0 attenuation; no stray GHI | Done | SWD-462 | docs/agents/PLAN-solar-gain-cloud-cover.md | — |
| SWD-464 | Sub-task | Tests, CalVer, changelog, App sync for solar cloud cover | Done | SWD-462 | docs/agents/PLAN-solar-gain-cloud-cover.md | — |
| SWD-459 | Task | [Bug] PE historical heater power uses heating capacity for cooling | Done | — | docs/agents/PLAN-pe-asymmetric-cooling.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/649 (`3a30be0`) |
| SWD-460 | Sub-task | Map PE heater power through asymmetric heating/cooling | Done | SWD-459 | docs/agents/PLAN-pe-asymmetric-cooling.md | — |
| SWD-461 | Sub-task | Tests, CalVer, changelog, App sync for PE cooling power | Done | SWD-459 | docs/agents/PLAN-pe-asymmetric-cooling.md | — |
| SWD-456 | Task | [Iterate] Room view still shows idle U=0 / free-response instead of the NMPC plan | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/648 (`87be700`) |
| SWD-457 | Sub-task | Import slow_slot_start_s so NMPC apply can install the plan | Done | SWD-456 | docs/agents/ITERATE.md | — |
| SWD-458 | Sub-task | Tests, CalVer, changelog, App sync for NMPC plan plot | Done | SWD-456 | docs/agents/ITERATE.md | — |
| SWD-453 | Task | [Bug] Parameter estimation Load failed on one-week dataset | Done | — | docs/agents/PLAN-pe-week-load-failed.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/647 (`5d4f748`) |
| SWD-454 | Sub-task | Background PE job so Ingress does not drop the request | Done | SWD-453 | docs/agents/PLAN-pe-week-load-failed.md | — |
| SWD-455 | Sub-task | Tests, CalVer, changelog, App sync for PE job | Done | SWD-453 | docs/agents/PLAN-pe-week-load-failed.md | — |
| SWD-450 | Task | [Bug] Room plots missing optimal trajectories, price forecast, and outdoor temperature | Done | — | docs/agents/PLAN-2026-08-30-swd-450-catalog-forecast-attrs.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/646 (`e9af33c`) |
| SWD-451 | Sub-task | Preserve forecast attrs on catalog overlay + outdoor fallback | Done | SWD-450 | docs/agents/PLAN-2026-08-30-swd-450-catalog-forecast-attrs.md | — |
| SWD-452 | Sub-task | Tests, CalVer, changelog, App sync | Done | SWD-450 | docs/agents/PLAN-2026-08-30-swd-450-catalog-forecast-attrs.md | — |
| SWD-447 | Task | [Iterate] Ingress LOAD ERROR — extendDatasetToNow not found | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/645 (`d82931e`) |
| SWD-448 | Sub-task | Import extendDatasetToNow from room-charts | Done | SWD-447 | docs/agents/ITERATE.md | — |
| SWD-449 | Sub-task | Tests, CalVer, changelog, App sync for panel import | Done | SWD-447 | docs/agents/ITERATE.md | — |
| SWD-437 | Task | [Tweak] P deadband when NMPC is near zero | Done | — | docs/agents/PLAN-p-deadband-nmpc-off.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/637 (`07c3ac0`) |
| SWD-438 | Sub-task | Gated P deadband + live tuning keys | Done | SWD-437 | docs/agents/PLAN-p-deadband-nmpc-off.md | — |
| SWD-439 | Sub-task | Tuning pane, tests, CalVer, App sync | Done | SWD-437 | docs/agents/PLAN-p-deadband-nmpc-off.md | — |
| SWD-440 | Story | [Adopt] Meet structure catalog on the production tree | Done | — | docs/agents/ADOPT.md | Done — SWD-441–446; last PR #643 (`c7f67fb`) |
| SWD-441 | Task | [Adopt] Split engine controller facade into SDE, EKF, linearised, MPC | Done | SWD-440 | docs/agents/ADOPT.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/638 (`3b4122d`) |
| SWD-442 | Task | [Adopt] Split HeatingRuntime into ticker, MQTT, NMPC, panel-state collaborators | Done | SWD-440 | docs/agents/ADOPT.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/639 (`3ac73c7`) |
| SWD-443 | Task | [Adopt] Split ControlEngine build, live loop, and tuning preview | Done | SWD-440 | docs/agents/ADOPT.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/640 (`1421b56`) |
| SWD-444 | Task | [Adopt] Split estimation, diagnostics, and PE HTTP god modules | Done | SWD-440 | docs/agents/ADOPT.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/641 (`9d736b6`) |
| SWD-445 | Task | [Adopt] Split remaining Ingress panel god modules | Done | SWD-440 | docs/agents/ADOPT.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/642 (`01652bf`) |
| SWD-446 | Task | [Adopt] Remaining engine, MQTT, and thin-bridge catalog rows | Done | SWD-440 | docs/agents/ADOPT.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/643 (`c7f67fb`) |
| SWD-434 | Task | [Iterate] Room DISTURBANCES outdoor/solar history as solid lines | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/636 (`161ad7f`) |
| SWD-435 | Sub-task | Restore DISTURBANCES outdoor/solar history to solid lines | Done | SWD-434 | docs/agents/ITERATE.md | — |
| SWD-436 | Sub-task | Tests, CalVer, changelog, App sync for DISTURBANCES lines | Done | SWD-434 | docs/agents/ITERATE.md | — |
| nmpc-input-bias | Task | [Bug] NMPC input bias (u_ref) must step on each accepted plan | Done | — | docs/agents/PLAN-nmpc-input-bias.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/635 (`ff1449e`) |
| SWD-432 | Task | [Sandbox] Room-view temperature forecast oscillates while planned power is smooth | Done | — | docs/agents/SANDBOX-forecast-oscillation.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/634 (`9d815d1`) |
| SWD-431 | Task | [Sandbox] Controller Tuning preview vs room view predictions in HA app plots | Done | — | docs/agents/SANDBOX-preview-room-plots.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/633 (`eb3fa55`) |
| SWD-430 | Task | [Sandbox] Loading animation belongs on NMPC/P countdown timers | Done | — | docs/agents/SANDBOX-kpi-loading.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/632 (`fa64ee6`) |
| SWD-426 | Task | [Tweak] Align NMPC/P timers on one grid; independent solves; KPI loading | Done | — | docs/agents/PLAN-nmpc-p-independent-grid.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/631 (`507873f`) |
| SWD-427 | Sub-task | Shared grid + independent NMPC and P (no extra P after NLP) | Done | SWD-426 | docs/agents/PLAN-nmpc-p-independent-grid.md | — |
| SWD-428 | Sub-task | Loading animation on compute KPI cards while solving | Done | SWD-426 | docs/agents/PLAN-nmpc-p-independent-grid.md | — |
| SWD-429 | Sub-task | Tests, CalVer, changelog, App sync | Done | SWD-426 | docs/agents/PLAN-nmpc-p-independent-grid.md | — |
| SWD-417 | Task | [Sandbox] Forecast temperature jitter vs EKF/OCP integrator substeps | Done | — | docs/agents/SANDBOX-forecast-jitter.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/630 (`eb28be6`) |
| SWD-424 | Sub-task | Keep room-view Forecast on the NMPC air path | Done | SWD-417 | docs/agents/SANDBOX-forecast-jitter.md | — |
| SWD-425 | Sub-task | Tests, CalVer, App sync for NMPC Forecast plot | Done | SWD-417 | docs/agents/SANDBOX-forecast-jitter.md | — |
| SWD-418 | Task | [Bug] NMPC long timer resets when the solve finishes — schedule must stay drift-free | Done | — | docs/agents/PLAN-nmpc-timer-drift.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/628 (`554e9fc`) |
| SWD-419 | Sub-task | Wall-clock epoch: do not restamp NMPC/control timers when a solve finishes | Done | SWD-418 | docs/agents/PLAN-nmpc-timer-drift.md | — |
| SWD-420 | Sub-task | Tests, CalVer, changelog, App sync for drift-free NMPC timer | Done | SWD-418 | docs/agents/PLAN-nmpc-timer-drift.md | — |
| SWD-421 | Task | [Iterate] Room view plots 15-minute steps instead of the 2-hour NMPC trajectory | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/629 (`c0a2c13`) |
| SWD-422 | Sub-task | Keep Ingress plots on the installed 2-hour NMPC plan | Done | SWD-421 | docs/agents/ITERATE.md | — |
| SWD-423 | Sub-task | Tests, CalVer, changelog, App sync | Done | SWD-421 | docs/agents/ITERATE.md | — |
| SWD-414 | Task | [Iterate] Room view optimal trajectory still U=0 / 30°C free response | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/627 (`434027a`) |
| SWD-415 | Sub-task | Accept NMPC plans that beat zero-heat and plot that trajectory | Done | SWD-414 | docs/agents/ITERATE.md | — |
| SWD-416 | Sub-task | Tests, CalVer, changelog, App sync for trajectory plot | Done | SWD-414 | docs/agents/ITERATE.md | — |
| SWD-408 | Task | [Tweak] Proper Heating Assistant icon for App and HA integration | Done | — | docs/agents/PLAN-heating-assistant-icon.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/625 (`acbd43b`) |
| SWD-409 | Sub-task | Brand mark SVG/PNGs + App and integration wiring | Done | SWD-408 | docs/agents/PLAN-heating-assistant-icon.md | — |
| SWD-410 | Sub-task | Tests, CalVer, changelog, App sync | Done | SWD-408 | docs/agents/PLAN-heating-assistant-icon.md | — |
| SWD-411 | Task | [Iterate] Heat and cool on the fast loop when comfort bounds are already violated | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/626 (`ae030d9`) |
| SWD-412 | Sub-task | Fast P comfort fallback and publish actuators on NMPC apply | Done | SWD-411 | docs/agents/ITERATE.md | — |
| SWD-413 | Sub-task | Tests, CalVer, App sync for comfort fallback | Done | SWD-411 | docs/agents/ITERATE.md | — |
| SWD-405 | Task | [Iterate] NMPC planned power stays at 0 kW when cooling is needed | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/624 (`68d86cf`) |
| SWD-406 | Sub-task | Refresh NMPC forecast on apply and retry idle zero plans | Done | SWD-405 | docs/agents/ITERATE.md | — |
| SWD-407 | Sub-task | Tests, CalVer, App sync for cooling plan plot | Done | SWD-405 | docs/agents/ITERATE.md | — |
| SWD-400 | Task | [Iterate] NMPC must choose negative heater power when cooling is allowed | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/623 (`61c56e7`) |
| SWD-401 | Sub-task | Scale NMPC NLP so SLSQP can choose negative u | Done | SWD-400 | docs/agents/ITERATE.md | — |
| SWD-402 | Sub-task | Tests, CalVer, App sync for signed NMPC u | Done | SWD-400 | docs/agents/ITERATE.md | — |
| SWD-403 | Sub-task | Publish NMPC cycle attrs and dual countdown rings in the UI | Done | SWD-400 | docs/agents/ITERATE.md | — |
| SWD-404 | Sub-task | Tests, cache-bust, changelog, App sync for dual countdown | Done | SWD-400 | docs/agents/ITERATE.md | — |
| SWD-392 | Story | [Explore] Hierarchical nonlinear OCP + P tracking | Done | — | docs/ROADMAP.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/622 (`c1701de`) |
| SWD-393 | Task | [Model] Formulate hierarchical NMPC + P-FF, hold/fail/watchdog | Done | SWD-392 | docs/agents/MODEL-nmpc-p-ff.md | Done — `/sandbox SWD-394` |
| SWD-394 | Task | [Sandbox] Offline NMPC period + closed-loop P eval | Done | SWD-392 | docs/agents/SANDBOX-nmpc-p-ff.md | Done — `/define SWD-395` |
| SWD-395 | Task | [Define] Production NMPC + P, single heater, last-plan hold, 5 h → off + notify | Done | SWD-392 | docs/agents/PLAN-nmpc-p-ff.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/622 (`c1701de`) |
| SWD-396 | Sub-task | Mean OCP + analytic Jacobian + accept/reject (replace QP path) | Done | SWD-395 | docs/agents/PLAN-nmpc-p-ff.md | — |
| SWD-397 | Sub-task | Fast P + heater K_p; EKF uses applied u | Done | SWD-395 | docs/agents/PLAN-nmpc-p-ff.md | — |
| SWD-398 | Sub-task | NLP worker thread + 5 h fail watchdog + notify | Done | SWD-395 | docs/agents/PLAN-nmpc-p-ff.md | — |
| SWD-399 | Sub-task | Timing triple 2 h / 8 substeps / 36 h, Tuning UI, tests, CalVer, App sync | Done | SWD-395 | docs/agents/PLAN-nmpc-p-ff.md | — |
| SWD-389 | Task | [Tweak] Guide PE users on how much data to gather | Done | — | docs/agents/PLAN-pe-data-duration.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/621 |
| SWD-390 | Sub-task | PE page + TUNING duration guidance copy | Done | SWD-389 | docs/agents/PLAN-pe-data-duration.md | — |
| SWD-391 | Sub-task | Tests, CalVer, App sync | Done | SWD-389 | docs/agents/PLAN-pe-data-duration.md | — |
| SWD-385 | Task | [Bug] System Status BAD tag quality despite fine HA sensor measurements | Done | — | docs/agents/PLAN-tag-quality-stale-bad.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/620 |
| SWD-386 | Sub-task | Catalog overlay + prune persisted BAD tag quality | Done | SWD-385 | docs/agents/PLAN-tag-quality-stale-bad.md | — |
| SWD-387 | Sub-task | Republish inbound tags when HA has started | Done | SWD-385 | docs/agents/PLAN-tag-quality-stale-bad.md | — |
| SWD-388 | Sub-task | Tests, CalVer, App sync | Done | SWD-385 | docs/agents/PLAN-tag-quality-stale-bad.md | — |
| SWD-356 | Task | [Iterate] Restart required as Settings repair, not an Update card | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/619 |
| SWD-357 | Sub-task | Settings Restart required repair + tombstone MQTT update | Done | SWD-356 | docs/agents/ITERATE.md | — |
| SWD-358 | Sub-task | Tests, CalVer, App sync | Done | SWD-356 | docs/agents/ITERATE.md | — |
| SWD-352 | Task | [Tweak] App update changelog, restart-required Settings card | Done | — | docs/agents/PLAN-app-update-path.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/618 |
| SWD-353 | Sub-task | App CHANGELOG.md for Supervisor update dialog | Done | SWD-352 | docs/agents/PLAN-app-update-path.md | — |
| SWD-354 | Sub-task | Restart-required Settings update entity | Done | SWD-352 | docs/agents/PLAN-app-update-path.md | — |
| SWD-355 | Sub-task | Tests, CalVer, App sync | Done | SWD-352 | docs/agents/PLAN-app-update-path.md | — |
| SWD-349 | Task | [Tweak] PE thermal-mass bounds and prior toward selected room size | Done | — | docs/agents/PLAN-pe-mass-prior.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/617 |
| SWD-350 | Sub-task | Relative C bounds + MAP toward selected room size | Done | SWD-349 | docs/agents/PLAN-pe-mass-prior.md | — |
| SWD-351 | Sub-task | Tests, CalVer, App sync | Done | SWD-349 | docs/agents/PLAN-pe-mass-prior.md | — |
| SWD-344 | Task | [Bug] PE simulation missing heater/disturbances, poor wall init, category copy | Done | — | docs/agents/PLAN-pe-sim-aux-tw0.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/616 |
| SWD-345 | Sub-task | Plot PE heater/disturbances from ID history | Done | SWD-344 | docs/agents/PLAN-pe-sim-aux-tw0.md | — |
| SWD-346 | Sub-task | Compute and apply optimal Tw0 on Simulate | Done | SWD-344 | docs/agents/PLAN-pe-sim-aux-tw0.md | — |
| SWD-347 | Sub-task | Simplify PE category descriptions | Done | SWD-344 | docs/agents/PLAN-pe-sim-aux-tw0.md | — |
| SWD-348 | Sub-task | Tests, cache-bust, CalVer, App sync | Done | SWD-344 | docs/agents/PLAN-pe-sim-aux-tw0.md | — |
| SWD-343 | Task | [Iterate] Clickable PE category guides for low-comfort identification data | Done | — | docs/agents/ITERATE.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/615 |
| SWD-323 | Story | [Explore] Parameter estimation effectiveness and guidance | Done | — | docs/ROADMAP.md | Done — map complete (PR #612) |
| SWD-334 | Task | [Model] Contact-gated UA + occupancy disturbance (no envelope lock) | Done | SWD-323 | docs/agents/MODEL-pe-contact-ua-occupancy.md | Done — SWD-335 shipped |
| SWD-335 | Task | [Define] Robust open-loop PE for household extras | Done | SWD-323 | docs/agents/PLAN-pe-robust-open-loop.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/614 |
| SWD-336 | Sub-task | Identified contact-gated UA in production open-loop PE | Done | SWD-335 | docs/agents/PLAN-pe-robust-open-loop.md | — |
| SWD-337 | Sub-task | Backend dataset category coverage for PE | Done | SWD-335 | docs/agents/PLAN-pe-robust-open-loop.md | — |
| SWD-338 | Sub-task | PE page recommended-data checklist | Done | SWD-335 | docs/agents/PLAN-pe-robust-open-loop.md | — |
| SWD-339 | Sub-task | Tests, val bar, version bump, App sync | Done | SWD-335 | docs/agents/PLAN-pe-robust-open-loop.md | — |
| SWD-332 | Task | [Tweak] Offline PE validation open-loop prediction accuracy | Done | SWD-323 | docs/agents/PLAN-pe-validation-accuracy.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/613 |
| SWD-333 | Sub-task | Harness train/val open-loop score + report | Done | SWD-332 | docs/agents/REPORT-pe-robustness-household.md | — |
| SWD-328 | Task | [Research] Synthesise household-like single-room traces and identify robust PE approaches | Done | SWD-323 | docs/agents/RESEARCH-pe-robustness-household.md | Done — SWD-329 shipped |
| SWD-329 | Task | [Define] Offline PE robustness analysis on synthetic household-like data | Done | SWD-323 | docs/agents/PLAN-pe-robustness-household.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/612 |
| SWD-330 | Sub-task | Offline PE robustness harness + on-demand factorial + report | Done | SWD-329 | docs/agents/REPORT-pe-robustness-household.md | — |
| SWD-326 | Task | [Define] Offline PE combined vs separated/staged benchmark | Done | SWD-323 | docs/agents/PLAN-pe-split-benchmark.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/611 (`9b1e380`) |
| SWD-327 | Sub-task | Offline PE split/staging benchmark harness + report | Done | SWD-326 | docs/agents/REPORT-pe-dataset-separation.md | — |
| SWD-325 | Task | [Model] Formulate PE treatment of hidden wall temperature and staged windows | Done | SWD-323 | docs/agents/MODEL-pe-hidden-tw.md | `/define SWD-326` |
| SWD-324 | Task | [Research] Diagnose current PE and survey applicable improvements | Done | SWD-323 | docs/agents/RESEARCH-pe-effectiveness.md | `/define SWD-326` |
| SWD-322 | Task | [Tweak] Exclude open door/window room samples from Parameter Estimation | Done | — | docs/agents/PLAN-pe-exclude-window-open.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/610 (`ee41ee6`) |
| SWD-321 | Task | [Tweak] Room DISTURBANCES outdoor/solar history as Measured-style points | Done | — | docs/agents/PLAN-disturbances-history-points.md | Done — feature #607; CalVer closeout https://github.com/marcuskrogh/HeatingAssistant/pull/609 |
| SWD-316 | Story | [Explore] Estimation history hole while plots + control OK | Done | — | docs/ROADMAP.md | Done — map complete |
| SWD-319 | Task | [Research] Discriminate id_history JSONL hole vs horizon load ignoring disk | Done | SWD-316 | docs/agents/RESEARCH-estimation-history-hole.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/601 |
| SWD-320 | Task | [Bug] resolve_history(horizon) merges id_history JSONL | Done | SWD-316 | docs/agents/PLAN-resolve-history-horizon-jsonl.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/602 |
| SWD-318 | Task | [Bug] Align ID sample write with plot cadence | Done | SWD-316 | docs/agents/PLAN-id-sample-plot-cadence.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/603 (`58d71a7`) |
| SWD-317 | Task | [Tweak] Surface ID history health on System Status | Done | SWD-316 | docs/agents/PLAN-id-history-status-card.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/605 |
| SWD-315 | Task | [Tweak] Remove IPOPT from parameter estimation — SciPy only | Done | — | docs/agents/PLAN-remove-ipopt-scipy.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/599 (`0397afa`) |
| SWD-311 | Task | [Refine] App-first README and docs cleanup | Done | — | docs/agents/PLAN-app-first-docs.md | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/598 (`ebb8aae`) |
| SWD-312 | Sub-task | Consumer README rewrite + App sync | Done | SWD-311 | docs/agents/PLAN-app-first-docs.md | — |
| SWD-313 | Sub-task | Consumer docs update/delete | Done | SWD-311 | docs/agents/PLAN-app-first-docs.md | — |
| SWD-314 | Sub-task | Maintainer docs + cleanup + remove HACS | Done | SWD-311 | docs/agents/PLAN-app-first-docs.md | — |
| SWD-307 | Task | [Tweak] Calendar versioning YYYY.MM.PATCH (HA-style) | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/597 |
| SWD-308 | Sub-task | Cut over live versions to 2026.08.0 | Done | SWD-307 | (archived) | — |
| SWD-310 | Sub-task | Encode YYYY.MM.PATCH in sync lock + docs | Done | SWD-307 | (archived) | — |
| SWD-309 | Sub-task | Tests for calver lock and assertions | Done | SWD-307 | (archived) | — |
| SWD-300 | Task | [Feature] System Status page, health indicator, and Parameter Estimation rename | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/595 |
| SWD-304 | Sub-task | Backend quality enum + status payload | Done | SWD-300 | (archived) | — |
| SWD-302 | Sub-task | System Status page + health indicator + remove pill | Done | SWD-300 | (archived) | — |
| SWD-303 | Sub-task | Overview system strip + controller KPIs split | Done | SWD-300 | (archived) | — |
| SWD-301 | Sub-task | Hard-cut Parameter Estimation rename | Done | SWD-300 | (archived) | — |
| SWD-305 | Sub-task | Tests + version bump + package sync | Done | SWD-300 | (archived) | — |
| SWD-299 | Bug | [Bug] Identification KPIs (model fit / R² / RMSE / Estimated) not populating Overview or System Identification index | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/593 |
| SWD-298 | Bug | [Bug] Door/window sensors do not turn off heaters after configured debounce — App missing window override | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/592 |
| SWD-297 | Bug | [Bug] Applied / measured solar gain stuck at 0 — App hass_states hardcodes solar_gain_measured | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/590 |
| SWD-296 | Bug | [Bug] Sysid Apply Parameters not restored — defaults on reload + panel jumps to overview | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/588 |
| SWD-289 | Task | [Define] Restore system identification page — end App sysid no-ops + fix panel chart imports | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/586 |
| SWD-293 | Sub-task | Fix sysid panel ES-module imports/exports + cache bust | Done | SWD-289 | (archived) | — |
| SWD-292 | Sub-task | Restore engine sysid modules deleted in SWD-262 | Done | SWD-289 | (archived) | — |
| SWD-294 | Sub-task | Wire P0 compute services + publish sysid/open-loop sensors | Done | SWD-289 | (archived) | — |
| SWD-290 | Sub-task | Wire Apply/persist + parameter_history on controller_config | Done | SWD-289 | (archived) | — |
| SWD-295 | Sub-task | Wire DatasetStore create/delete under App data_dir | Done | SWD-289 | (archived) | — |
| SWD-291 | Sub-task | Regression tests + version 2.0.27 + App package sync | Done | SWD-289 | (archived) | — |
| SWD-288 | Bug | [Bug] Climate card setpoints reset to default — overview and room view cannot change target or comfort band | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/584 |
| SWD-286 | Bug | [Bug] Room temperature plot ignores schedule comfort_offset — constraints stay at room default | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/582 |
| SWD-287 | Bug | [Bug] Expanded schedule on Schedules detail collapses on its own during reconfiguration | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/582 |
| SWD-285 | Bug | [Bug] Controller Tuning preview ignores unapplied params — only works after Apply | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/580 |
| SWD-284 | Bug | [Bug] Room view Price plot missing historical data — App never publishes electricity_price sensor | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/578 |
| SWD-283 | Bug | [Bug] Large whitespace between Save Current Window inputs on mobile | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/576 |
| SWD-282 | Bug | [Bug] Solar gain plot stuck at zero despite High exposure — App room build drops aperture | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/574 |
| SWD-281 | Task | [Iterate] App update clears room-plot / ID history — persist under /data like original integration | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/572 |
| SWD-280 | Task | [Iterate] Climate heat-pump actuation missing after thin bridge — planned cooling never reaches HA entity | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/570 |
| SWD-279 | Task | [Iterate] Plot forecasts still flat — JSON-safe attrs, weather.get_forecasts, linearised from estimated output | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/568 |
| SWD-278 | Task | [Iterate] Incomplete plot forecasts — wire outdoor/solar/price into MPC compute + MQTT attrs | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/567 |
| SWD-277 | Task | [Iterate] Plot samples too dense + empty forecasts — gate history to update_interval and expose MPC trajectories | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/566 |
| SWD-276 | Task | [Iterate] KPIs/plots flat overnight — App has no wall-clock history/control ticker | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/565 |
| SWD-275 | Task | [Iterate] MQTT rc=5 — SUPERVISOR_TOKEN missing without with-contenv entrypoint | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/564 |
| SWD-274 | Task | [Iterate] MQTT rc=5 — SUPERVISOR_TOKEN missing without hassio_api | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/563 |
| SWD-273 | Task | [Iterate] MQTT still disconnected after mqtt:need — one-shot discovery + ignored SSL/endpoint | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/560 |
| SWD-271 | Task | [Iterate] Streamline config UX — searchable HA entity picker + Environment recommendations | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/559 |
| SWD-270 | Task | [Iterate] Ingress shows MQTT disconnected — App missing Supervisor Mosquitto credentials | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/558 |
| SWD-269 | Task | [Iterate] Ingress features empty / Controller Tuning 502 after MQTT non-blocking start | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/557 |
| SWD-268 | Task | [Iterate] Ingress 502 Bad Gateway after MQTT update — App not ready | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/554 |
| SWD-267 | Task | [Iterate] Ingress entity picker only shows App sensors — cannot wire HA room temperatures | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/551 |
| SWD-266 | Task | [Iterate] Ingress panel LOAD ERROR — bare module path fails dynamic import | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/550 |
| SWD-265 | Task | [Iterate] Ingress UI stuck on Loading App API | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/549 |
| SWD-264 | Task | [Iterate] Ingress UI 404 — static assets missing from pip install | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/548 |
| SWD-263 | Task | [Iterate] App rejects --options-path and crash-loops on start | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/547 |
| SWD-262 | Task | [Iterate] Finish HAOS App: Ingress parity, thin-only tree, port clash | Done | — | (archived) | Done — https://github.com/marcuskrogh/HeatingAssistant/pull/546 |
| SWD-255 | Task | HAOS App + thin MQTT integration (compute isolation) | Done | — | (archived) | Done |
| SWD-258 | Sub-task | App packaging skeleton (sync + version lock) | Done | SWD-255 | (archived) | — |
| SWD-259 | Sub-task | Thin MQTT integration (entity↔tag bridge) | Done | SWD-255 | (archived) | — |
| SWD-260 | Sub-task | MQTT contract (topics, bindings, status) | Done | SWD-255 | (archived) | — |
| SWD-261 | Sub-task | Move compute into App (parity + persistence) | Done | SWD-255 | (archived) | — |
| SWD-257 | Sub-task | Port dashboard to App Ingress | Done | SWD-255 | (archived) | — |
| SWD-256 | Sub-task | E2E hardening (load isolation + regressions) | Done | SWD-255 | (archived) | — |
| SWD-254 | Bug | [Bug] Remove dual-mode nonlinear MPC (revert to pre-SWD-240) — HA hang | Done | — | (archived) | Done |
| SWD-238 | Story | Dual-mode MPC (linear / non-linear) | To Do | — | — | Dual-mode removed from main by SWD-254; optional Story closeout |
| SWD-248 | Task | [Bug] stop NMPC hang (executor, timeout, SciPy horizon cap) | Done | — | — | Done — superseded by SWD-254; PR #542 closed |

## Log
- 2026-09-02 — `/review` SWD-469 CLEAN (focused sequential). COMMENT on PR #652. last NMPC duration wraps `solve_nmpc_blocking`; overview harness `appendChild` matches DOM move. Next ship merge.
- 2026-09-02 — `/ship` SWD-469 remaining: architect, sandbox accept, implement expand host + last NMPC duration, tests 4 passed + node harness, CalVer 2026.08.41. Next `/review SWD-469`.
- 2026-09-02 — `/define` SWD-469: PLAN `docs/agents/PLAN-kpi-expand-detail.md` (feature / feature-standard, sandbox inject). Click KPI → top of section + detail panel; last NMPC duration on expand. Sub-tasks SWD-470–473. Next `/architect SWD-469`.
- 2026-08-31 — `/ship` SWD-465 via PR #651 (`3719368`): P and room Forecast stay on the original NMPC air trajectory for the 2 h window; review CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.40`. Next Done.
- 2026-08-31 — `/test`+`/restructure` SWD-465: 93 passed. Wind-stable leftover `T_ref`; pad-past-end and no-plan `_forecast_T`. `_pad_plan_tail` campground OK (flat C=6). Next `/review SWD-465`.
- 2026-08-31 — `/test`+`/harden` SWD-465: 87 passed (frozen T_ref, input-bias, deadband, plot grid, forecast resim, SWD-395). Copy at accept only. Next `/review SWD-465`.
- 2026-08-31 — `/implement` SWD-465 In Progress: freeze P `T_ref` copies at accept; PR https://github.com/marcuskrogh/HeatingAssistant/pull/651. Next `/test SWD-465`.
- 2026-08-31 — `/define` SWD-465: PLAN `docs/agents/PLAN-p-frozen-nmpc-tref.md` (bug / fix-fast). P tracks accept-time NMPC `(T_ref, u_ref)` for the 2 h window; Forecast resim stays plot-only. Sub-tasks SWD-466–467. Next `/implement SWD-465`.
- 2026-08-30 — `/ship` SWD-462 via PR #650 (`27ccf06`): historical solar gain follows weather cloud cover; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.39`. Next Done.
- 2026-08-30 — `/review-fix` SWD-462 CLEAN (focused sequential): 0 blockers / 0 should-fix. APPROVE on PR #650. Next `/ship SWD-462`.
- 2026-08-30 — `/define` SWD-462: PLAN `docs/agents/PLAN-solar-gain-cloud-cover.md` (bug / fix-fast). Historical solar k=0 must use cloud cover; Sub-tasks SWD-463–464. Next `/implement SWD-462`.
- 2026-08-30 — `/ship` SWD-459 via PR #649 (`3a30be0`): PE Heating Input uses cooling capacity for `u < 0`; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.38`. Next Done.
- 2026-08-30 — `/ship` SWD-459 closeout: merged `origin/main`; CalVer 2026.08.38 (main shipped 37). Changelog `heating_assistant/CHANGELOG.md` `# 2026.08.38`. Next merge PR #649.
- 2026-08-30 — `/review-fix` SWD-459 CLEAN (focused sequential): 0 blockers / 1 should-fix (cooling fallback to heating gain) fixed forward. COMMENT on PR #649. Next `/ship SWD-459`.
- 2026-08-30 — `/harden` SWD-459: heat-only `can_cool` guard; drop stray GSHP pellet comment. Next `/review-fix SWD-459`.
- 2026-08-30 — `/test` SWD-459: 1088 passed / 88 skipped / 18 deselected (fast). Extra partial-cool and heat-only PE series. No extra product behaviour. Next `/harden SWD-459`.
- 2026-08-30 — `/implement` SWD-459 In Progress: PE Heating Input uses cooling capacity for `u < 0`; CalVer 2026.08.38. Next `/test SWD-459`.
- 2026-08-30 — `/define` SWD-459: PLAN `docs/agents/PLAN-pe-asymmetric-cooling.md` (bug / fix-fast). PE Heating Input used heating capacity for negative `u`; Sub-tasks SWD-460–461. Next `/implement SWD-459`.
- 2026-08-30 — `/ship` SWD-456 via PR #648 (`87be700`): restore NMPC plan on plots and heater command; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.37`. Next Done.
- 2026-08-30 — `/review-fix` SWD-456 CLEAN (focused sequential): 0 blockers / 0 should-fix. COMMENT on PR #648. Next `/ship SWD-456`.
- 2026-08-30 — `/harden` SWD-456: import sits with the other `nmpc_timing` helpers on `TickerMixin`; `_slow_slot_start` still one branch. No extract. Next `/review-fix SWD-456`.
- 2026-08-30 — `/test` SWD-456: 75 passed (SWD-456, input-bias, SWD-426/414/395, CalVer). Added None-epoch `_slow_slot_start` and `_u_prev` lock. Working surface: none (no live HA). Next `/harden SWD-456`.
- 2026-08-30 — `/iterate` SWD-456 In Progress: import `slow_slot_start_s` so NMPC apply can install the plan; PR https://github.com/marcuskrogh/HeatingAssistant/pull/648; CalVer 2026.08.37. Next `/test SWD-456`.
- 2026-08-30 — `/ship` SWD-453 via PR #647 (`5d4f748`): background PE job so Ingress does not drop week-length fits; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.36`. Next Done.
- 2026-08-30 — `/review-fix` SWD-453 CLEAN (focused sequential): 0 blockers / 0 should-fix. COMMENT on PR #647. Next `/ship SWD-453`.
- 2026-08-30 — `/harden` SWD-453: clump PE worker args into `_PeJobWork`; snapshot copy extracted. Next `/review-fix SWD-453`.
- 2026-08-30 — `/test` SWD-453: 1080 passed / 88 skipped / 18 deselected (fast). Extra HTTP double-start while running. No extra product behaviour. Next `/harden SWD-453`.
- 2026-08-30 — `/implement` SWD-453 In Progress: background PE job + Ingress poll; CalVer 2026.08.36; PR https://github.com/marcuskrogh/HeatingAssistant/pull/647. Next `/test SWD-453`.
- 2026-08-30 — `/define` SWD-453: PLAN `docs/agents/PLAN-pe-week-load-failed.md` (bug / fix-fast). Background PE job so Ingress does not drop one-week fits; Sub-tasks SWD-454–455. Next `/implement SWD-453`.
- 2026-08-30 — `/ship` SWD-450 via PR #646 (`e9af33c`): catalog overlay keeps weather/price forecast attrs; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.35`. Next Done.
- 2026-08-30 — `/review-fix` SWD-450 CLEAN (focused sequential): 0 blockers / 0 should-fix. COMMENT on PR #646. Next `/ship SWD-450`.
- 2026-08-30 — `/harden` SWD-450: outdoor °C extracted to `_temperature_from_tag`; overlay comment explains attr re-attach vs MQTT scalar-only. Next `/review-fix SWD-450`.
- 2026-08-30 — `/test` SWD-450: 52 passed / 1 skipped (catalog overlay, SWD-278/279/385, price history, CalVer). No extra tests. Next `/harden SWD-450`.
- 2026-08-30 — `/implement` SWD-450 In Progress: catalog overlay preserves weather/price forecast attrs; skip non-numeric catalog states; outdoor °C falls back to weather `temperature` attr; CalVer 2026.08.35; PR https://github.com/marcuskrogh/HeatingAssistant/pull/646. Next `/test SWD-450`.
- 2026-08-30 — `/ship` SWD-447 via PR #645 (`d82931e`): Ingress LOAD ERROR — `extendDatasetToNow` imported from `room-charts.js`; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.34`. Next Done.
- 2026-08-30 — `/review-fix` SWD-447 CLEAN (focused): 0 blockers / 0 should-fix. COMMENT on PR #645. CI green. Next `/ship SWD-447`.
- 2026-08-30 — `/iterate` SWD-447 In Progress: Ingress LOAD ERROR — `extendDatasetToNow` imported from `room-charts.js`; PR https://github.com/marcuskrogh/HeatingAssistant/pull/645; CalVer 2026.08.34. Next `/test SWD-447`.
- 2026-08-29 — `/adopt` SWD-444 characterize: lock suite 68 passed / 5 skipped / 6 deselected. Branch `cursor/swd-444-adopt-estimation-1253`. Next `/implement SWD-444`.
- 2026-08-29 — `/ship` SWD-443 via PR #640 (`1421b56`): ControlEngine build/preview mixins; changelog skipped (internal structure). Next `/adopt SWD-444`.
- 2026-08-29 — `/review-fix` SWD-443 CLEAN (focused): 0 blockers / 0 should-fix. COMMENT on PR #640. Lock suite 223 passed / 5 skipped. Next `/ship SWD-443`.
- 2026-08-29 — `/harden` SWD-443: DRY live forecast cache via `_snapshot_from_controller`; flatten preview nested tries. Lock suite 223 passed / 5 skipped. PR https://github.com/marcuskrogh/HeatingAssistant/pull/640. Next `/review-fix SWD-443`.
- 2026-08-29 — `/adopt` SWD-443 characterize: lock suite 221 passed / 5 skipped on current `control_loop.py`. Branch `cursor/swd-443-adopt-control-engine-1253`. Next `/implement SWD-443`.
- 2026-08-29 — `/ship` SWD-442 via PR #639 (`3ac73c7`): HeatingRuntime mixins for ticker/NMPC/`hass_states`/wiring/history; changelog skipped (internal structure). Next `/adopt SWD-443`.
- 2026-08-29 — `/review-fix` SWD-442 CLEAN (focused): 0 blockers / 0 should-fix. COMMENT on PR #639. Lock suite 132 passed / 1 skipped. Next `/ship SWD-442`.
- 2026-08-29 — `/adopt` SWD-442 implement: HeatingRuntime mixins for ticker/NMPC/`hass_states`/wiring/history; PR https://github.com/marcuskrogh/HeatingAssistant/pull/639. Lock suite 132 passed / 1 skipped. Next `/review-fix SWD-442`.
- 2026-08-29 — `/ship` SWD-441 via PR #638 (`3b4122d`): controller facade split into SDE/EKF/linearised/MPC modules; changelog skipped (internal structure). Next `/adopt SWD-442`.
- 2026-08-29 — `/ship` SWD-437 via PR #637 (`07c3ac0`): gated P deadband when NMPC is near zero; live `p_deadband` / `u_ref_gate`; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.33`. Next Done.
- 2026-08-29 — `/review-fix` SWD-441 CLEAN (focused): 0 blockers / 0 should-fix. COMMENT on PR #638. Lock suite 226 passed / 6 skipped. Next `/ship SWD-441`.
- 2026-08-29 — `/ship` SWD-434 via PR #636 (`161ad7f`): DISTURBANCES outdoor/solar history restored to solid lines; forecasts stay dashed; Indoor Measured stays points; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.32`. Next Done.
- 2026-08-29 — `/review-fix` SWD-434 CLEAN (focused): 0 blockers / 0 should-fix / 0 notes. COMMENT (cannot APPROVE own PR). Local fast suite 994 passed, 88 skipped, 18 deselected. GitHub pytest-fast + panel-harness green; slow shards in flight at review. Next `/ship SWD-434`.
- 2026-08-29 — `/iterate` SWD-434 In Review: DISTURBANCES outdoor/solar history restored to solid lines (grey/yellow); PR https://github.com/marcuskrogh/HeatingAssistant/pull/636; CalVer 2026.08.32. Fast suite 994 passed, 88 skipped, 18 deselected. Next `/review-fix SWD-434`.
- 2026-08-29 — `/iterate` SWD-434 from SWD-321: restore DISTURBANCES outdoor/solar history to solid lines (keep colours); Relates SWD-321; Sub-tasks SWD-435–436. Next `/review-fix SWD-434`.
- 2026-08-28 — `/ship` nmpc-input-bias via PR #635 (`ff1449e`): `u_ref` steps on accept; NLP warm-start recedes one slow interval; P-only apply without EKF; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.31`. Next Done.
- 2026-08-28 — `/review-fix` nmpc-input-bias CLEAN (focused): 0 blockers / 0 should-fix / 2 deferred notes (lock-timeout actuators vs `_u_prev`; un-awaited P publish coroutine). COMMENT (cannot APPROVE own PR). CI 6/6 green. Next `/ship`.
- 2026-08-28 — `/implement` nmpc-input-bias In Review: `u_ref` steps on accept; NLP warm-start recedes one slow interval; P-only apply without EKF. PR https://github.com/marcuskrogh/HeatingAssistant/pull/635; CalVer 2026.08.31. Jira MCP unavailable. Next `/review-fix`.
- 2026-08-28 — `/define`+`/implement` nmpc-input-bias: PLAN `docs/agents/PLAN-nmpc-input-bias.md` (bug / fix-fast). `u_ref` steps on accept; NLP warm-start recedes one slow interval; P-only apply without EKF. Jira MCP unavailable. Next `/review-fix`.
- 2026-08-22 — `/ship` SWD-432 via PR #634 (`9d815d1`): missing GHI steps use cloud/clear, not `ghi_now`; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.30`. Next Done.
- 2026-08-22 — `/review-fix` SWD-432 CLEAN (focused): 0 blockers / 0 should-fix / 2 deferred notes (live GHI holes; `c_air_fraction` ignored). COMMENT (cannot APPROVE own PR). Fast suite 987 passed, 88 skipped, 18 deselected. Next `/ship SWD-432`.
- 2026-08-22 — `/implement` SWD-432 In Review: GHI `None` uses cloud/clear, not `ghi_now`; PR https://github.com/marcuskrogh/HeatingAssistant/pull/634; CalVer 2026.08.30. Next `/review-fix SWD-432`.
- 2026-08-21 — `/ship` SWD-431 via PR #633 (`eb3fa55`): remaining-`U*` Forecast resim from the current EKF with OCP `step_hold` (not freeze-`T_ref`); matching Tuning preview overlays; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.29`. Next Done.
- 2026-08-21 — `/review-fix` SWD-431 CLEAN (focused): 0 blockers / 0 should-fix / 2 deferred notes (worker apply vs `_control_lock`; empty-preview NLP). `gh` review API 403, posted comment. Merging `main` (SWD-430). Next `/ship SWD-431`.
- 2026-08-21 — `/sandbox`+implement SWD-431: Forecast is remaining-`U*` resim from the current EKF with OCP `step_hold` (not freeze-`T_ref`). vs resim 0 K; vs frozen `T_ref[k:]` 1.30 K. Matching Tuning preview still overlays. Inspect `sandbox/preview-vs-room/inspect/04_*`. CalVer 2026.08.28. PR https://github.com/marcuskrogh/HeatingAssistant/pull/633. Next `/review-fix SWD-431`.
- 2026-08-21 — `/sandbox`+implement SWD-431: room-view Forecast is remaining OCP `T_ref` (implicit-Euler `n_int`), vs OCP 0.000 K. Matching Tuning preview still overlays. Inspect `sandbox/preview-vs-room/inspect/03_*`. CalVer 2026.08.28. PR https://github.com/marcuskrogh/HeatingAssistant/pull/633. Next `/review-fix SWD-431`. (Withdrawn: freeze-`T_ref` ignores updated disturbances.)
- 2026-08-21 — `/ship` SWD-430 via PR #632 (`fa64ee6`): loading animation on NEXT NMPC / NEXT CONTROL while that solver is busy; live KPIs stay applied values; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.28`. Next Done.
- 2026-08-21 — `/review-fix` SWD-430 CLEAN (focused): 0 blockers / 1 should-fix (dual-tree overlay asserts). Fix-forward on PR #632. Fast suite 973 passed, 88 skipped, 18 deselected. Next `/ship SWD-430`.
- 2026-08-21 — `/implement` SWD-430 In Review: countdown loading overlay on NEXT NMPC / NEXT CONTROL; live KPIs stay applied values; PR https://github.com/marcuskrogh/HeatingAssistant/pull/632; CalVer 2026.08.28. Fast suite 973 passed, 88 skipped, 18 deselected. Next `/review-fix SWD-430`.
- 2026-08-21 — `/sandbox` SWD-430 iteration 2: overlay on NEXT CONTROL / NEXT NMPC per flag; live KPIs stay actual values. Inspect `sandbox/kpi-loading/inspect/02_nmpc.png`. Next `/sandbox SWD-430`.
- 2026-08-21 — `/sandbox` SWD-430 iteration 1: shipped overlay is 8% white on `--bg-card` (invisible); candidate teal sweep + accent border + ` · computing`. Inspect `sandbox/kpi-loading/inspect/01_computing.png`. Next `/sandbox SWD-430`.
- 2026-08-21 — `/ship` SWD-426 via PR #631 (`507873f`): shared NMPC/P Start epoch; independent solves (no extra P after NLP); KPI loading; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.27`. Next Done.
- 2026-08-21 — `/review-fix` SWD-426 CLEAN (focused): 0 blockers / 4 should-fix (accept index from plan origin; lock P sync; room forecast stamp; System Status last P ts). Fix-forward on PR #631. Fast suite 970 passed, 88 skipped, 18 deselected. Next `/ship SWD-426`.
- 2026-08-21 — `/implement` SWD-426 In Review: shared NMPC/P Start epoch; independent solves (no extra P after NLP); KPI loading; PR https://github.com/marcuskrogh/HeatingAssistant/pull/631; CalVer 2026.08.27. Fast suite 969 passed, 88 skipped, 18 deselected. Next `/review-fix SWD-426`.
- 2026-08-21 — `/define` SWD-426: PLAN `docs/agents/PLAN-nmpc-p-independent-grid.md` (tweak / delta-fast). Shared Start epoch; independent NMPC and P; no extra P after NLP; KPI loading while computing. Sub-tasks SWD-427–429. Next `/implement SWD-426`.
- 2026-08-21 — `/ship` SWD-417 via PR #630 (`eb28be6`): remaining-`U*` resim on room-view Forecast (not freeze-`t_ref`); Planned Power keeps leftover 2 h outdoor ZOH; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.26`. Next Done.
- 2026-08-21 — `/review-fix` SWD-417 CLEAN (focused): 0 blockers remaining / 3 should-fix (disabled-source idle-U overlay; `_nmpc_k` vs apply identity; SWD-421 tests still expected frozen `T_ref`). Fix-forward on PR #630 (`e461dd2`). Merged `main` (SWD-418/421). CalVer 2026.08.26. Fast suite 962 passed, 88 skipped, 18 deselected. Next `/ship SWD-417`.
- 2026-08-21 — `/implement` SWD-417 In Review: remaining-`U*` resim on room-view Forecast (not freeze-`t_ref`); PR https://github.com/marcuskrogh/HeatingAssistant/pull/630; CalVer 2026.08.25. Fast suite 950 passed, 88 skipped, 18 deselected. Next `/review-fix SWD-417`.
- 2026-08-21 — `/sandbox` SWD-417 iteration 4: room-view live cache vs Tuning preview re-solve; max |T| 1.88 K after 8 ticks; different U*. Next `/sandbox SWD-417`.
- 2026-08-20 — `/sandbox` SWD-417 iteration 3: freeze production `U*`, re-roll T at n_int 1/10/40/100; n_int=10 vs 100 is 23 mK; 15 min wiggles remain on the high-fidelity path. Next `/sandbox SWD-417`.
- 2026-08-20 — `/sandbox` SWD-417 iteration 2: peaked price × s_rom 0.05/0.1/1/5; traces overlay; ROM does not move Forecast jitter. Next `/sandbox SWD-417`.
- 2026-08-20 — `/sandbox` SWD-417 from SWD-414: Forecast T jitter vs `n_int_steps`; Relates SWD-414; inspect `sandbox/forecast-jitter/inspect/01_*`. n_int=40 smoother; live 2.5 K swing not reproduced. Next `/sandbox SWD-417`.
- 2026-08-20 — `/ship` SWD-418 via PR #628 (`554e9fc`): two-hour planner countdown stays on the Start epoch; NLP finish does not restamp; plot/ID ticks stay off that grid. Review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.25`. Next Done.
- 2026-08-20 — `/review-fix` SWD-418: 1 blocker / 1 should-fix (history ticker aligned to NMPC epoch; SWD-318 test stamped `_last_control_ts`). Fix-forward on PR #628 (`803e982`): plot/ID ticks stay `now + interval`; control stays on the Start grid. Local pytest-fast 951 passed, 88 skipped, 18 deselected. Next `/ship SWD-418`.
- 2026-08-20 — `/implement` SWD-418 In Review: Start epoch is `last_nmpc_ts` / `last_run_ts`; NMPC finish does not restamp; PR https://github.com/marcuskrogh/HeatingAssistant/pull/628; CalVer 2026.08.25. Fast suite 951 passed, 88 skipped, 18 deselected. Next `/review-fix SWD-418`.
- 2026-08-20 — `/define` SWD-418: PLAN `docs/agents/PLAN-nmpc-timer-drift.md` (bug / fix-fast). `last_nmpc_ts` is the Start epoch; NMPC finish must not restamp it. Sub-tasks SWD-419–420. Next `/implement SWD-418`.
- 2026-08-20 — `/ship` SWD-421 via PR #629 (`c0a2c13`): room view Forecast / Planned Power stay on the two-hour planner path after 15-minute ticks; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.25`. Next Done.
- 2026-08-20 — `/iterate` SWD-421 In Review: keep room-view Forecast / Planned Power on the installed 2-hour `U*` hold and `T_ref` after 15-minute ticks; PR https://github.com/marcuskrogh/HeatingAssistant/pull/629; CalVer 2026.08.25. Fast suite 948 passed, 88 skipped, 18 deselected. Next `/review-fix SWD-421`.
- 2026-08-20 — `/iterate` SWD-421 from SWD-414: room view still plots 15-minute power steps and a jittery Forecast after each fast `compute()`; keep Planned Power / Forecast on the installed 2-hour `U*` hold and `T_ref`. Relates SWD-414; Sub-tasks SWD-422–423. Next `/review-fix SWD-421`.
- 2026-08-20 — `/ship` SWD-414 via PR #627 (`434027a`): room view plots the two-hour planner path when it beats leaving the heater off; accept at ≥0.1% better than J(u=0); review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.24`. Next Done.
- 2026-08-20 — `/review-fix` SWD-414: 0 blockers / 1 should-fix (accept threshold used strict `<` at the 0.1% bar). Fix-forward on PR #627.
- 2026-08-20 — `/iterate` SWD-414 In Review: accept NMPC when J is 0.1% better than J(u=0); room view refetches on `last_nmpc_ts`; PR https://github.com/marcuskrogh/HeatingAssistant/pull/627; CalVer 2026.08.24. Fast suite 945 passed, 88 skipped, 18 deselected. Next `/review-fix SWD-414`.
- 2026-08-20 — `/iterate` SWD-414 from SWD-411: room-view optimal trajectory still U=0 / 30°C free response; accept plans that beat J(u=0); Relates SWD-411; Sub-tasks SWD-415–416. Next `/implement SWD-414`.
- 2026-08-20 — `/ship` SWD-408 via PR #625 (`acbd43b`): proper Heating Assistant icon for App store, Ingress, and HA 2026.3+ `brand/`; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.23`. Next Done.
- 2026-08-20 — `/review-fix` SWD-408 CLEAN (focused): 1 blocker / 6 should-fix (CalVer behind main; brands README; font fallbacks; favicon size + cache-bust; Python `copytree brand/`; PLAN logo 500×200). Fix-forward on PR #625. Next `/ship SWD-408`.

- 2026-08-20 — `/implement` SWD-408 In Review: brand SVG/PNGs, Supervisor icon/logo, HA `brand/` folder, Ingress favicon; CalVer 2026.08.21. Fast suite 928 passed, 88 skipped. PR https://github.com/marcuskrogh/HeatingAssistant/pull/625. Next `/review-fix SWD-408`.
- 2026-08-20 — `/define` SWD-408: PLAN `docs/agents/PLAN-heating-assistant-icon.md` (tweak / delta-fast). Shared house + settling-curve mark; Supervisor icon/logo; HA 2026.3+ `brand/` folder; brands lift copy. Sub-tasks SWD-409–410. Next `/implement SWD-408`.
- 2026-08-20 — `/ship` SWD-411 via PR #626 (`ae030d9`): heat/cool on the 15 min loop when already out of band; post-accept publish works off ticker/start ephemeral loops; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.22`. Next Done.
- 2026-08-20 — `/review-fix` SWD-411: 1 blocker / 3 should-fix (ephemeral `_nmpc_loop` skipped post-accept publish; lock wait; tests; changelog). Fix-forward on PR #626.
- 2026-08-20 — `/iterate` SWD-411 In Review: fast P comfort fallback + publish actuators on NMPC apply; PR https://github.com/marcuskrogh/HeatingAssistant/pull/626; CalVer 2026.08.22. Next `/review-fix SWD-411`.
- 2026-08-20 — `/iterate` SWD-411 from SWD-405: live heat/cool idle at `u = 0` while comfort bounds are already violated; fast P fallback + publish actuators on NMPC apply; Relates SWD-405; Sub-tasks SWD-412–413. Next `/implement SWD-411`.
- 2026-08-20 — `/iterate` SWD-405 from SWD-400: planned cooling still 0 kW on the room plot; refresh forecast on apply + retry idle zero plans; Relates SWD-400; Sub-tasks SWD-406–407. Next `/review-fix SWD-405`.
- 2026-08-20 — `/define` SWD-395: PLAN `docs/agents/PLAN-nmpc-p-ff.md` (feature / feature-standard). Timing triple 2 h / 8 fast substeps / 36 h (`T_s` derived); `K_p` 0.1 /K; robust accept/reject; worker + 5 h watchdog. Sub-tasks SWD-396–399. Next `/implement SWD-395`.
- 2026-08-19 — `/sandbox` SWD-394 **accepted** (2 h, analytic Jacobian, worker thread). Supportive Task Done. Next `/define SWD-395`.
- 2026-08-19 — `/sandbox` SWD-394 iteration 3: analytic `dJ/dU` via production `dfdx`/`dfdu` (rel error 1.3e-5 vs FD). 2 h cold **22 s** / 80 iters (J=0.81, hit cap); warm **7.7 s** / 26 iters success. Inspect `sandbox/nmpc-p-ff/inspect/03_report.md`. Next `/sandbox SWD-394`.
- 2026-08-19 — `/sandbox` SWD-394 iteration 2: operator locked **2 h**. Cold SLSQP 94 s / 47 iters (success, cap 80). Warm polish hit maxiter. NMPC must use a worker thread (today’s `compute_actions` is inline on the App asyncio loop). Inspect `sandbox/nmpc-p-ff/inspect/02_report.md`. Next `/sandbox SWD-394`.
- 2026-08-19 — `/sandbox` SWD-394 iteration 1: approximate SLSQP solve times on synthetic two-room heat-pump house (live traces waived). QP ~0.7 s; 15 min NMPC ~112 s; 1 h ~77 s (maxiter); 2 h ~24 s. Inspect `sandbox/nmpc-p-ff/inspect/01_report.md`. Next `/sandbox SWD-394`.
- 2026-08-19 — `/sandbox` SWD-394 representativeness: measure bar is solve-time (p95/timeouts/warm-start) + closed-loop vs `HeatingLinearisedMPC`; live household traces named as a gap; inspect-loop not started. Artifact `docs/agents/SANDBOX-nmpc-p-ff.md`. Next `/sandbox SWD-394`.
- 2026-08-19 — `/model` SWD-393 Done: hierarchical mean OCP + P-FF; artifact `docs/agents/MODEL-nmpc-p-ff.md` on `cursor/swd-395-nmpc-p-tracker-46be` (no PR). Next `/sandbox SWD-394`.
- 2026-08-19 — `/explore` SWD-392: hierarchical nonlinear OCP + P tracking; route SWD-393 model → SWD-394 sandbox → SWD-395 define; last-plan hold; 5 h fail → u = 0 + persistent notification. Next `/model SWD-393`.
- 2026-08-19 — shipped SWD-389 via PR #621: PE duration guidance (one day covers categories; several days for a good model); review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.10`. Next Done.
- 2026-08-19 — `/review-fix` SWD-389 CLEAN (focused): 0 blockers / 0 should-fix; APPROVE intent on PR #621 (`gh` review API 403, posted comment). Fast suite 885 passed, 88 skipped. Next `/ship SWD-389`.
- 2026-08-18 — shipped SWD-385 via PR #620: System Status clears stale BAD tag quality when HA sensors already measure; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.9`. Next Done.
- 2026-08-18 — `/review-fix` SWD-385 CLEAN (focused): 0 blockers / 3 should-fix (retain/null-ts; HA-running republish; shared catalog/inbound snapshot timestamp). Focused tests 10 passed. Fast suite 885 passed, 88 skipped. Next `/ship SWD-385`.
- 2026-08-18 — `/implement` SWD-385 In Review: catalog overlay + ignore stale retained BAD + HA-started republish; PR https://github.com/marcuskrogh/HeatingAssistant/pull/620; CalVer 2026.08.9. Fast suite 881 passed, 88 skipped. Next `/review-fix SWD-385`.
- 2026-08-18 — `/define` SWD-385: System Status BAD tag quality despite fine HA measurements; catalog overlay + ignore stale retained BAD + HA-started republish; PLAN `docs/agents/PLAN-tag-quality-stale-bad.md`; Sub-tasks SWD-386–388; bug/fix-fast. Next `/implement SWD-385`.
- 2026-08-16 — shipped SWD-356 via PR #619: Restart required as Settings repair (HACS path), tombstone leftover MQTT Update card; review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.8`. Next Done.
- 2026-08-16 — `/review-fix` SWD-356 CLEAN (focused): 0 blockers / 2 should-fix (split fused repair tests + default delete path; document 2026.08.8 one-hop). Fast suite 875 passed, 88 skipped. Next `/ship SWD-356`.
- 2026-08-16 — `/implement` SWD-356 In Review: Restart required is a Settings **repair** (HACS path), not an MQTT Update card; tombstone leftover discovery; PR https://github.com/marcuskrogh/HeatingAssistant/pull/619; CalVer 2026.08.8. Fast suite 873 passed, 88 skipped. Next `/review-fix SWD-356`.
- 2026-08-16 — `/iterate` SWD-356 from SWD-352: Restart required as Settings repair (HACS path), not MQTT Update card; Relates SWD-352; Sub-tasks SWD-357–358. Next `/implement SWD-356`.
- 2026-08-16 — shipped SWD-352 via PR #618: App changelog on the update dialog + Restart required on Settings (no persistent notification); review-fix CLEAN (focused); changelog `heating_assistant/CHANGELOG.md` `# 2026.08.7`. Next Done.
- 2026-08-16 — `/review-fix` SWD-352 CLEAN (focused): 1 blocker / 6 should-fix addressed (stamp capture before dest replace; clear Restart required after Core restart). Deferred notes: native `update.py` stays unregistered; dual-tree CI. Fast suite 871 passed, 88 skipped. Next `/ship SWD-352`.
- 2026-08-16 — `/implement` SWD-352 In Review: App CHANGELOG.md + MQTT Restart required on Settings; no persistent notification / auto Core restart; PR https://github.com/marcuskrogh/HeatingAssistant/pull/618; CalVer 2026.08.7. Next `/review-fix SWD-352`.
- 2026-08-16 — `/define` SWD-352: App changelog on Supervisor update dialog + MQTT Restart required on Settings (no persistent notification, no auto Core restart); download % out (local Docker build); PLAN `docs/agents/PLAN-app-update-path.md`; Sub-tasks SWD-353–355; tweak/delta-fast. Next `/implement SWD-352`.
- 2026-08-16 — shipped SWD-349 via PR #617: PE \(C\) factor-5 upper box around selected room size + adaptive MAP toward that prior; review-fix CLEAN (focused; `gh` review API 403, posted comment); changelog skipped (none). Next Done.
- 2026-08-16 — `/review-fix` SWD-349 CLEAN (focused): 0 blockers / 0 should-fix / 5 non-actionable notes. Next `/ship SWD-349`.
- 2026-08-16 — `/implement` SWD-349 In Review: PE \(C\) factor-5 upper box around selected room size + MAP toward that prior; PR https://github.com/marcuskrogh/HeatingAssistant/pull/617; CalVer 2026.08.6. Next `/review-fix SWD-349`.
- 2026-08-16 — `/define` SWD-349: PE \(C\) relative box ×5 around selected room size + MAP toward that prior; PLAN `docs/agents/PLAN-pe-mass-prior.md`; Sub-tasks SWD-350–351; tweak/delta-fast; Relates SWD-335, SWD-344. Next `/implement SWD-349`.
- 2026-08-16 — shipped SWD-344 via PR #616: PE aux charts from ID history, optimal Tw0 on Simulate, shorter category guides; review-fix CLEAN (focused); changelog skipped (none). Next Done.
- 2026-08-15 — `/implement` SWD-344 In Review: PE aux charts from ID history, optimal Tw0 on Simulate, shorter category guides; PR https://github.com/marcuskrogh/HeatingAssistant/pull/616; CalVer 2026.08.5. Next `/review-fix SWD-344`.
- 2026-08-15 — `/define` SWD-344: PE aux charts from ID history, optimal Tw0 on Simulate, shorter category guides; PLAN `docs/agents/PLAN-pe-sim-aux-tw0.md`; Sub-tasks SWD-345–348; bug/fix-fast; Relates SWD-343. Next `/implement SWD-344`.
- 2026-08-15 — shipped SWD-343 via PR #615: clickable PE category guides, live save-window coverage preview, four-up dataset chips; review-fix CLEAN (focused); changelog skipped (none).
- 2026-08-15 — SWD-343 copy: Heater guide states the dataset must include this room's heater command off and on.
- 2026-08-15 — SWD-343 copy: Heater guide states heater power scale (off vs on duty) vs rated power.
- 2026-08-15 — `/iterate` SWD-343 In Review: clickable PE category guides + live save preview; PR https://github.com/marcuskrogh/HeatingAssistant/pull/615; CalVer 2026.08.4. Next `/review-fix SWD-343`.
- 2026-08-15 — `/iterate` SWD-343 from SWD-335: clickable PE category guides + live save-window coverage preview; comfort-first recipes; Relates SWD-335. Next `/review-fix SWD-343`.
- 2026-08-15 — shipped SWD-329 via PR #612 into `main`: offline household PE robustness harness + report; review-fix CLEAN; changelog skipped (none). Stack also lands SWD-332 and SWD-335. Story SWD-323 Done — map complete.
- 2026-08-15 — shipped SWD-332 via PR #613: hold-out open-loop val RMSE/MAE/R² on the household PE harness; review-fix CLEAN (PLAN seam init); changelog skipped (test/report only). Story SWD-323 stays open. Next `/review-fix SWD-329`.
- 2026-08-15 — shipped SWD-335 via PR #614: identified contact-gated UA_open + grey/teal PE category tiles; review-fix CLEAN; changelog skipped (none). Story SWD-323 stays open. Next `/review-fix SWD-332`.
- 2026-08-14 — `/review-fix` SWD-335 CLEAN (focused): 0 blockers / 3 should-fix addressed (Apply persists UA_open, pe_coverage 400, on-demand val bar). Next `/ship SWD-335`.
- 2026-08-14 — `/implement` SWD-335 UX: recommended-data category tiles (grey Not set to teal Supplied); not native checkboxes. Cache-bust 123. Next `/review-fix SWD-335`.
- 2026-08-14 — `/implement` SWD-335 UX: compact category checkboxes driven by Use on stored datasets; dataset summary chips; Run recommended estimation when all required boxes are checked. Next `/review-fix SWD-335`.
- 2026-08-14 — `/implement` SWD-335: identified contact-gated UA_open in production open-loop PE; PE recommended-data checklist + `/api/pe_coverage`; CalVer 2026.08.3; PR #614. Next `/review-fix SWD-335`.
- 2026-08-14 — `/define` SWD-335: product winner = identified contact-gated UA + 24 h q_int (not day-gated occupancy). PE page read-only data-coverage checklist. PLAN `docs/agents/PLAN-pe-robust-open-loop.md`; Sub-tasks SWD-336–339; feature-standard. Next `/implement SWD-335`.
- 2026-08-14 — SWD-323: 1R1C vs 2R2C estimator on the household 2R2C plant; 1R1C degenerate mean val RMSE 4.17 °C vs 2R2C 1.53 °C (clean rooms 3.46 vs 0.45). Keep 2R2C. Next `/define SWD-335`.
- 2026-08-14 — `/model` SWD-334: identify contact-gated \(UA_{\mathrm{open}}\) + day-gated \(q_{\mathrm{day}}\) (MAP prior toward 0, no \(C,R\) lock, keep OE). Probe: id-UA mean val 0.64 °C vs assumed-UA 0.83 °C; unregularized day-\(q\) grid overfits weak occ. Artifact `docs/agents/MODEL-pe-contact-ua-occupancy.md` on `cursor/swd-335-robust-ol-pe-747e`. SWD-334 Done. Next `/define SWD-335`.
- 2026-08-14 — `/explore` SWD-323 rechart: robust/reliable open-loop PE; identify contact-gated UA + occupancy disturbance (no C,R lock); keep OE; SWD-334 then SWD-335 (blocked by 334). Val bar: beat `window_ua`+OL 0.83 °C, no closed-window regression. Next `/model SWD-334`.
- 2026-08-14 — `/implement` SWD-332: train/val open-loop RMSE/MAE/R² on SWD-329 harness; 11 helper tests + on-demand 108-fit grid (~62 s); report updated; PR #613. Next `/review-fix SWD-332`.
- 2026-08-14 — `/define` SWD-332: hold-out open-loop val RMSE/MAE/R² on SWD-329 harness (MPC-relevant); θ error secondary; PLAN `docs/agents/PLAN-pe-validation-accuracy.md`; Sub-task SWD-333; delta-fast. Next `/implement SWD-332`.
- 2026-08-13 — `/implement` SWD-329: on-demand household PE factorial + helper tests; report `docs/agents/REPORT-pe-robustness-household.md`; PR #612; In Review. Next `/review-fix SWD-329`.
- 2026-08-13 — `/define` SWD-329: offline PE robustness factorial (occupancy × openings; six procedures × open-loop/Kalman); occupancy + extra UA harness-only; PLAN `docs/agents/PLAN-pe-robustness-household.md`; Sub-task SWD-330; feature-standard. Next `/implement SWD-329`.
- 2026-08-13 — `/research` SWD-328: household-like 2R2C extras (occupancy ≠ window); finding docs on `cursor/swd-329-pe-robustness-747e`; SWD-328 Done. Next `/define SWD-329`.
- 2026-08-13 — `/explore` SWD-323 rechart: do not pick a product PE procedure; synthesise household-like single-room data (window/door, occupancy); research SWD-328 then analysis SWD-329. Next `/research SWD-328`.
- 2026-08-13 — shipped SWD-326 via PR #611: offline combined vs separated/staged PE bake-off; review-fix CLEAN; changelog skipped (none). Story SWD-323 stays open.
- 2026-08-13 — `/define`+implement SWD-326: offline combined vs separated/staged PE bake-off; report `docs/agents/REPORT-pe-dataset-separation.md`; Sub-task SWD-327. Next `/review-fix SWD-326`.
- 2026-08-13 — `/model` SWD-325: `docs/agents/MODEL-pe-hidden-tw.md` on `cursor/swd-326-pe-effectiveness-747e`; \(T_w(t_0)\) PE decision, 24 h box \(\pm 25\%\) width; SWD-325 Done. Next `/define SWD-326`.
- 2026-08-13 — `/explore` SWD-323 rechart: SWD-325 model (hidden \(T_w\) / staged windows) blocks SWD-326 define (PE effectiveness + guidance, one delivery unit). Next `/model SWD-325`.
- 2026-08-13 — `/research` SWD-324: PE effectiveness brief `docs/agents/RESEARCH-pe-effectiveness.md`; joint \(T_w\) unused leading-window; literature on hidden state, regimes, excitation, guidance. Next `/explore SWD-323`.
- 2026-08-13 — `/explore` SWD-323: PE effectiveness + user guidance; route SWD-324 research (current state, why poor, what to apply); 2R2C + cheap indoor sensors only. Next `/research SWD-324`.
- 2026-08-13 — shipped SWD-322 via PR #610 (`ee41ee6`): PE excludes override-active room samples; review-fix CLEAN; changelog skipped (none).
- 2026-08-13 — `/review-fix` SWD-322 CLEAN (focused): 0 blockers / 0 should-fix; APPROVE intent on PR #610 (`gh` review API 403); Next ship closeout.
- 2026-08-13 — `/implement` SWD-322: revived PE window-exclusion regressions + App ID flag coverage; CONFIGURATION note; 18 passed; PR #610; In Review; Next `/review-fix SWD-322`.
- 2026-08-13 — `/define` SWD-322: exclude override-active room samples from offline Parameter Estimation (per-room only; chart gaps only); PLAN `docs/agents/PLAN-pe-exclude-window-open.md`; tweak/delta-fast; Relates SWD-298; branch `cursor/swd-322-pe-exclude-window-open-f7b1`; Next `/implement SWD-322` (await PLAN approval).
- 2026-08-12 — shipped SWD-321 via PR #607: DISTURBANCES outdoor/solar history Measured-style points; formal review-fix CLEAN (COMMENT reviews); Task Done.
- 2026-08-12 — `/ship` SWD-321 closeout follow-up: CalVer bump **2026.08.1 → 2026.08.2** via https://github.com/marcuskrogh/HeatingAssistant/pull/609 (missing from #607 merge; mirror #606 file set); no CHANGELOG.md in repo.
- 2026-08-12 — `/review-fix` SWD-321 CLEAN (focused): 0 blockers / 0 should-fix / 0 notes; APPROVE on PR #607; Next `/ship SWD-321`.
- 2026-08-12 — `/implement` SWD-321: DISTURBANCES outdoor/solar history → Measured-style points; tests `tests/test_swd321_disturbance_history_points.py` (2 passed); PR https://github.com/marcuskrogh/HeatingAssistant/pull/607; In Review; Next `/review-fix SWD-321`.
- 2026-08-12 — shipped SWD-317 via PR #605: ID history System Status card (warning=duration, error=3 append failures); review-fix CLEAN; Story SWD-316 Done (map complete). Rebuild HAOS App for System Status card.
- 2026-08-12 — `/define` SWD-317: ID history System Status card only (not overall health); warning=2× interval duration, error=3 consecutive append failures; PLAN `docs/agents/PLAN-id-history-status-card.md`; tweak/delta-fast; branch `cursor/swd-317-id-history-status-card-2dd4`; Next `/implement SWD-317` (await PLAN approval).
- 2026-08-11 — shipped SWD-318 via PR #603 (`58d71a7`): ID samples on ticker + `update_tag` + durable-first; review-fix CLEAN. Rebuild HAOS App to pick up writers. Next `/define SWD-317`.
- 2026-08-11 — `/implement` SWD-318: ID samples on ticker + `update_tag` + durable-first; tests `tests/test_swd318_id_sample_plot_cadence.py`; PR #603; In Review; Next `/review-fix SWD-318`.
- 2026-08-11 — `/define` SWD-318: align ID sample write with plot cadence (Option B + durable-first); PLAN `docs/agents/PLAN-id-sample-plot-cadence.md`; bug/fix-fast; branch `cursor/swd-318-id-sample-plot-cadence-2dd4`; Next `/implement SWD-318`.
- 2026-08-11 — shipped SWD-320 via PR #602: resolve_history(horizon) merges id_history JSONL (Option A); review-fix CLEAN; Next `/define SWD-318`.
- 2026-08-11 — `/define` SWD-320: resolve_history(horizon) merges id_history JSONL (Option A); PLAN `docs/agents/PLAN-resolve-history-horizon-jsonl.md`; bug/fix-fast; branch `cursor/swd-320-resolve-history-horizon-jsonl-2dd4`; Next `/implement SWD-320`.
- 2026-08-11 — `/research` SWD-319: load-path defect + write asymmetry code-proven; artifact `docs/agents/RESEARCH-estimation-history-hole.md`; Next `/define SWD-320`.
- 2026-08-11 — `/explore` SWD-316: estimation history hole while plots + control OK; route SWD-319/320/318/317.
- 2026-08-11 — shipped SWD-315 via PR #599 (`0397afa`): SciPy-only parameter estimation (remove IPOPT/cyipopt path); review-fix CLEAN.
- 2026-08-11 — SWD-315 review-fix CLEAN (focused): SciPy-only estimation; deferred MPC legacy IPOPT labels in slow perf harness; Next `/ship SWD-315`.
- 2026-08-11 — `/implement` SWD-315: SciPy-only estimation; PR https://github.com/marcuskrogh/HeatingAssistant/pull/599; Next `/review-fix SWD-315`.
- 2026-08-11 — `/define` SWD-315: remove IPOPT from parameter estimation (SciPy L-BFGS-B only); PLAN `docs/agents/PLAN-remove-ipopt-scipy.md`; branch `cursor/swd-315-remove-ipopt-scipy-a072`; delta-fast; Next `/implement SWD-315`.
- 2026-08-11 — shipped SWD-311 via PR #598 (`ebb8aae`): App-first README + docs cleanup; HACS removed; review-fix CLEAN.

- 2026-08-11 — SWD-311 review-fix CLEAN (focused): THEORY App paths, App README `../docs/` links, ISSUES archived artifacts; Next `/ship SWD-311`.
- 2026-08-11 — `/ship` SWD-311: App-first README + docs cleanup; remove HACS; purge leftover roadmaps; branch `cursor/swd-311-app-first-docs-75fa`.
- 2026-08-11 — shipped SWD-307 via PR #597 (`022c9e8`): calendar versioning `YYYY.MM.PATCH`; v2026.08.0; CI green. Rebuild App on HAOS to pick up the new version stamp.
- 2026-08-11 — SWD-307 review-fix CLEAN on PR #597; calendar versioning `YYYY.MM.PATCH` / `2026.08.0`; shipping closeout.
- 2026-08-11 — `/define`+`/ship` SWD-307: calendar versioning `YYYY.MM.PATCH`; PLAN `docs/agents/PLAN-calver-versioning.md`; Sub-tasks SWD-308/310/309; branch `cursor/swd-307-calver-versioning-d25e`; delta-fast; cutover to `2026.08.0`.
- 2026-08-10 — shipped SWD-300 via PR #595: System Status page + health indicator + Parameter Estimation rename; v2.0.32; CI green. Rebuild App on HAOS so System Status / health indicator / Overview split appear.
- 2026-08-10 — `/define`+`/ship` SWD-300: System Status page + health indicator + Parameter Estimation rename; PLAN `docs/agents/PLAN-system-status.md`; Sub-tasks SWD-304/302/303/301/305; branch `cursor/swd-300-system-status-c2e7`; feature-heavy; Next `/implement SWD-300`.
- 2026-08-10 — shipped marcuskrogh/skills Cloud install via PR #594: committed `.agents/skills/`, prefer-workflow pointers, install+start sync + home mirrors; review-fix CLEAN. Start a new Cloud Agent on main to pick up skills; if `<agent_skills>` still empty, enable skills as a Required marketplace plugin.
- 2026-08-10 — shipped SWD-299 via PR #593: publish identification fit KPIs (`*_model_fit_quality` / `*_parameter_confidence`); v2.0.31; review-fix CLEAN. Rebuild App on HAOS so Overview MODEL FIT and System Identification R²/RMSE/Estimated populate.
- 2026-08-10 — SWD-299 review-fix CLEAN on PR #593; identification fit KPIs; v2.0.31; shipping closeout.
- 2026-08-10 — `/ship` SWD-299 implement: publish `*_model_fit_quality` + `*_parameter_confidence` in App hass_states; port fit helpers; v2.0.31; PR #593; Next `/review-fix SWD-299`.
- 2026-08-10 — `/define` SWD-299: identification KPIs (model fit / R² / RMSE / Estimated) not populating Overview or System Identification index — App never publishes `*_model_fit_quality` / `*_parameter_confidence` after SWD-262; PLAN `docs/agents/PLAN-identification-kpis.md`; branch `cursor/swd-299-identification-kpis-3a87`; Next `/implement SWD-299`.
- 2026-08-10 — shipped SWD-298 via PR #592: App door/window heater override after debounce; v2.0.30; review-fix CLEAN. Rebuild App on HAOS so open sensors shut heaters after `window_open_debounce`.
- 2026-08-10 — `/ship` SWD-298 implement: App window override + timers + disabled_sources/Q inflation; v2.0.30; PR #592; Next `/review-fix SWD-298`.
- 2026-08-10 — `/define` SWD-298: door/window sensors do not turn off heaters after debounce — App missing window override deleted in SWD-262; PLAN `docs/agents/PLAN-window-heater-override.md`; branch `cursor/swd-298-window-heater-override-1125`; Next `/implement SWD-298`.
- 2026-08-10 — shipped SWD-297 via PR #590 (`6288e22`): publish applied solar_gain_measured; v2.0.29; review-fix CLEAN. Rebuild App on HAOS so DISTURBANCES Solar Gain left of NOW tracks daytime dynamics.
- 2026-08-10 — `/ship` SWD-297: implement measured solar_gain_measured from applied solar forecast; v2.0.29; PR #590; Next `/review-fix SWD-297`.
- 2026-08-10 — `/define` SWD-297: applied/measured solar gain stuck at 0 while forecast is correct — `hass_states` hardcodes `solar_gain_measured`; BUG `docs/agents/BUG.md`; branch `cursor/swd-297-measured-solar-gain-zero-f475`; Next `/implement SWD-297`.
- 2026-08-10 — shipped SWD-296 via PR #588 (`6ccce63`): restore sysid Apply params + panel route; v2.0.28; review-fix CLEAN. Rebuild App on HAOS so Apply Parameters stick and the panel keeps the current page across remounts.
- 2026-08-10 — SWD-296 review-fix CLEAN on PR #588; restore sysid Apply params + panel route; v2.0.28; CI green. Next: merge + rebuild HAOS.
- 2026-08-10 — SWD-296 In Review + PR #588: restore sysid Apply params + panel route; v2.0.28; Next `/review-fix SWD-296`.
- 2026-08-10 — `/bug`+implement SWD-296: sysid Apply Parameters not restored + panel overview remount; restore `estimated_params` on engine rebuild, publish thermal attrs on `temperature_filtered`, sessionStorage route restore; v2.0.28; branch `cursor/swd-296-sysid-params-overview-5009`.
- 2026-08-10 — shipped SWD-289 via PR #586 (`197c307`): restore system identification (panel imports + App sysid ownership); v2.0.27; review-fix CLEAN. Rebuild App on HAOS so EKF / open-loop / automatic identification work.
- 2026-08-10 — SWD-289 review-fix CLEAN on PR #586; panel imports + App sysid ownership; v2.0.27; shipping closeout.
- 2026-08-10 — `/ship` SWD-289: implement → review-fix → closeout for system identification restore.
- 2026-08-10 — `/define` SWD-289: restore system identification page (panel import gaps + end App sysid no-ops deferred from SWD-281); PLAN `docs/agents/PLAN-sysid-services.md`; Sub-tasks SWD-293/292/294/290/295/291; branch `cursor/swd-289-sysid-services-851a`; Next `/implement SWD-289`.
- 2026-08-10 — shipped SWD-288 via PR #584 (`a084c5a`): climate setpoint persistence; v2.0.26; review-fix CLEAN. Rebuild App on HAOS so TARGET / COMFORT BAND stick on Overview and room view.
- 2026-08-10 — SWD-288 review-fix CLEAN on PR #584; shipping closeout (v2.0.26).
- 2026-08-10 — SWD-288 In Review + PR #584: climate setpoint persistence; v2.0.26; Next `/review-fix SWD-288`.
- 2026-08-10 — `/bug`+implement SWD-288: climate card setpoints reset — App climate services were no-ops; wire set_temperature/turn_on/off + panel set_room_setpoint; v2.0.26; branch `cursor/swd-288-climate-setpoint-reset-d3ac`.
- 2026-08-09 — shipped SWD-286/SWD-287 via PR #582 (`bc6b090`): schedule comfort constraints + expand collapse; v2.0.25; rebuild App on HAOS so Night Mode ±3 shows on room plot and expanded schedules stay open.
- 2026-08-09 — SWD-286/SWD-287 In Review + PR #582: schedule comfort constraints + expand collapse; v2.0.25; Next `/review-fix`.
- 2026-08-09 — `/bug`+implement SWD-286/SWD-287: schedule comfort_offset on plot/controller + schedules expand collapse; v2.0.25; branch `cursor/swd-286-schedule-comfort-constraints-7e7d`.
- 2026-08-09 — shipped SWD-285 via PR #580 (`093e547`): Controller Tuning preview uses unapplied draft params; v2.0.24; review-fix CLEAN. Rebuild App on HAOS so Preview reflects draft weights before Apply.
- 2026-08-09 — SWD-285 review-fix CLEAN on PR #580; shipping closeout (v2.0.24).
- 2026-08-09 — `/bug`+implement SWD-285: Controller Tuning preview ignores unapplied params — restore App `preview_tuning_forecast`; v2.0.24; branch `cursor/swd-285-tuning-preview-unapplied-ebd3`.
- 2026-08-09 — shipped SWD-284 via PR #578 (`afcbea7`): publish electricity_price + day-ahead history backfill; v2.0.23; review-fix CLEAN. Rebuild App on HAOS so room Price shows left of NOW.
- 2026-08-09 — SWD-284 review-fix CLEAN on PR #578; shipping closeout (v2.0.23).
- 2026-08-09 — `/ship` SWD-284: room view Price plot missing historical data — App never publishes electricity_price; v2.0.23; branch `cursor/swd-284-price-history-a08d`.
- 2026-08-09 — SWD-284 In Review + PR #578: publish electricity_price + day-ahead history backfill; v2.0.23; Next `/review-fix SWD-284`.
- 2026-08-09 — `/bug` SWD-284: room view Price plot missing historical data — App never publishes `electricity_price` synthetic; synthesize from day-ahead attrs; v2.0.23; branch `cursor/swd-284-price-history-a08d`.
- 2026-08-09 — shipped SWD-283 via PR #576 (`f117f93`): Save Current Window mobile flex gap; v2.0.22; review-fix CLEAN. Rebuild App on HAOS so identification save fields stack tightly on phone.
- 2026-08-09 — SWD-283 review-fix CLEAN on PR #576; shipping closeout (v2.0.22).
- 2026-08-09 — `/ship` SWD-283: large whitespace between Save Current Window inputs on mobile — `flex: 1 1 220px` became height under column layout; v2.0.22; branch `cursor/fix-sysid-save-row-mobile-gap-6a4c`.
- 2026-08-09 — shipped SWD-282 via PR #574 (`38846fb`): Option A solar exposure aperture wired in App room build; v2.0.21; review-fix CLEAN. Rebuild App on HAOS so DISTURBANCES Solar Gain shows dynamics.
- 2026-08-09 — SWD-282 review-fix CLEAN on PR #574; shipping closeout (v2.0.21).
- 2026-08-09 — `/ship` SWD-282: solar gain flat zero despite High exposure — `_build_house_model` dropped aperture; v2.0.21; branch `cursor/swd-282-solar-exposure-aperture-3296`.
- 2026-08-09 — shipped SWD-281 via PR #572 (`11325cc`): persist plot/ID history under `/data`; v2.0.20; review-fix CLEAN. Rebuild App on HAOS so room plots survive updates.
- 2026-08-09 — SWD-281 review-fix CLEAN on PR #572; shipping closeout (v2.0.20).
- 2026-08-09 — SWD-281 In Review + PR #572: persist plot/ID history under `/data`; v2.0.20; Next `/review-fix SWD-281`.
- 2026-08-09 — `/iterate` SWD-281 from SWD-279 deferred scope: App update clears room-plot / ID history — persist under `/data`; v2.0.20; branch `cursor/swd-281-history-persistence-32e0`.
- 2026-08-09 — shipped SWD-280 via PR #570 (`7106752`): climate HP actuation + thermal measured power; v2.0.19; review-fix CLEAN. Rebuild App on HAOS so planned cooling reaches the climate entity.
- 2026-08-09 — SWD-280 review-fix CLEAN on PR #570; shipping closeout (v2.0.19).
- 2026-08-09 — SWD-280 In Review + PR #570: climate HP actuation + thermal measured power; v2.0.19; Next `/review-fix SWD-280`.
- 2026-08-09 — `/iterate` SWD-280: climate HP actuation missing after thin bridge — planned −3.5 kW cooling never reaches HA entity; measured −1 W was raw fraction; v2.0.19; branch `cursor/swd-280-climate-actuation-c648`.
- 2026-08-09 — shipped SWD-279 via PR #568 (`d894c79`): JSON-safe attrs, weather.get_forecasts, EKF bridge; v2.0.18; review-fix CLEAN. Rebuild App on HAOS for Price Forecast / Disturbances / Linearised from Filtered.
- 2026-08-09 — SWD-279 review-fix CLEAN on PR #568; shipping closeout (v2.0.18).
- 2026-08-09 — SWD-279 In Review + PR #568: JSON-safe attrs, weather.get_forecasts, EKF bridge; v2.0.18; Next `/review-fix SWD-279`.
- 2026-08-09 — `/iterate` SWD-279 from SWD-278: plot forecasts still flat — JSON-safe attrs, weather.get_forecasts, linearised from estimated output; v2.0.18; branch `cursor/swd-279-forecast-bridge-attrs-4b6c`.
- 2026-08-09 — shipped SWD-278 via PR #567: outdoor/solar/price into MPC + MQTT attrs; v2.0.17; review-fix CLEAN. Rebuild App on HAOS for Disturbances + day-ahead Price Forecast.
- 2026-08-09 — SWD-278 In Review + PR #567: outdoor/solar/price into MPC + MQTT attrs; v2.0.17; Next `/review-fix SWD-278`.
- 2026-08-09 — `/iterate` SWD-278 from SWD-277: incomplete outdoor/solar/price/linearised forecasts — MQTT attrs + stop zeroing solar; v2.0.17; branch `cursor/swd-278-forecast-disturbances-f56e`.
- 2026-08-09 — shipped SWD-277 via PR #566: history gated to update_interval + MPC forecasts; v2.0.16; review-fix CLEAN. Rebuild App on HAOS for ~15 min plot cadence and Forecast / Planned Power.
- 2026-08-09 — SWD-277 review-fix CLEAN: room_slug keys, price_tag, power capacity meta, forecast lock; Next `/ship SWD-277`.
- 2026-08-09 — SWD-277 In Review + PR #566: history gated to update_interval + MPC forecast payload; v2.0.16; Next `/review-fix SWD-277`.
- 2026-08-09 — `/iterate` SWD-277 from SWD-276: plot samples too dense + empty forecasts — gate history to update_interval + expose MPC trajectories; v2.0.16; branch `cursor/swd-277-plot-cadence-forecasts-f56e`.
- 2026-08-09 — shipped SWD-276 via PR #565: wall-clock history/control ticker; v2.0.15; review-fix CLEAN. Rebuild App on HAOS so quiet-period plots keep sampling.
- 2026-08-09 — SWD-276 In Review + PR #565: wall-clock history/control ticker; v2.0.15; Next `/review-fix SWD-276`.
- 2026-08-09 — `/iterate` SWD-276 from SWD-275: MQTT ok but KPIs/plots flat overnight — no wall-clock history/control ticker; v2.0.15; branch `cursor/swd-276-wall-clock-ticker-f56e`.
- 2026-08-08 — shipped SWD-275 via PR #564: with-contenv entrypoint for SUPERVISOR_TOKEN; v2.0.14; review-fix CLEAN. Rebuild App on HAOS to pick up token export.
- 2026-08-08 — SWD-275 In Review + PR #564: with-contenv entrypoint for SUPERVISOR_TOKEN; v2.0.14; Next `/review-fix SWD-275`.
- 2026-08-08 — `/iterate` SWD-275 from SWD-274: SUPERVISOR_TOKEN still missing after hassio_api — run.sh lacked with-contenv; v2.0.14; branch `cursor/swd-275-with-contenv-token-f56e`.
- 2026-08-08 — shipped SWD-274 via PR #563: hassio_api + homeassistant_api for SUPERVISOR_TOKEN; v2.0.13; review-fix CLEAN (`supervisor_token_present`). Rebuild App on HAOS to pick up token injection.
- 2026-08-08 — SWD-274 review-fix CLEAN on PR #563: hassio_api confirmed necessary (user still on v2.0.12); health exposes `supervisor_token_present`; packaging flags OK. Next `/ship SWD-274`.
- 2026-08-08 — SWD-274 In Review + PR #563: hassio_api + homeassistant_api for SUPERVISOR_TOKEN; v2.0.13; Next `/review-fix SWD-274`.
- 2026-08-08 — `/iterate` SWD-274 from SWD-273: MQTT rc=5 not authorised — SUPERVISOR_TOKEN missing without `hassio_api`; v2.0.13; branch `cursor/swd-274-hassio-api-token-f56e`.
- 2026-08-08 — shipped SWD-273 via PR #560 (`a536861`): retry Supervisor MQTT discovery + SSL endpoint; v2.0.12; review-fix CLEAN (ssl CI, retry stop-on-creds, no explicit-cred overwrite, HTTP diagnostics, result=error path).
- 2026-08-08 — SWD-273 review-fix CLEAN on PR #560: ssl CI expectation, retry stop-on-creds, no explicit-cred overwrite, HTTP diagnostics, result=error path; shipping.
- 2026-08-08 — SWD-273 In Review + PR #560: retry Supervisor MQTT discovery + SSL endpoint; v2.0.12; Next `/review-fix SWD-273`.
- 2026-08-08 — `/iterate` SWD-273 from SWD-270: MQTT still disconnected after mqtt:need — retry discovery + apply host/port/ssl + TLS + diagnostics; v2.0.12; branch `cursor/swd-273-mqtt-discovery-retry-f56e`.

- 2026-08-08 — shipped SWD-271 via PR #559 (`1306eab`): searchable HA entity catalog + Environment UX; v2.0.11; review-fix CLEAN (catalog flag + weather outdoor °C fallback).
- 2026-08-08 — `/iterate` SWD-271 from SWD-270: streamline config UX — MQTT HA entity catalog for searchable pickers; Environment recommends price+weather, collapses outdoor temp, removes solar irradiance; v2.0.11; branch `cursor/swd-271-config-ux-entity-picker-7676`.
- 2026-08-08 — shipped SWD-270 via PR #558 (`b8201de`): mqtt:need + Supervisor MQTT credential discovery; v2.0.10; review-fix CLEAN.
- 2026-08-08 — SWD-270 review-fix CLEAN: credential pair rule, durable secret fallback, honest `mqtt_source`, redact `mqtt_password` from HTTP JSON; Next Done / ship via PR #558.
- 2026-08-08 — SWD-270 In Review + PR #558: mqtt:need + Supervisor MQTT credential discovery; v2.0.10; Next `/review-fix SWD-270`.
- 2026-08-08 — `/iterate` SWD-270 from SWD-269: MQTT disconnected because Mosquitto rejects anonymous and App lacked `mqtt:need` + Supervisor credential discovery; v2.0.10; branch `cursor/swd-270-mqtt-supervisor-creds-65c0`.
- 2026-08-08 — shipped SWD-269 via PR #557: soft MQTT writes, retain tag/in, in-memory history, KPI sensors, MQTT status; v2.0.9; review-fix CLEAN (energy gap + bare 503 handlers fixed forward).
- 2026-08-08 — SWD-269 In Review + PR #557: soft MQTT writes, retain tag/in, in-memory history, KPI sensors, MQTT status; v2.0.9; Next `/review-fix SWD-269`.
- 2026-08-08 — `/iterate` SWD-269 from SWD-268: empty KPIs/plots + Controller Tuning 502 when MQTT publish hard-fails; soft MQTT writes, retain tag/in, in-memory history, KPI sensors, MQTT status in panel; v2.0.9; branch `cursor/swd-269-empty-kpi-tuning-502-65c0`.
- 2026-08-08 — shipped SWD-268 via PR #554: non-blocking MQTT so Ingress binds without Mosquitto; v2.0.8.
- 2026-08-08 — SWD-268 review-fix CLEAN (focused); should-fix message-dispatch deadlock fixed forward.
- 2026-08-08 — SWD-268 In Review + PR #554: non-blocking MQTT so Ingress binds without Mosquitto; v2.0.8; Next `/review-fix SWD-268`.
- 2026-08-08 — `/iterate` SWD-268: Ingress 502 after v2.0.7 MQTT ship (PR #553); Paho connect raised before HTTP bind; v2.0.8; branch `cursor/swd-268-mqtt-ingress-502-72da`.
- 2026-08-08 — shipped KPI MQTT live sync via PR #553 (v2.0.7): App connects to Mosquitto + panel live state sync; KPIs/room temps populate. (Mirror previously labeled this SWD-268 before the Ingress-502 iterate claimed that key in Jira.)
- 2026-08-08 — shipped SWD-267 via PR #551: typed HA entity IDs + auto MQTT bindings; v2.0.6.
- 2026-08-08 — SWD-267 PR #551: free-text HA entity IDs + auto MQTT bindings; v2.0.6; In Review.
- 2026-08-08 — `/iterate` SWD-267: Ingress entity picker only shows App sensors; free-text entity IDs + auto MQTT bindings; v2.0.6; branch `cursor/swd-267-ha-entity-wiring-5d31`.
- 2026-08-08 — shipped SWD-266 via PR #550: Ingress BASE_PATH from script URL fixes dynamic import LOAD ERROR; v2.0.5.
- 2026-08-08 — `/iterate` SWD-266 from SWD-265: bare `BASE_PATH` breaks dynamic `import()` under Ingress; v2.0.5; PR #550.
- 2026-08-08 — shipped SWD-265 via PR #549: Ingress base href + relative asset/API paths fix loading stall; v2.0.4.
- 2026-08-07 — `/iterate` SWD-262 from SWD-255: port 8100 (PLC clash), Ingress industrial panel App shim, thin-only custom_components; branch `cursor/swd-262-finish-haos-app-01f0`.
- 2026-08-07 — shipped SWD-255 via PR #545 (merge `973a2c5`): HAOS App + thin MQTT integration; Sub-tasks SWD-258/259/260/261/257/256 Done.
- 2026-08-07 — `/ship` SWD-255 implement: App packaging, MQTT bridge, engine compute, Ingress shell, hardening tests (39 passed). Moving to review-fix.
- 2026-08-07 — `/define` SWD-255: HAOS App + thin MQTT integration plan approved; Sub-tasks SWD-258/259/260/261/257/256; branch `cursor/swd-255-haos-app-mqtt-01f0`; Next `/implement SWD-255`.
- 2026-08-06 — shipped SWD-254 via PR #544 (merge `5fa0ac6`): product tree restored to `30814c4`; dual-mode nonlinear MPC removed from main.
- 2026-08-06 — SWD-254 implement: product tree matches `30814c4` (excl. SWD-254 docs); pytest 1750 passed / 6 skipped. PR #544.
- 2026-08-06 — SWD-254: restore tree to `30814c4` (pre PR #539 / SWD-240) to remove dual-mode nonlinear MPC after HA Core hang. Branch `cursor/swd-254-remove-nonlinear-mpc-2550`. Dual-mode artifacts (SWD-239/240/246/247/253 docs) removed with the revert.
