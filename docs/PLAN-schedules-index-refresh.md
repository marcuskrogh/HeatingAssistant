# PLAN: Schedules index refresh (SWD-22)

Parent: [SWD-18](https://marcusknielsen.atlassian.net/browse/SWD-18) · Task: [SWD-22](https://marcusknielsen.atlassian.net/browse/SWD-22)

## Summary

Refresh period previews on the schedules index and room schedule detail (collapsed) for the three schedule types. Each preview shows short type label + name + timing + mode. Keep NOW/NEXT badges and boxed period separation. Move past and disabled periods into a collapsed Inactive section so they stay editable for reuse (e.g. vacation date edits).

## Acceptance criteria

* Index and room-detail collapsed rows show type, name, timing, and mode for all three types
* All-day periods show "all day" without clock times
* NOW/NEXT work across types (NOW = currently matching; NEXT = soonest upcoming among active periods)
* Boxed separation between periods preserved
* Past and disabled periods appear under a collapsed Inactive section; upcoming continuous remain in the main list
* Inactive periods remain editable/deletable for reuse
* Panel/unit tests cover preview strings per type, all-day, NOW/NEXT across types, and inactive bucketing

## Decisions

* Type labels (short): Weekly / Date range / Continuous
* Preview content: type + name + timing + mode
* All-day weekly/date-range: show "all day" (no clock times)
* Non-all-day timing:
  * weekly = weekdays + start–end time
  * date range = start–end dates + daily window
  * continuous = start–end datetimes
* Same preview on schedules index and room detail collapsed summaries
* Inactive = fully past or disabled; collapsed by default on both surfaces
* Upcoming continuous stay in the main (active) list
* NOW/NEXT only consider active (non-inactive) periods
* Overrides not shown in preview

## Inactive rules

| Type | Inactive when |
|------|----------------|
| Any | `enabled === false` |
| Continuous | `end_at` ≤ now (local datetime string compare) |
| Date range | `end_date` < today's date string |
| Weekly | never "past" by date alone (only if disabled) |

## NOW / NEXT

* **NOW** — currently matching among active periods (`periodMatchesNow`)
* **NEXT** — soonest upcoming start among active periods that are not currently matching, across all three types:
  * weekly window: next start on a matching weekday
  * weekly all_day: next matching day at 00:00 if not matching today
  * date_range window: next daily window start within the remaining date range
  * date_range all_day: next calendar day in range at 00:00 if not currently matching
  * continuous: `start_at` if in the future

## Out of scope

* Drag-to-reorder (SWD-24)
* Overview / room time-axis visualization (SWD-21)
* Period editor layout (SWD-23) — expanded editor stays as-is

## Open items

* Locale-aware date/weekday formatting (follow existing HA/locale patterns)
* SWD-24: inactive periods should not participate in active-list drag reorder

## Sub-tasks

1. [SWD-42](https://marcusknielsen.atlassian.net/browse/SWD-42) — Shared period preview helper
2. [SWD-44](https://marcusknielsen.atlassian.net/browse/SWD-44) — Schedules index wiring
3. [SWD-43](https://marcusknielsen.atlassian.net/browse/SWD-43) — Room detail collapsed summaries
4. [SWD-41](https://marcusknielsen.atlassian.net/browse/SWD-41) — Inactive section
5. [SWD-45](https://marcusknielsen.atlassian.net/browse/SWD-45) — Schedules preview and inactive tests

## Hotspots

* `custom_components/heating_assistant/www/js/schedule-utils.js`
* `…/schedules/schedules-index.js`
* `…/schedules/schedules-detail.js`
* `…/schedules/schedules-shared.js` (`makePeriodRow` / `periodRowHtml`)
* `www/css/pages/schedules.css`
* Shared consumers via `periodRowHtml`: `schedule-overview.js`, `room-climate-tile.js`
