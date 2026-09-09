# Changelog

User-facing notes for the HeatingAssistant Home Assistant App. Supervisor
shows the section whose heading matches the version being installed. Headings
must be the exact calendar version on its own line.

# 2026.09.20

- Nonlinear model predictive control uses the same sample interval and
  look-ahead as Linear. The old two-hour plan period and held-command
  grid are gone; a new nonlinear plan is solved each sample (typically
  tens of seconds of compute inside a 15 minute sample).

# 2026.09.19

- Controller Tuning planner cards show Linear or Nonlinear, then model
  predictive control, with a distinct in-use vs selected state. Switching
  the live solver still requires Apply Changes. Nonlinear timing uses a
  sample interval instead of a substep count. Linear-only cost weights
  (setpoint pull, heater-effort, linear band penalty, end-of-horizon)
  are hidden when Nonlinear is selected.

# 2026.09.18

- Controller Tuning lets you choose linear or nonlinear model predictive
  control. Shared weights stay in place when you switch, and each planner
  keeps its own timing settings.
- Nonlinear control holds the planned heater command between NMPC solves.
  The extra tracking loop on the planned temperature is gone, and the room
  Regulator Load card is removed. Heating units still map that command to a
  climate setpoint from the unit's internal temperature. The old heater
  P-gain field for the two-layer tracker is gone.

# 2026.09.17

- Next Control and Next NMPC rings stay in the computing overlay for the
  whole solver run, and load values, optimal trajectories, and plots refresh
  as soon as that solver finishes instead of waiting for the next dashboard
  cycle.

# 2026.09.16

- Next Control and Next NMPC rings no longer flash a computing overlay when
  the solver is idle after a countdown wrap. The spin still appears as soon
  as a compute starts, including in the first seconds after the timer resets.

# 2026.09.15

- Overview health now reflects only the sensors currently configured. Removed
  sensors no longer keep a leftover warning after they are taken out of the
  setup.

# 2026.09.14

- Config pages show the device and entity name on each sensor chip, so sensors
  that share a short name like TempPV can be told apart.

# 2026.09.13

- The Heating Assistant panel loads again after the last progress-popup
  change. A JavaScript syntax error had blocked the whole UI.

# 2026.09.12

- After automatic parameter estimation finishes, the progress popup stays
  open with a short reason the fit stopped (for example maximum iterations
  reached). An X in the upper right closes it, and stops a running fit
  without applying new parameters.

# 2026.09.11

- Automatic parameter estimation now runs one model fit from your current
  settings instead of several restarts. The progress plot no longer jumps
  back to a poor error every few dozen evaluations. Fit quality is unchanged.

# 2026.09.10

- Ingress type and shared chrome now follow one scale across pages and
  popups. Room-view plots stay the visual guide; other plots, including the
  parameter-estimation progress canvas, match those line widths and ticks.
- While automatic parameter estimation runs, a popup stays on screen (including
  on a phone) with RMS error against a 1 °C tolerance and a log plot of
  normalised fit quality. Time left sits in a thin footer. If the popup is
  taller than the screen, scroll inside it.

# 2026.09.9

- Next Control and Next NMPC rings show a spinning overlay and a computing
  label while that solver is running, including right after the countdown
  wraps, instead of staying idle until the next dashboard refresh.

# 2026.09.8

- Room schedule pages show each configured comfort period as a card you
  can open and edit. The inactive list stays hidden when it is empty.

# 2026.09.7

- Parameter estimation now shows a live popup while a fit is running: remaining
  time against the configured maximum, fit error, and a plot of that error
  moving toward zero. If the time limit is reached, previous parameters stay
  in place.

# 2026.09.6

- Solar-gain smoothing now continues after an App restart. The lag no
  longer reseeds to a sudden sky jump on the first sample.

# 2026.09.5

- Solar gain no longer jumps in one sample when cloud cover or a solar
  irradiance forecast steps. The watt trace after the usual sky model is
  low-pass filtered (default 30 min; set under Environment → Solar model)
  so history, NOW, and the forecast change gradually. Set the time
  constant to 0 to disable smoothing.

# 2026.09.4

- Parameter estimation now scores how well the model predicts indoor air over
  the same look-ahead the controller uses, instead of a long tiled open-loop
  window. If a run exceeds the time limit (default one minute), the previous
  parameters stay in place. Raise that limit under Configuration → Advanced,
  or use a shorter dataset.

