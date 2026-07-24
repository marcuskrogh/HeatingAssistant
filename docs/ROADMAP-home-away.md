# Roadmap: Home / Away climate control

## Goals

- Let Heating Assistant recognise when residents are **Home** or **Away** and relax indoor climate control while Away, so the house does not spend energy holding a tight temperature band when nobody is there.
- Keep the product surface **simple**: one house-level Home/Away concept that users can toggle (and optionally drive from HA presence), without requiring per-room schedule edits for everyday absences.
- Save energy without harming well-being: Away must still respect frost / building-protection floors and recover gracefully when people return.

## Problem today

Comfort schedules already support clock-based setback and off periods (`docs/CONFIGURATION.md` §10.6), including vacation-style `continuous_span` windows. Matching is **local clock only** — “no presence sensor or automation required.” That works for predictable routines, but:

- Unplanned absences (afternoon outing, weekend trip decided last-minute) still track the occupied setpoint tightly.
- There is no house-wide occupancy state; setback must be configured per room as schedule periods.
- MPC preheat anticipates *scheduled* comfort transitions, not an unexpected “someone came home.”
- `docs/ROADMAP-scheduling.md` explicitly lists presence as an out-of-scope follow-on.
- `improvements/ACTIONS.md` Action 5 notes eco/away corridors should be wider (±2.5–3 °C) than occupied (±1 °C), but presets are not shipped.
- `docs/ROADMAP.md` §17.6 plans occupancy for **internal-gain forecasting**, not for setpoint / corridor Home–Away control.

Users can approximate Away with HA automations calling `set_room_setpoint` / `update_room_schedule` / climate OFF — workable but not an integrated product feature.

## Product intent

| State | User expectation |
|-------|------------------|
| **Home** | Normal control: base setpoints + comfort schedule + default (or tight) comfort corridor. |
| **Away** | Energy-saving policy: do **not** tightly track occupied setpoints; allow a wider band and/or a mild setback so heating/cooling is used only when needed to stay in a safe, recoverable range. |

Away is **not** the same as system STOP (`set_system_enabled: false`) and not necessarily the same as schedule `mode: off`. Default Away should keep the plant protective and recoverable, not abandon the house to outdoor temperature.

## Recommended Away policy (v1)

Apply a **house-wide overlay** on top of the current schedule resolution, for every room that is user-enabled:

1. **Setback setpoint** — optional delta from the room’s *effective schedule/base setpoint* (e.g. −2 °C heating / +2 °C cooling when heat pumps can cool), or an absolute eco setpoint. Default: mild relative setback so rooms stay close enough for fast recovery.
2. **Wider comfort corridor** — raise effective `comfort_offset` to an Away preset (literature-aligned **2.5–3.0 °C**, see Action 5) so MPC does not fight small deviations.
3. **Energy bias** — optionally multiply `energy_weight` upward while Away so the optimiser prefers cheaper / lower actuation inside the corridor.
4. **Frost / protection floor** — never go below the room’s frost-protection floor (default 12 °C); Away must not defeat window-open or frost logic.
5. **On return to Home** — clear the overlay; resume schedule/base immediately. Rely on existing MPC horizon preheat for the next *scheduled* comfort rise; optional anticipatory return is a later phase.

### Explicitly deferred from v1

- Per-room Away exceptions (e.g. keep nursery tight) — nice-to-have after house-wide ships.
- Geofence ETA / distance-based preheat before arrival.
- HMM occupancy → `Q_int` forecasting (`ROADMAP.md` §17.6) — complementary, not a substitute for Home/Away control.
- Replacing weekly eco schedule periods — schedules remain for predictable routines; Home/Away covers occupancy that the clock cannot know.

## Architecture sketch

Hook after schedule resolution in `coordinator/schedule_control.py` (`apply_schedule` / `compute_control_trajectory`):

```
base + schedule  →  EffectiveControlParams
                         ↓
              Home/Away overlay (if Away)
                         ↓
              live room setpoint / corridor / Q·R scales
```

Conflict precedence (proposed):

1. System disabled → no actuation (unchanged).
2. Window-open override → force heat/cool off (unchanged).
3. User room disabled / climate OFF → room off (unchanged).
4. **Away overlay** when house occupancy is Away (and Away feature enabled).
5. Comfort schedule (including `mode: off` + frost).
6. Room base setpoint / default comfort offset.

When Away and an `off` schedule period both apply: keep heaters off + frost (schedule off is stricter); Away setback only affects rooms that would otherwise be in comfort/tracking.

Horizon projection: while Away is active, project Away params flat across the MPC horizon (same pattern as schedule-suspended). When presence can forecast a return within the horizon (phase 3+), blend Home params into later steps for anticipatory recovery.

## Presence / recognition

