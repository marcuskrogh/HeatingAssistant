# Implementation plan: Period editor restructure (SWD-23)

## Summary

- Restructure the room schedule period editor into a type-first layout: type segmented control, Name, When (per type), Behaviour (mode only), Overrides (add-from-picker).
- Support create/edit for all three schedule types, including **Continuous span** with Start/End datetime fields.
- Persist inactive Mode overrides and unused-type When payloads so Comfort↔Off and type switches are reversible without full reconfig; runtime applies only the active Mode/type.
- Overrides UI and intended inherit semantics are specified here; **inherit/override data model stays SWD-20**. Design SWD-20 next, then implement both.

Parent story: [SWD-18](https://marcusknielsen.atlassian.net/browse/SWD-18)

## Scope / Decisions / Constraints

### In scope

- Room schedule **detail** period cards only (not index, overview, drag-reorder, experiments).
- Type picker: segmented control at top — **Weekly recurring** / **Date range** / **Continuous span**.
- Name directly under type.
- **When** by type:
  - Weekly: weekdays + explicit **All day** vs **time window**; Start/End times only for window.
  - Date range: start/end dates + same All day / window control.
  - Continuous span: Start and End as **datetime** fields.
- **Behaviour**: Mode only (`Comfort` / `Off`).
- **Overrides**: mode-filtered add-from-picker; removable (back to inherit). Comfort: setpoint, comfort offset, tracking weight, energy weight. Off: frost protection only.
- Mode switch: keep other mode’s overrides in the saved payload; do not apply them while inactive.
- Type switch: keep **all** type When payloads in the saved period; active `schedule_type` selects which apply.
- Add Period defaults: Weekly recurring, Comfort, window 08:00–22:00, all weekdays, Overrides empty.
- Layout/CSS pass for the new sections; drop legacy Recurring weekly / All day checkbox conflation in the editor.
- Schema/panel support as needed for multi-When persistence and inactive Mode override fields (alongside SWD-20 for absent=inherit semantics).

### Out of scope

- Experiments block (unchanged).
- SWD-20 backend inherit resolution (designed separately; implemented with this after both designs).
- Drag-to-reorder (SWD-24), schedules index (SWD-22), overview/room viz (SWD-21).
- HA options-flow schedule editor.

### Dependencies

- Builds on SWD-19 schedule types / `time_mode`.
- Intended Overrides inherit behaviour depends on SWD-20; this plan documents the UX contract only for that part.

## Acceptance criteria

- Expanded period editor shows Type → Name → When → Behaviour → Overrides in that order.
- User can create and edit all three types, including continuous span datetimes.
- Changing type or mode and changing back restores prior When / override config without re-entry (within saved data).
- Override picker lists only mode-relevant settings not already added; remove returns that setting to inherit (per SWD-20 semantics when implemented).
- New periods start with empty Overrides and do not snapshot room defaults in the intended model (SWD-20); UI must not force-seed override fields on add.
- Legacy editor toggles (`recurring` / `all_day` as primary UX) are gone from the period card.
- Experiments section unchanged.
- Panel tests/harnesses cover type switching, continuous span editing, override add/remove, and mode-filtered picker.

## Work packages

1. **Period card layout shell** — Sectioned Type / Name / When / Behaviour / Overrides structure and CSS; remove legacy toggle layout.
2. **Type picker and When-by-type** — Segmented type control; When UIs for weekly, date range, and continuous span (datetime); All day vs window control; Add Period defaults.
3. **Multi-type When persistence** — Store all type When payloads on the period; active `schedule_type` drives matching/editing; type switch preserves fields.
4. **Behaviour + Overrides UX** — Mode-only Behaviour; mode-filtered add/remove override picker; persist inactive-mode overrides without applying them.
5. **Serialization and panel helpers** — Wire editor state to SWD-19 types + multi-When / dual-mode override fields; keep compatibility until SWD-20 lands for true absent=inherit.
6. **Tests** — Harness/unit coverage for layout behaviour, type/mode round-trips, continuous span, override picker filtering and remove.

## Open items

- Exact override-picker chrome (popover vs inline menu) — implementer choice within “add-from-picker.”
- Collapsed-card summary strings for continuous span / date range — follow existing summary patterns.
- SWD-20 design next; joint implementation after both plans exist.

## Jira

- Parent: [SWD-18](https://marcusknielsen.atlassian.net/browse/SWD-18)
- Design ticket: [SWD-23](https://marcusknielsen.atlassian.net/browse/SWD-23)
- Related: [SWD-19](https://marcusknielsen.atlassian.net/browse/SWD-19) (types), [SWD-20](https://marcusknielsen.atlassian.net/browse/SWD-20) (inherit model, design next)
- Sub-tasks:
  1. [SWD-33](https://marcusknielsen.atlassian.net/browse/SWD-33) — Period card layout shell
  2. [SWD-36](https://marcusknielsen.atlassian.net/browse/SWD-36) — Type picker and When-by-type
  3. [SWD-31](https://marcusknielsen.atlassian.net/browse/SWD-31) — Multi-type When persistence
  4. [SWD-35](https://marcusknielsen.atlassian.net/browse/SWD-35) — Behaviour and Overrides UX
  5. [SWD-34](https://marcusknielsen.atlassian.net/browse/SWD-34) — Serialization and panel helpers
  6. [SWD-32](https://marcusknielsen.atlassian.net/browse/SWD-32) — Period editor panel tests
