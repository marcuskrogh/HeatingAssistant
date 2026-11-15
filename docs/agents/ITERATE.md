# Iterate: live planner still gets a flat forecast and unscaled power

## Prior work
- Task: [SWD-601](https://marcusknielsen.atlassian.net/browse/SWD-601)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/713 (`4844e11`, 2026.11.5)
- Parent: [SWD-595](https://marcusknielsen.atlassian.net/browse/SWD-595)

## Problem
- On 2026.11.5 the live tick hands the solver a flat outdoor series (the current sensor) and a flat price (the current sensor). Solar still varies.
- Planned watts in the tick row and in `/api/forecasts` are the rated heat-pump power. At startup the same row is 0 W while the restored command is 1.
- A map-shaped forecast is ignored by the outdoor and price builders. A price timestamp without a timezone aborts the disturbance build.
- The bridge opens `manifest.json` on the event loop. One schedule test follows the host timezone.

## Pass criteria
- A time-varying outdoor and price series reaches the real solver kwargs and `/api/forecasts`.
- Tick `q_model_W` and `/api/forecasts` `heating_power` use `power_scale` at startup and after a parameter restore.
- The manifest read is off the event loop. The schedule test uses a fixed timezone.
- Plan acceptance and the 60 s solver limit stay as they are.

## Out of scope
- Merging, releasing, or tagging. Contacting the live house.
- Changing whether a 200-iteration plan is accepted.

## Tracker
- Task: [SWD-602](https://marcusknielsen.atlassian.net/browse/SWD-602)
- Parent: [SWD-595](https://marcusknielsen.atlassian.net/browse/SWD-595)
- Branch: `cursor/swd-602-forecast-power-9bcd`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/714

## Next
In Review — https://github.com/marcuskrogh/HeatingAssistant/pull/714. Do not merge or release.
