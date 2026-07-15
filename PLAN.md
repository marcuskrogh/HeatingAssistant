# Implementation plan: Fix schedule persistence regression

## Summary

Schedules created on the Heating Assistant dashboard schedules page appear saved on the room detail view after clicking **Save Changes**, but disappear on other pages (schedules index, overview) and do not survive a Home Assistant restart. One older schedule still persists — likely written before a regression or via a legacy storage path.

This is a **dual failure**:

1. **Frontend display gap** — the detail page masks missing/stale data with optimistic fallbacks; list views do not.
2. **Backend persistence gap** — new saves are not reliably written to (or reloaded from) `persisted_schedules` in the config entry.

## What we know (reproduction)

| Step | Behaviour |
|------|-----------|
| Create period + **Save Changes** | Detail page shows saved periods |
| Navigate to overview / schedules index | Periods missing |
| Navigate back to same room detail | Periods still visible |
| Full HA restart | New schedules gone; one old schedule remains |

The detail page "still works" after navigation because `schedules-detail.js` falls back to optimistically patched config-entity state (`patchStateSchedule`) and a post-save snapshot — not because all consumers see authoritative data. PR #512 added that workaround explicitly for stale/empty `getSchedules()` WebSocket responses.

## Root cause analysis

### Frontend (confirmed in code)

- **Detail page** (`schedules-detail.js`): `resolveScheduleData()` — WebSocket → config-entity state → `savedScheduleSnapshot`.
- **Schedules index** (`schedules-index.js`), **overview** (`overview.js`), **room detail**: only `connection.getSchedules()` — no fallback.
- After save, `patchStateSchedule()` updates in-memory panel state, so the detail page looks correct even when WebSocket/coordinator data is empty or stale.

### Backend (suspected regression)

- Save path: `updateRoomSchedule` → `handle_update_room_schedule` → `reload_room_schedule` + `async_update_entry` with `CONF_PERSISTED_SCHEDULES`.
- Load path: `_init_room_state` overlays `self._entry.data[CONF_PERSISTED_SCHEDULES]` on startup.
- Tests cover the service write and startup overlay, but **not** a full round-trip (save → restart → all UI consumers see data).
- The surviving old schedule may be stored under a legacy path (`entry.data.rooms[].schedule`, preserved by YAML merge) while newer saves only use `persisted_schedules` — a path that may have regressed in the horizontal/vertical slice refactor (#498) or related changes.
- `coordinator._entry` is a `MergedEntry` whose `.data` is **not updated** after save; if anything writes merged data back without `persisted_schedules`, disk state could be wiped.

## Scope / Decisions / Constraints

**In scope**

- Fix persistence so dashboard-created schedules survive HA restart.
- Unify schedule read path across all panel pages (same fallback strategy as detail).
- Add regression tests for save → reload → display round-trip.
- Investigate why `getSchedules()` returns empty/stale after a successful save (fix at source if possible, not only mask in UI).

**Out of scope**

- Redesigning the schedules UX (e.g. auto-save on every edit) unless needed as part of the fix.
- YAML/options-flow schedule editing — dashboard is the reported surface.

**Constraints**

- Follow existing patterns: `CONF_PERSISTED_SCHEDULES` (same as setpoints), `patchStateSchedule`, `serializeSchedulePeriod`.
- Minimal diff; no unrelated refactors.

## Acceptance criteria

1. Create a new comfort period on the schedules detail page, click **Save Changes**, navigate to **Overview** and **Schedules index** — the period appears on both without re-saving.
2. After a full HA restart, the saved schedule is still present on detail, index, and overview.
3. Existing persisted schedules (including the one old schedule) are not lost or corrupted.
4. Period enable/disable toggles on the index page continue to persist immediately (existing behaviour).
5. New automated tests cover backend round-trip persistence and frontend read-path consistency across navigation.

## Work packages

1. **Diagnose persistence** ([SWD-5](https://marcusknielsen.atlassian.net/browse/SWD-5)) — After save, inspect config entry for `persisted_schedules`; add a failing integration test that saves → reloads coordinator → asserts periods present. Determine whether the write fails, keys mismatch, or data is wiped on reload.

2. **Fix backend persistence** ([SWD-3](https://marcusknielsen.atlassian.net/browse/SWD-3)) — Ensure `handle_update_room_schedule` reliably persists and reloads; sync `coordinator._entry.data` after save if needed; verify room-name keys match between save and `_init_room_state` overlay. Migrate any legacy `rooms[].schedule` data if relevant.

3. **Fix `getSchedules` staleness** ([SWD-6](https://marcusknielsen.atlassian.net/browse/SWD-6)) — If coordinator has schedules in memory after save but WebSocket returns empty, fix serialization or listener ordering so authoritative data is immediately available.

4. **Unify frontend read path** ([SWD-7](https://marcusknielsen.atlassian.net/browse/SWD-7)) — Extract shared `resolveScheduleData` (or equivalent) into `schedules-shared.js`; use it in index, overview, and room-detail so all pages read schedules consistently.

5. **Regression tests** ([SWD-4](https://marcusknielsen.atlassian.net/browse/SWD-4)) — Python: save → entry reload → coordinator init → `serialize_room_schedules`. JS harness: save patches state; index uses shared resolver; navigation does not lose displayed periods.

## Open items

- Which room holds the surviving old schedule, and whether new schedules are created on the same or different rooms (helps confirm room-key mismatch).
- Whether the user's setup uses YAML-defined rooms (affects legacy `rooms[].schedule` vs `persisted_schedules` interaction).
- Whether PR #512 is deployed in the environment where the bug is seen (detail-page workaround exists but list views were not updated).

## Jira

- Design ticket: [SWD-2](https://marcusknielsen.atlassian.net/browse/SWD-2)
- Sub-tasks:
  - [SWD-5](https://marcusknielsen.atlassian.net/browse/SWD-5) — Diagnose schedule persistence failure
  - [SWD-3](https://marcusknielsen.atlassian.net/browse/SWD-3) — Fix backend schedule persistence
  - [SWD-6](https://marcusknielsen.atlassian.net/browse/SWD-6) — Fix getSchedules staleness after save
  - [SWD-7](https://marcusknielsen.atlassian.net/browse/SWD-7) — Unify frontend schedule read path
  - [SWD-4](https://marcusknielsen.atlassian.net/browse/SWD-4) — Add schedule persistence regression tests
