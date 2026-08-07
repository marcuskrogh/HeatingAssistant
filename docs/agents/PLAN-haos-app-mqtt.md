# Implementation plan: Heating Assistant HAOS App + thin MQTT integration

## Summary
- Replace the in-process Heating Assistant architecture with a **HAOS App** that owns compute, durable data, and the operator dashboard (Ingress), plus a **thin HA integration** that only bridges existing HA device entities over **MQTT** (Mosquitto required).
- Packaging and update mechanics take **inspiration from PLCAssistant** (bundled integration sync on App start, single version lock, custom GitHub App repo) — **not** a fork of PLCAssistant.
- **All current functionality is preserved** (MPC/EKF/ID, schedules, multi-sensor averaging, etc.) by reusing existing compute/UI code where it makes sense.

## Scope / Decisions / Constraints

**In**
- HAOS Supervisor **App** in this HeatingAssistant repo (`repository.yaml`, App folder, Dockerfile, `run.sh`).
- Thin `custom_components/…` integration auto-synced onto HA config share on App start; Core restart when disk version ≠ loaded version.
- **Version lock:** App `config.yaml` ≡ integration `manifest.json` ≡ Dockerfile `BUILD_VERSION`.
- Exactly **one** App `config.yaml` in the repo (Supervisor update invariant).
- Mosquitto (or equivalent MQTT broker App) **required**; default broker `core-mosquitto`.
- App owns: control software, config, history, identified parameters, runtime state (App data volume), Ingress dashboard (**as close as identical** to today’s panel).
- Thin integration owns: subscribe/publish for **user HA entities** (temps, heaters, weather, …) ↔ MQTT tags; no Heating Assistant–owned `climate.*` / diagnostic entities in HA.
- Multi-sensor rooms: integration publishes **per-entity tags**; **App averages** (and all fusion) as today.
- Config UX lives in the **App UI** (ported dashboard); App publishes binding/config over MQTT so the integration knows what to bridge.
- Greenfield cutover for this install — no dual-mode in-process fallback, no Core/Container Compose target.

**Out**
- Forking or copying PLCAssistant product behaviour/UI.
- Official HA Apps store publication (custom GitHub App repo is enough).
- Supervisor CPU/memory caps (uncapped for now).
- Embedded MQTT broker inside the App.
- Preserving the old HACS-only in-process runtime as a supported mode.

**Constraints**
- App ↔ HA **data plane = MQTT only** (App does not call the HA API for I/O).
- App load must **not freeze or lock up** the rest of HA when possible.
- Functionality must not regress vs current Heating Assistant capabilities.

## Inputs
- Research: none
- Model: none
- Inspiration: PLCAssistant packaging docs (`01-shape`, `02-mqtt-topics`, `04-updates`) for sync/versioning/MQTT patterns only

## Acceptance criteria
1. On HAOS: install Mosquitto + add this GitHub App repo → install/start App → thin integration appears under `custom_components` after sync; after Core restart, configure entity bindings via App UI.
2. App Update bumps one shared version; Supervisor shows Update; post-update sync + restart-required signal match the PLCAssistant-style flow.
3. Live loop: HA sensor/actuator/weather entities ↔ MQTT ↔ App; heaters commanded correctly; **multi-sensor rooms still average** in the App.
4. Dashboard via Ingress matches current Heating Assistant UX as closely as practical (rooms, schedules, config, controls).
5. Heavy App work (e.g. parameter ID / MPC) does not freeze HA Core UI or other integrations on the MiniPC.
6. Existing compute behaviour preserved for the current feature set (reuse modules where sensible); automated tests cover MQTT bridges, averaging, and packaging/version lock.

## Work packages
1. **App packaging skeleton** ([SWD-258](https://marcusknielsen.atlassian.net/browse/SWD-258)) — App `config.yaml`, Dockerfile, `run.sh` (integration sync + restart request), `repository.yaml`, single-App-config + version-lock CI guards.
2. **Thin MQTT integration** ([SWD-259](https://marcusknielsen.atlassian.net/browse/SWD-259)) — entity↔tag bridge; consume App-published bindings; Mosquitto dependency; no HA-owned Heating entities.
3. **MQTT contract** ([SWD-260](https://marcusknielsen.atlassian.net/browse/SWD-260)) — instance prefix, tag in/out, config/bindings, status/LWT; document topic map (PLC-inspired, Heating-specific).
4. **Move compute into App** ([SWD-261](https://marcusknielsen.atlassian.net/browse/SWD-261)) — reuse thermal model / EKF / MPC / ID / coordinator logic; persist state on App data volume; multi-sensor averaging stays in App.
5. **Port dashboard to Ingress** ([SWD-257](https://marcusknielsen.atlassian.net/browse/SWD-257)) — current panel UX as App UI; config + bindings authored there; push bindings/config to integration over MQTT.
6. **End-to-end hardening** ([SWD-256](https://marcusknielsen.atlassian.net/browse/SWD-256)) — HAOS MiniPC acceptance; load isolation check; regression tests for packaging, MQTT, and core control behaviours.

## Open items
- Exact MQTT topic/payload schema details — initial map in `docs/agents/MQTT-TOPICS.md`; extend during install soak.
- Naming locked: Python package/import slug is `heatingassistant`; HAOS App folder is `heating_assistant`; Supervisor slug remains `heatingassistant`; bundled HA integration domain remains `heating_assistant`.
- Full industrial panel HA-websocket parity in Ingress — shell reuses static assets; remaining WS-dependent panels documented in App `index.html`.
- Root `custom_components/heating_assistant` fat tree still present for legacy unit imports; App installs sync thin from `heating_assistant_mqtt_thin` / App bundle only.

## Tracker
- Provider: jira
- Story: —
- Task: [SWD-255](https://marcusknielsen.atlassian.net/browse/SWD-255)
- Sub-tasks: [SWD-258](https://marcusknielsen.atlassian.net/browse/SWD-258), [SWD-259](https://marcusknielsen.atlassian.net/browse/SWD-259), [SWD-260](https://marcusknielsen.atlassian.net/browse/SWD-260), [SWD-261](https://marcusknielsen.atlassian.net/browse/SWD-261), [SWD-257](https://marcusknielsen.atlassian.net/browse/SWD-257), [SWD-256](https://marcusknielsen.atlassian.net/browse/SWD-256)
- Branch: `cursor/swd-255-haos-app-mqtt-01f0` (delivery; maps to `swd-255-haos-app-mqtt`)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/545

## Next
`/review-fix SWD-255` — Review and auto-fix (single pass)
