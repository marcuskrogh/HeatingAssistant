# Iterate: restore the constant climate offset hold

## Prior work
- Removed in [SWD-262](https://marcusknielsen.atlassian.net/browse/SWD-262) (`ef816f82`), added in `8bd3435b`
- Sample contract: [SWD-598](https://marcusknielsen.atlassian.net/browse/SWD-598) / PR #710
- Parent: [SWD-595](https://marcusknielsen.atlassian.net/browse/SWD-595)

## Problem
- A climate unit written once per sample lets delivered power fall as its internal temperature rises toward a fixed target.
- Marcus chose the constant offset regulator until there is a power measurement.

## Pass criteria
- Between samples, rewrite the climate target as internal temperature plus the planner's commanded offset, only when the internal temperature changes.
- Stay on the 0.5 °C step and inside the sigmoid / `delta_sat` bound. Never exceed the commanded offset.
- At most one hold write per 60 s per zone, and none when the rounded target is unchanged.
- `climate_offset_hold` defaults on. Off leaves the sample-only write.
- No write when the command is stale, the entity is unavailable or unknown, HVAC is off or cool, or the controller is disabled or in fallback.
- A sample is still one estimator step, one solve, and one actuator write. The hold is a separate counted path.

## Out of scope
- Plan acceptance and the 60 s solver limit.
- Merging, releasing, or tagging.

## Tracker
- Task: [SWD-603](https://marcusknielsen.atlassian.net/browse/SWD-603)
- Parent: [SWD-595](https://marcusknielsen.atlassian.net/browse/SWD-595)
- Branch: `cursor/swd-603-offset-hold-9bcd`
- Rebased onto main `9281ac6` (2026.11.6). Version stays 2026.11.7.
