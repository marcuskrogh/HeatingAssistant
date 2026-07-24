# Roadmap: Deliberate schedule configuration

## Goals

- Make schedule configuration clearer and more deliberate: orthogonal concepts (timing, behaviour, overrides) are visually and structurally separated.
- Introduce explicit **schedule types** so users can express weekly recurring, date-bounded daily windows, and one continuous span (e.g. vacation) without confusion.
- Replace implicit priority with **drag-to-reorder** on room schedule pages; first match wins.
- Fix the **inherit vs override** model: only settings explicitly added to a period are stored; everything else always follows the current room default (so a global setpoint change does not require editing every period).
- Migrate existing saved periods cleanly to the new model with no user rework.

## Scope

### In

- **Schedules page** (room detail editor as primary focus; index page updated to reflect new period types and boxed separation).
- Full **layout restructure** of the period editor into clear sections (type/when, behaviour, optional overrides).
- Three schedule types, selected up front:
  1. **Weekly recurring** — current weekday + time-of-day pattern.
  2. **Date range, daily window** — same time window applied on each day within a start/end date range.
  3. **Continuous span** — single uninterrupted window from a start datetime to an end datetime (e.g. vacation).
- **Drag-and-drop reorder** of periods on room schedule detail pages only.
- **Override picker** UX: inheritable settings (setpoint, comfort offset, tracking/energy weights, frost protection, and future tunables) appear on a period only after the user adds them; unset = inherit room default at runtime.
- Backend model, serialization, matching logic, and **automatic migration** for existing periods.
- Clearer structure for advanced per-period settings within the new layout.

### Out (optional follow-on)

- Overview and room-detail schedule visualizations (boxed layout, NOW/NEXT badges, possible time-axis integration).
- Drag-and-drop outside room schedule pages.
- New scheduling capabilities beyond the three types (hold-for-N-hours, global templates).
- House-level **Home/Away** occupancy control (presence-driven eco overlay) — tracked separately in [`ROADMAP-home-away.md`](ROADMAP-home-away.md).

## Suggested phases

| Phase | Topic | Notes |
|-------|-------|-------|
| **1** | Schedule type model & migration | Add explicit `schedule_type` (or equivalent); implement continuous-span matching; map existing `recurring` / `all_day` / date fields to the three types without user action; extend tests. |
| **2** | Inherit/override data model | Ensure only explicitly overridden fields are persisted (`null`/absent = inherit); fix UI serialization so new periods do not snapshot room defaults; runtime resolution always uses current room values for inherited fields. |
| **3** | Period editor restructure | Type picker up front; distinct **When** section per type (no conflated all-day / recurring toggles); **Behaviour** section (mode); **Overrides** section with add-from-picker; full layout pass. |
| **4** | Drag-to-reorder priority | Reorderable period list on room schedule detail; persist order; first-match-wins unchanged semantically. |
| **5** | Schedules index refresh | Update period previews/summaries for new types; maintain NOW/NEXT badges; boxed separation between periods. |
| **6** *(optional)* | Overview & room view | Boxed period cards, clearer NOW/NEXT; explore time-axis visualization as a separate design pass. |

## Open questions

- Final labels and field layout per schedule type (especially continuous span datetime pickers).
- Full list of settings in the override picker at launch vs. extensibility for future tunables.
- Whether the sys-id **experiments** block on the schedule page gets the same layout treatment in phase 3 or stays as-is.
- Whether the HA **options-flow** schedule editor should eventually mirror the panel (likely deferred).

## Jira

- Story: [SWD-18](https://marcusknielsen.atlassian.net/browse/SWD-18) — Deliberate schedule configuration (Heating Assistant)
- Tasks:
  - [SWD-19](https://marcusknielsen.atlassian.net/browse/SWD-19) — Schedule type model and migration
  - [SWD-20](https://marcusknielsen.atlassian.net/browse/SWD-20) — Inherit/override data model
  - [SWD-23](https://marcusknielsen.atlassian.net/browse/SWD-23) — Period editor restructure
  - [SWD-24](https://marcusknielsen.atlassian.net/browse/SWD-24) — Drag-to-reorder schedule priority
  - [SWD-22](https://marcusknielsen.atlassian.net/browse/SWD-22) — Schedules index refresh
  - [SWD-21](https://marcusknielsen.atlassian.net/browse/SWD-21) — Overview and room view schedule visualization (optional)
