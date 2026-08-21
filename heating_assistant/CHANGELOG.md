# Changelog

User-facing notes for the HeatingAssistant Home Assistant App. Supervisor
shows the section whose heading matches the version being installed. Headings
must be the exact calendar version on its own line.

# 2026.08.27

- The 15-minute control countdown and the two-hour planner countdown now
  stay on the same Start clock, so they meet at every planner slot. While
  a new plan is being computed, heaters keep following the previous plan
  until the next 15-minute tick, and the compute KPI cards show a loading
  animation until the new values arrive. Room Forecast and Planned Power
  refresh when that plan lands, even if heaters wait for the next tick.

# 2026.08.26

- Room-view Forecast and Planned Power now follow the remaining two-hour
  plan from the current temperature: power still holds for two hours, and
  the temperature path is a resimulation of that leftover plan rather than
  a replay of the first interval or a frozen copy of the planner's air path.

# 2026.08.25

- The two-hour planner countdown now stays on the clock from when you press
  Start. Finishing a plan no longer restarts that timer, so heating cycles
  do not drift later and later.
- Room view Forecast and Planned Power now stay on the two-hour planner
  path after each 15-minute control tick, matching Controller Tuning
  preview: power holds for two hours, and the temperature forecast is
  the planner trajectory rather than a jittery short-step re-rollout.

# 2026.08.24

- Room view now plots the two-hour planner's path (Forecast and Planned
  Power) when that plan is better than leaving the heater off. A useful
  cooling plan is no longer dropped, so the next-day heat spike is not
  shown as if it were the optimum.

# 2026.08.23

- Heating Assistant now has its own icon in the App store and in
  Home Assistant Settings.

# 2026.08.22

- Heaters and heat pumps now heat or cool on the 15-minute loop when the
  room is already outside the comfort band, instead of waiting for the
  two-hour planner and holding the current temperature.
- After the two-hour planner accepts a path, climate and number commands
  update immediately instead of waiting for the next 15-minute tick.

# 2026.08.21

- Heat/cool planned power now shows cooling (negative kW) as soon as the
  two-hour planner finishes, instead of staying at zero through a heat spike.

# 2026.08.20

- When a heat pump can cool, the two-hour planner now uses negative power
  instead of sitting at zero.
- Overview and room pages now show two countdowns: the 15-minute control
  cycle and the two-hour planner cycle.

# 2026.08.11

- Heating now uses a two-rate planner: a slow nonlinear plan every two hours
  and a fast tracker every 15 minutes. Heaters stay at the last good plan if a
  solve fails, and switch off with a Home Assistant notice after five hours
  without a usable plan.

# 2026.08.10

- Parameter estimation now says one day can cover every recommended category,
  but several days usually give a more reliable model.

# 2026.08.9

- System Status no longer keeps a sensor warning when Home Assistant already
  has valid measurements for the configured tags.

# 2026.08.8

- Restart required is a Settings repair (same section as other apps), not a
  HeatingAssistant update. This release also removes a leftover update card.
  After you restart Home Assistant, it goes away.

# 2026.08.7

- Changelog text on the App update dialog.
- After a thin-bridge sync, Settings shows Restart required until you restart
  Home Assistant Core.

# 2026.08.6

- Parameter estimation keeps thermal mass near the selected room size.
