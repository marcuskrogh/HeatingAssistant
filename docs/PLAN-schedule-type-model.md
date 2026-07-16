# Implementation plan: Schedule type model and migration (SWD-19)

## Summary

Introduce an explicit `schedule_type` on every comfort period, with three variants: `weekly_recurring`, `date_range_daily`, and `continuous_span`. Replace the implicit `recurring` / `all_day` combination with a deliberate type plus a `time_mode` enum where relevant. Implement continuous-span matching with half-open local datetimes. Migrate all existing UI-persisted periods on upgrade, normalize storage, and ship minimal panel parity so the current editor keeps working until SWD-23.

Parent story: [SWD-18](https://marcusknielsen.atlassian.net/browse/SWD-18)

## Scope / Decisions / Constraints

### In scope (SWD-19)

- Python `SchedulePeriod` / `build_schedule` / matching logic for all three types
- `time_mode`: `"window"` | `"all_day"` on `weekly_recurring` and `date_range_daily` only
- `continuous_span`: `start_at` / `end_at` as local ISO datetimes (`2026-07-20T10:00:00`, no timezone suffix)
- Half-open matching everywhere (inclusive start, exclusive end)
- One-shot migration on integration upgrade: rewrite config-entry schedules immediately
- `update_room_schedule` service schema: `schedule_type` required; reject legacy-only payloads
- Minimal panel parity: `serializeSchedulePeriod`, `periodMatchesNow`, and related helpers
- Tests (Python + harness)

### Out of scope

- Full period editor restructure, datetime picker UX → SWD-23
- Inherit/override data model → SWD-20
- Drag-to-reorder → SWD-24
- Schedules index refresh → SWD-22
- YAML `configuration.yaml` schedule definitions (assume schedules come from UI only)

### Period shape by type

| `schedule_type` | When fields | `time_mode` |
|---|---|---|
| `weekly_recurring` | `days`, `start`/`end` (HH:MM, when window) | `"window"` \| `"all_day"` |
| `date_range_daily` | `start_date`, `end_date`, `start`/`end` (when window) | `"window"` \| `"all_day"` |
| `continuous_span` | `start_at`, `end_at` (local ISO datetime) | — |

Legacy fields `recurring` and `all_day` are **not** persisted after migration.

### Migration mapping (existing UI data → new model)

| Legacy | New |
|---|---|
| `recurring: true`, `all_day: false` | `weekly_recurring`, `time_mode: "window"` |
| `recurring: true`, `all_day: true` | `weekly_recurring`, `time_mode: "all_day"` |
| `recurring: false`, `all_day: false` | `date_range_daily`, `time_mode: "window"` |
| `recurring: false`, `all_day: true` | `date_range_daily`, `time_mode: "all_day"` |

No existing period becomes `continuous_span` (new type only).

### Minimal panel behaviour (until SWD-23)

- Current editor toggles remain locally; `serializeSchedulePeriod` maps to `schedule_type` + `time_mode` on save
- `continuous_span` cannot be created from the current UI (matching + model only); creation UX deferred to SWD-23

## Acceptance criteria

- Every persisted period has `schedule_type`; payloads without it are rejected
- No `recurring` or `all_day` fields remain in stored config-entry data after upgrade
- `weekly_recurring` and `date_range_daily` behave identically to today for migrated data
- `continuous_span` matches half-open `[start_at, end_at)` in local time
- `time_mode: "all_day"` covers the full calendar day on matching weekdays or dates
- Upgrade migration runs once and rewrites the config entry without user action
- `periodMatchesNow` (JS) mirrors Python `SchedulePeriod.matches()`
- Existing Python schedule tests updated; new tests cover all three types, migration, and continuous span edge cases (same-day span, cross-midnight span, boundary exclusivity)
- Current schedules panel can still view, edit, and save migrated periods without errors

## Work packages

1. **Constants and period schema** — Add `schedule_type`, `time_mode`, `start_at`, `end_at` constants; define validation rules per type in `build_schedule`.
2. **Matching logic** — Refactor `SchedulePeriod.matches()` for three explicit types; remove `recurring`/`all_day` code paths.
3. **Upgrade migration** — Detect legacy periods on load; map per table above; persist normalized config entry immediately.
4. **Service schema and serialization** — Update `update_room_schedule` Voluptuous schema; update coordinator round-trip serialization; reject legacy-only writes.
5. **Minimal panel parity** — Update `serializeSchedulePeriod` and `periodMatchesNow` (and helpers) to read/write the new shape; keep old editor toggles working via translation layer.
6. **Tests** — Extend `test_schedule.py`, migration round-trip tests, service schema tests, and JS harness coverage.

## Open items

- Exact UI placement of period vs all-day selector (deferred to SWD-23; storage uses `time_mode` enum)
- Whether `continuous_span` creation needs a temporary stub in the current editor before SWD-23 (likely not — model-only until new editor ships)

## Jira

- Parent: [SWD-18](https://marcusknielsen.atlassian.net/browse/SWD-18)
- Design ticket: [SWD-19](https://marcusknielsen.atlassian.net/browse/SWD-19)
- Sub-tasks:
  - [SWD-25](https://marcusknielsen.atlassian.net/browse/SWD-25) — Constants and period schema
  - [SWD-27](https://marcusknielsen.atlassian.net/browse/SWD-27) — Matching logic
  - [SWD-29](https://marcusknielsen.atlassian.net/browse/SWD-29) — Upgrade migration
  - [SWD-30](https://marcusknielsen.atlassian.net/browse/SWD-30) — Service schema and serialization
  - [SWD-26](https://marcusknielsen.atlassian.net/browse/SWD-26) — Minimal panel parity
  - [SWD-28](https://marcusknielsen.atlassian.net/browse/SWD-28) — Tests
