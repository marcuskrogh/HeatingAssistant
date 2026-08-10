# Implementation plan: Restore system identification (App ownership)

## Summary
- The System Identification room page no longer runs automatic identification, one-step EKF reconstruction, or multi-step open-loop simulation after the HAOS App cutover.
- Two layers fail together: **panel ES-module gaps** (immediate `buildEkfChart` / `buildOlChart` ReferenceErrors) and **App runtime sysid no-ops** (services accept and return without computing; synthetic sensors never published).
- Restore original integration behaviour by fixing panel imports and ending the deferred SWD-281 out-of-scope item: full App ownership of sysid services, caches, datasets, and result sensors.

## Scope / Decisions / Constraints

**In**
- Fix `sysid-datasets.js` / `sysid-detail.js` import/export gaps so EKF/OL charts and Stored Datasets UI load.
- End `HeatingRuntime.apply_service` catch-all no-op for identification services needed by the room page.
- Restore hass-free engine modules deleted in SWD-262 that the page depends on (`compute_open_loop_predictions`, `DatasetStore` / `build_dataset`, parameter lifecycle, simulation helpers).
- Publish Ingress synthetic sensors `sensor.heating_assistant_<slug>_sysid_simulation` and `sensor.heating_assistant_<slug>_open_loop_rmse` from runtime caches (same attribute contracts the panel already expects).
- Persist identified parameters + parameter history via App config / `data_dir`; expose `parameter_history` (and related fields) on `controller_config`.
- Real dataset create/list/get/delete under App `/data`.
- Version bump to **2.0.27** + `scripts/sync-ha-app-package.sh`.

**Out**
- Scheduled identification **experiments** (schedule / cancel / delete experiment) — leave as no-op or stub unless already trivial.
- Diagnostics services not used by the room page (`analyze_model_fit`, `validate_parameters`, `controller_performance_report`, `compute_loglik_slice`, setup `estimate_parameters` / `simulate_thermal_response`) unless needed as shared helpers.
- Reintroducing fat HA Core diagnostic entities / Recorder rebuild path (same constraint as SWD-281).
- Redesigning the sysid UI or changing identification algorithms.

**Decisions**
- Port pre-SWD-262 handlers/helpers from git (`ef816f8^`) into App/engine modules rather than reinventing contracts — panel payload shapes stay stable.
- History for ID windows comes from App `history_buffer` + `IdentificationHistoryStore` (already restored by SWD-281); do not call HA Recorder.
- CPU-heavy EKF/ML/open-loop work runs off the event loop (`asyncio.to_thread`), matching the old executor pattern.
- After each mutating service, shim `refresh()` / next `hass_states()` must surface updated sensor attrs so the panel’s post-call wait can plot results.

**Constraints**
- Thin MQTT bridge remains I/O-only; sysid compute stays in the App.
- Shared version lock: App `config.yaml` ≡ integration `manifest.json` ≡ Dockerfile / `pyproject.toml`.
- Cloud delivery branch uses `cursor/…-851a`; maps to workspace pattern `swd-289-sysid-services`.

## Inputs
- Research: none
- Model: none
- Prior: SWD-281 deferred *Full sysid service ownership / ending sysid no-ops*; SWD-262 deleted fat HA `services/identification.py`, `model_diagnostics.py`, `datasets.py`, `coordinator/parameter_lifecycle.py`, `sensor/diagnostics.py` (`SysIdSimulationSensor` / `OpenLoopRMSESensor`)
- User report: EKF/OL buttons throw `Can't find variable: buildEkfChart` / `buildOlChart`; automatic identification unavailable; API connected

## Acceptance criteria
1. Room sysid page: **Run One-Step EKF Reconstruction** completes without ReferenceError and draws the measured/predicted chart + RMSE/MAE from `*_sysid_simulation`.
2. **Run Multi-Step Open-Loop Simulation** completes without ReferenceError and draws the open-loop chart + RMSE/MAE from `*_open_loop_rmse`.
3. **Run Automatic Identification** (selected datasets and/or current window) dry-runs ML estimation and populates editable parameter fields from sysid results (Apply still required).
4. **Apply Parameters** persists thermal/heater/stochastic params into the live `ControlEngine` model and App config; Applied Model History lists snapshots and supports delete.
5. **Save / select / load / delete** stored datasets round-trip under App `/data` and appear via `/api/datasets` / shim `list_datasets`.
6. Panel cache-bust token bumped; App package synced; shared version **2.0.27**.
7. Automated regressions cover: panel import harness; `apply_service` for the three P0 compute services populates `hass_states()` sensor attrs; create_dataset appears in `datasets()`; store params → `controller_config()["parameter_history"]`.

## Work packages
1. **Panel ES-module fix** ([SWD-293](https://marcusknielsen.atlassian.net/browse/SWD-293)) — export/import chart helpers + `formatMass`; import `createCollapsible` / `makeDataset` in datasets module; regression harness; cache-bust bump.
2. **Restore engine modules** ([SWD-292](https://marcusknielsen.atlassian.net/browse/SWD-292)) — port `compute_open_loop_predictions` (min), `DatasetStore`/`build_dataset`, parameter lifecycle, simulation helpers into `heatingassistant/engine/` (hass-free, App `data_dir`).
3. **P0 compute + sensors** ([SWD-294](https://marcusknielsen.atlassian.net/browse/SWD-294)) — wire `estimate_parameters_ml`, `run_sysid_simulation`, `run_open_loop_simulation` in `apply_service`; maintain caches; synthesize sysid/open-loop sensors in `hass_states()`.
4. **Apply / persist / history** ([SWD-290](https://marcusknielsen.atlassian.net/browse/SWD-290)) — wire `store_identified_parameters`, `update_estimation_params`, `delete_parameter_history`; extend `controller_config()`.
5. **DatasetStore** ([SWD-295](https://marcusknielsen.atlassian.net/browse/SWD-295)) — wire `create_dataset` / `delete_dataset`; back `/api/datasets` from the durable store.
6. **Tests + ship prep** ([SWD-291](https://marcusknielsen.atlassian.net/browse/SWD-291)) — App regressions; adapt/unskip identification tests where practical; version **2.0.27**; sync App package.

## Open items
- Experiments UI path remains out of scope; confirm during implement whether leaving those services as no-ops produces user-visible errors on Schedules experiments (if so, minimal stub messaging only).
- Exact persistence file layout for `DatasetStore` / parameter history under `/data` — prefer pre-SWD-262 schema adapted to App paths; lock during package 2.

## Tracker
- Provider: jira
- Story: —
- Task: [SWD-289](https://marcusknielsen.atlassian.net/browse/SWD-289)
- Sub-tasks: [SWD-293](https://marcusknielsen.atlassian.net/browse/SWD-293), [SWD-292](https://marcusknielsen.atlassian.net/browse/SWD-292), [SWD-294](https://marcusknielsen.atlassian.net/browse/SWD-294), [SWD-290](https://marcusknielsen.atlassian.net/browse/SWD-290), [SWD-295](https://marcusknielsen.atlassian.net/browse/SWD-295), [SWD-291](https://marcusknielsen.atlassian.net/browse/SWD-291)
- Relates: [SWD-281](https://marcusknielsen.atlassian.net/browse/SWD-281)
- Branch: `cursor/swd-289-sysid-services-851a` (maps to `swd-289-sysid-services`)
- PR: _(set after draft open)_

## Next
`/implement SWD-289` — Build per this plan (same branch/PR)