| Source | Role |
|--------|------|
| **Manual / service / panel toggle** | Primary, always available. Persisted like `system_enabled`. |
| **Optional HA entity** | `binary_sensor`, `input_boolean`, `person`/`group`, or `zone.home` count → Away when “nobody home”. |
| **HA automations** | Remain supported via `set_occupancy` / `set_away` service even without a bound entity. |

v1 recognition = bind one entity (or none) + debounce (e.g. 5–15 min continuous Away before applying overlay) to avoid flapping when phones drop off Wi‑Fi briefly.

## Scope

### In (feature phases below)

- House-level occupancy state: `home` | `away`.
- Away control overlay: setback + wider corridor (+ optional energy-weight bias).
- Service + persisted state + overview UI indicator/toggle.
- Optional presence-entity binding with debounce.
- Docs, entities, and tests.

### Out (later)

- Per-room Away overrides and “keep this room Home.”
- Arrival ETA / geofence preheat.
- Occupancy → internal-gain forecast (roadmap Phase 5/6 disturbance work).
- Hold-for-N-hours and global schedule templates (still separate follow-ons from scheduling roadmap).

## Suggested phases

| Phase | Topic | Notes |
|-------|-------|-------|
| **1** | Occupancy state & Away overlay | House `home`/`away` state; persist; apply setback + comfort_offset (+ optional energy_weight) after `apply_schedule`; flat horizon while Away; frost/window precedence; services `set_occupancy` / get state. |
| **2** | Panel + entities | Overview Home/Away toggle; optional settings for setback Δ, Away corridor preset, energy bias; `sensor`/`binary_sensor` exposing occupancy; room tiles show Away badge when overlay active. |
| **3** | Presence binding | Config option for presence entity; debounce; document recommended `zone.home` / person group patterns; tests for flap immunity. |
| **4** *(optional)* | Return awareness | If presence entity leaves Away with horizon remaining, or user has a calendar/ETA helper, project Home setpoints into later MPC steps so recovery starts before arrival. |
| **5** *(optional)* | Per-room exceptions + Action 5 presets | Room-level “follow Away / stay Home”; ship Tight/Standard/Relaxed corridor presets shared with schedule editor (Action 5). |

## Open questions

- Default Away behaviour: **relative setback** (e.g. −2 °C) vs **absolute eco setpoint** (e.g. 18 °C) vs **corridor-only** (same setpoint, wider band / higher energy weight)? Recommendation: relative setback + wider corridor as the default package; absolute setpoint as advanced option.
- Does Away suspend schedule matching entirely, or only override comfort periods? Recommendation: overlay on comfort; honour schedule `off` + frost as-is.
- Should Away apply to cooling the same way (raise cooling setpoint / widen band)? Recommendation: yes, symmetric eco delta for heat-pump rooms.
- Debounce defaults and whether “Away armed” requires explicit feature enable vs always-on toggle.
- Interaction with schedule suspend (`set_schedule_enabled: false`) used today for WFH/guests — Away should still apply when schedules are suspended unless we document otherwise.

## Acceptance criteria (feature-level)

- User can set house to Away (panel or service) and all enabled rooms stop tightly tracking occupied setpoints within one coordinator cycle.
- Measured energy / actuation intensity drops for the same outdoor conditions vs Home (test harness or documented KPI), while temperatures stay above frost floor.
- Returning to Home restores schedule/base behaviour without requiring schedule edits.
- Optional presence entity can drive the same state with debounce; manual override remains possible.
- Window-open, frost, and system/room disable continue to work under Away.
- Docs explain Home/Away vs weekly eco schedules vs vacation `continuous_span`.

## Jira

- Story: [SWD-60](https://marcusknielsen.atlassian.net/browse/SWD-60) — Home/Away climate control (Heating Assistant)
- Tasks:
  - [SWD-63](https://marcusknielsen.atlassian.net/browse/SWD-63) — Occupancy state and Away control overlay (Phase 1)
  - [SWD-64](https://marcusknielsen.atlassian.net/browse/SWD-64) — Home/Away panel UI and entities (Phase 2)
  - [SWD-62](https://marcusknielsen.atlassian.net/browse/SWD-62) — Presence entity binding with debounce (Phase 3)
  - [SWD-65](https://marcusknielsen.atlassian.net/browse/SWD-65) — Return awareness / horizon blend (Phase 4, optional)
  - [SWD-61](https://marcusknielsen.atlassian.net/browse/SWD-61) — Per-room Away exceptions and corridor presets (Phase 5, optional)

## Related artifacts

- `docs/ROADMAP-scheduling.md` — presence listed as scheduling follow-on; this roadmap owns the product Home/Away feature.
- `docs/ROADMAP.md` §17.6 — occupancy for `Q_int` forecast (complementary).
- `improvements/ACTIONS.md` Action 5 — corridor presets for occupied vs eco/away.
- `coordinator/schedule_control.py` — primary integration point for the overlay.
