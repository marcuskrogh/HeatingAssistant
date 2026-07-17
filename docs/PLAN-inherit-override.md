# Implementation plan: Inherit/override data model (SWD-20)

## Summary

- Persist only true overrides: a field is stored iff its value differs from the inherit baseline.
- Absent key (preferred write) or `null` (accepted on read) means **inherit** — runtime always uses the current room/baseline value.
- One-shot upgrade migration strips matching-default fields and mode-irrelevant fields from existing periods.
- Ongoing save stripping is **UI-only**; backend resolves inherit and serializes `Optional` as omit, but does not re-strip matching values.
- Aligns with SWD-23 Overrides UX; implement **SWD-23 + SWD-20 together**.

Parent story: [SWD-18](https://marcusknielsen.atlassian.net/browse/SWD-18)

## Scope / Decisions / Constraints

### Inherit baselines

| Field | Inherit means |
|---|---|
| `setpoint` | Room base setpoint |
| `comfort_offset` | Room comfort offset |
| `tracking_weight` | `1.0` (no change to global Q) |
| `energy_weight` | `1.0` (no change to global R) |
| `frost_protection` | Default frost floor (`12`) |

### Model

- `frost_protection` becomes `Optional` (like other overrides); Off periods with no frost override inherit `12` at resolve time.
- Mode (`comfort` / `off`) is Behaviour, always persisted — not an override.
- Comfort override fields: setpoint, comfort_offset, tracking_weight, energy_weight.
- Off override fields: frost_protection only.
- Write: **omit** inherited keys. Read: missing or `null` → inherit.

### UI persistence rules (every save)

- Overrides section only shows fields the period has actually changed.
- Add-from-picker seeds the control with the inherit baseline; if still equal at Save → drop (back to inherit).
- Persist a field only when value ≠ baseline at save time.
- Keep inactive-mode overrides that still differ (SWD-23 reversibility); do not strip them just because they are irrelevant to the active mode.

### Backend

- Resolve: `None` → current baseline (above table); Off frost floor uses inherited/default frost.
- `period_to_dict` / panel serialize: omit `None` override fields (including frost); stop always writing frost.
- **Do not** strip matching-default values on the service/save path — trust UI.
- One-shot upgrade migration (like SWD-19): for each period, strip (1) fields equal to that room’s current baselines, and (2) fields irrelevant to the period’s **current** mode.

### Out of scope

- Period editor layout / picker chrome → SWD-23
- Drag-reorder, index, overview → SWD-24 / SWD-22 / SWD-21
- YAML `configuration.yaml` schedules (UI-persisted only, same as SWD-19)

## Acceptance criteria

1. New periods save with no override keys unless the user changes a value from the inherit baseline.
2. Changing room base setpoint / comfort offset updates effective behaviour for periods that did not override those fields — without editing the periods.
3. Tracking/energy overrides persist only when ≠ `1.0`; inherit applies multiplier `1.0`.
4. Off periods may omit `frost_protection`; runtime uses default frost floor `12`.
5. Upgrade migration rewrites `persisted_schedules`: matching-default and mode-irrelevant keys removed; user-visible schedule timing/mode unchanged.
6. Inactive-mode differing overrides survive Comfort↔Off round-trips after SWD-23 UX lands.
7. Tests cover: resolve inherit, UI serialize omit/strip, migration strip matching + mode-irrelevant, frost optional Off path.

## Work packages

1. **Optional frost + resolve inherit** ([SWD-37](https://marcusknielsen.atlassian.net/browse/SWD-37)) — `SchedulePeriod.frost_protection: Optional`; resolve Off/Comfort inherit baselines; omit `None` in `period_to_dict`.
2. **Upgrade migration** ([SWD-38](https://marcusknielsen.atlassian.net/browse/SWD-38)) — One-shot strip of matching-default and mode-irrelevant override fields using current room/config baselines.
3. **Panel serialize / editor state** ([SWD-39](https://marcusknielsen.atlassian.net/browse/SWD-39)) — Stop snapshotting room defaults on add; save only differing overrides; seed-then-drop if unaltered; keep inactive-mode differing overrides (with SWD-23).
4. **Tests** ([SWD-40](https://marcusknielsen.atlassian.net/browse/SWD-40)) — Python resolve + migration; JS harness for serialize omit, unaltered drop, and mode-switch retention of inactive overrides.

## Open items

- Float compare: exact equality on stored numbers (implementer default).

## Jira

- Parent: [SWD-18](https://marcusknielsen.atlassian.net/browse/SWD-18)
- Design ticket: [SWD-20](https://marcusknielsen.atlassian.net/browse/SWD-20)
- Related: [SWD-23](https://marcusknielsen.atlassian.net/browse/SWD-23) (joint implement), [SWD-19](https://marcusknielsen.atlassian.net/browse/SWD-19) (types/migration pattern)
- Sub-tasks:
  1. [SWD-37](https://marcusknielsen.atlassian.net/browse/SWD-37) — Optional frost + resolve inherit
  2. [SWD-38](https://marcusknielsen.atlassian.net/browse/SWD-38) — Upgrade migration strip overrides
  3. [SWD-39](https://marcusknielsen.atlassian.net/browse/SWD-39) — Panel serialize and editor inherit state
  4. [SWD-40](https://marcusknielsen.atlassian.net/browse/SWD-40) — Inherit/override regression tests
