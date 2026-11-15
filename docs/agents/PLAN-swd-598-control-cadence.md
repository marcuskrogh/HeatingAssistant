# SWD-598 control cadence

Approved to prepare. Do not merge until Marcus has seen the pull request.

## Scope

One sample is 15 minutes (`update_interval` / `nmpc_period`, default 900 s).

- One planner solve per sample.
- Room-temperature tags update the estimator with the real elapsed time since its last update. They do not solve and they do not write.
- Price tags and other sensor tags only store the new value.
- One actuator write per sample. The sample does not write the previous plan's next step and then the new plan.

Out of scope: model re-identification, an inner PI loop, parameter or tuning changes, and control-law changes beyond this cadence.

## Pass criteria

- Many sensor and price events between samples produce one solve and one write on the sample.
- Sensor and price events alone produce zero solves and zero writes.
- Estimator predict duration is the real elapsed time (37 s, 900 s, and 1800 s after a missed sample), not a fixed 900 s.
- A second call in the same sample does not solve or write again.

## Behaviour Marcus should see

- A missed or late sample is one solve when the ticker wakes. The estimator integrates the whole gap. There is no catch-up solve.
- The first estimate after a start with no saved `ekf_last_t` does not invent a 900 s step. After a restart, `ekf_last_t` is restored, so the first predict is the real gap.
- If a solve is still running at the next sample, that sample updates the estimator and does not start a second solve. The in-flight solve writes once when it finishes.
- A rejected solve does not write. The last command stays. The 5-hour watchdog still writes `u = 0` once when it trips.
- Saving config or a schedule still runs one immediate cycle. That path is not a sensor event.
- A window or door tag can still force the heater off. That is the existing override, not a planner sample.
- Version stays 2026.11.1. This pull request is not a release.
