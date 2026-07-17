# PLAN: Drag-to-reorder schedule priority (SWD-24)

Parent: [SWD-18](https://marcusknielsen.atlassian.net/browse/SWD-18) · Task: [SWD-24](https://marcusknielsen.atlassian.net/browse/SWD-24)

## Summary

- On the room schedule **detail** page, users reorder **enabled** periods by drag-and-drop; list order is priority (first-match-wins unchanged).
- Drop **auto-saves** via existing `update_room_schedule` when the operation stays cheap/smooth.
- Reorder is **blocked** while there are unsaved edits; the page and the affected schedule/periods show that clearly.
- No type-based priority tiers; no priority numbers/labels beyond list order.

## Scope

### In

- Active (enabled) period list on room schedule detail only
- Whole-row drag (no dedicated handle)
- Touch: hold-then-move so normal scroll does not grab a row
- On drag start: collapse all open editors, keeping entered values in local state
- Persist order on successful drop
- Re-enabling an inactive period appends it to the end of the active list (lowest priority)
- Inactive remain in a separate section, not in the drag list
- Dirty-state indicators + disable reorder while dirty
- On drop save failure: show an error (leave order as unsaved so Save can retry)

### Out

- Drag on schedules index / other pages
- Hard hierarchy by schedule type
- Explicit priority badges/numbers/helper copy
- Backend matching changes (still first match in list order)
- New priority fields in the period schema

## Decisions

| Topic | Choice |
|-------|--------|
| Persist | Auto-save on every successful drop |
| Dirty | Block reorder; indicate on page and on schedule/period(s) with unsaved changes |
| Dirty includes | Diff vs last save **and** in-progress values in open editors (flush before allowing drag) |
| Drag target | Entire row (avoid starting drag from interactive controls where needed) |
| Touch | Hold-and-move, not immediate grab |
| Expanded editors | Collapse all on drag start; keep values locally |
| Inactive | Separate section; on enable → end of active list |
| Type hierarchy | None — user-controlled list order only |
| Failure | Show error; treat new order as unsaved until saved |
| Priority cue | List order alone |

## Constraints

- Compatible with current `localPeriods` + `update_room_schedule` persistence (order = array order).
- Must not break first-match-wins tests / NOW preview behavior.
- Follow existing panel patterns (vanilla JS shadow DOM, no new build step).

## Acceptance criteria

- User can reorder enabled periods on the detail page; after a successful drop, reload shows the same order and matching follows it.
- Unsaved edits (including open editor fields) disable reorder and are visible at page and schedule/period level; after Save, reorder works again.
- Drag start collapses open editors without losing typed values.
- Scrolling on touch does not accidentally reorder; hold-then-move does.
- Inactive periods are not draggable; enabling one places it last among active until reordered.
- Failed drop save shows an error and does not leave the UI believing the server accepted the order.
- Automated coverage for reorder → persist → first-match behavior (and dirty gating as practical in harness).

## Work packages

1. **Dirty-state gating & indicators** — Track unsaved page/period state (incl. open editors); surface indicators; disable reorder while dirty.
2. **Drag-and-drop reorder UI** — Active-list DnD, whole-row drag, hold-and-move on touch, collapse-on-drag-start, enable→append-to-end.
3. **Persist on drop & failure UX** — Auto-save full list order on drop; error + unsaved state on failure; keep first-match-wins semantics.
4. **Tests** — Panel harness and/or unit coverage for order persistence, dirty block, enable-to-end, matching after reorder.

## Open items

- Exact dirty indicator chrome (banner vs row styling) — implementer follows existing panel visual language.
- Precise hold duration / move threshold for touch — tune for “intuitive, no accidental grab.”

## Jira

- Parent: [SWD-18](https://marcusknielsen.atlassian.net/browse/SWD-18)
- Design ticket: [SWD-24](https://marcusknielsen.atlassian.net/browse/SWD-24)
- Subtasks:
  - [SWD-49](https://marcusknielsen.atlassian.net/browse/SWD-49) — Dirty-state gating & indicators
  - [SWD-47](https://marcusknielsen.atlassian.net/browse/SWD-47) — Drag-and-drop reorder UI
  - [SWD-46](https://marcusknielsen.atlassian.net/browse/SWD-46) — Persist on drop & failure UX
  - [SWD-48](https://marcusknielsen.atlassian.net/browse/SWD-48) — Tests for reorder, dirty gate, enable-to-end