# 2026.09.3

- Parameter Estimation open-loop and EKF reconstruction start the wall
  node from the fitted initial temperature of the current parameter set
  when you load a dataset that was used to estimate those parameters.
  Otherwise the wall initial temperature is fitted on the plotted window.
  The wall field says which of those two sources was used.

# 2026.09.2

- KPI cards at a glance are the original compact gauges again. The
  description sentence appears only after you open a card.

# 2026.09.1

- Expanding a KPI card moves that same card to the top of the section and
  grows it open. The page stays on the open card. Overview SYSTEM STATUS
  shows NMPC Load as last NMPC solve versus 10% of the NMPC period. The
  room view shows Regulator Load as last P-cycle versus 2 seconds. Open
  cards put the meaning under a Description heading and separate values
  with light row dividers.

# 2026.09.0

- Overview and room KPI cards include a short sentence saying what each
  figure is, both at a glance and when the card is open. The open card still
  lists the absolute values. First September App version.

# 2026.08.41

- Overview and room KPI cards stay the same at a glance. Click one to move
  it to the top of that section and open a larger card: the live value and
  animation on top, and a darker panel below with what the number means and
  the absolute figures. MPC LOAD still shows a percent; the open card lists
  last P-cycle time and last NMPC solve time in seconds.

# 2026.08.40

- Room Forecast shows the last NMPC temperature trajectory — the air
  path under two-hour heater holds, including the solver's fast-grid
  substeps, not a single constant. Weather updates no longer redraw a
  free-response that looks like the house is leaving the comfort zone.
  The fast loop tracks that same trajectory. Planned Power stays leftover
  planner power, which still steps only every two hours.

# 2026.08.39

- Solar gain on a cloudy day follows the weather cloud cover again, including
  the historical trace left of NOW. Missing current cloud percent no longer
  leaves that sample on an unattenuated clear-sky path while the forecast is
  scaled. An unused solar-irradiance tag is ignored.

# 2026.08.38

- Parameter Estimation Heating Input now plots cooling at the configured
  cooling capacity. Full cool no longer shows as minus the heating max
  (for example −7000 W when cooling is about −3500 W).

# 2026.08.37

- Room plots show the accepted heating plan again, and heaters step to
  that plan. A missing import had been discarding every new two-hour
  plan, so Forecast looked like the heater was off, Planned Power stayed
  at 0 kW, and the heater never received a command.

# 2026.08.36

- Running recommended parameter estimation on a week of stored data no
  longer fails with Load failed. The fit now runs in the background
  while the page waits for the result.

# 2026.08.35

- Room plots keep the planned temperature and heating trajectories, energy
  price forecast, and outdoor forecast after Home Assistant refreshes its
  entity list. Those series no longer go missing or flatten to a single value.

# 2026.08.34

- The Heating Assistant panel loads again. A missing room-chart helper had
  stopped the app on a load error after the last update.

# 2026.08.33

- During night or other planner-off intervals, heaters stay off unless room
  air drifts more than 1 °C from the planned temperature. Small preheat
  commands are still followed. Both the off-threshold and the 1 °C band are
  on Controller Tuning.

# 2026.08.32

- Room DISTURBANCES plots historical outdoor temperature and solar gain as
  solid lines again, so the chart is easier to read than the previous point
  cloud. Forecasts stay dashed. Grey outdoor and yellow solar colours are
  unchanged.

# 2026.08.31

- When a new two-hour heating plan is accepted, heaters step to that plan
  immediately instead of waiting for the next 15-minute tick, so commanded
  power no longer looks like a slow lag on each planner update.

# 2026.08.30

- Room-view Forecast no longer reuses current sunlight when a future solar
  irradiance step is missing; it uses the sky and cloud model instead.

# 2026.08.29

- Room-view Forecast re-simulates leftover planner power from the current
  estimator using the same implicit-Euler substeps as the optimiser, so
  updated weather still moves the plot, at the same accuracy the planner
  used. Controller Tuning preview with matching weights uses that series.

# 2026.08.28

- While a plan or control tick is computing, the matching countdown ring
  (next two-hour plan or next 15-minute control) shows a loading animation.
  Live values such as heating power stay as they are.

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
