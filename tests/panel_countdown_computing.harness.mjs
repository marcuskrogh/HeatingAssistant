/**
 * Wrap vs flag overlay for the shared Next Compute ring.
 * Run: node tests/panel_countdown_computing.harness.mjs
 */
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const countdown = await import(
  `${pathToFileURL(join(ROOT, 'heatingassistant/app/static/js/components/countdown.js')).href}`
);

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg);
    process.exit(1);
  }
}

const MPC = 'sensor.heating_assistant_mpc_performance';
const SUMMARY = 'sensor.heating_assistant_system_summary';
const epoch = 1_700_000_000;
const dt = 900;

function state(attrs, enabled = true) {
  return {
    [SUMMARY]: { state: '1', attributes: { system_enabled: enabled } },
    [MPC]: {
      state: '0.2',
      attributes: {
        dt_s: dt,
        nmpc_period_s: dt,
        mpc_mode: 'nmpc',
        last_nmpc_ts: epoch,
        nmpc_computing: false,
        control_computing: false,
        nmpc_result_ts: epoch - 10,
        last_control_ran_ts: epoch - 10,
        last_nmpc_duration_s: 20,
        ...attrs,
      },
    },
  };
}

const spec = countdown.COUNTDOWN_COMPUTE;

assert(
  countdown.countdownIsComputing(state({ nmpc_computing: true }), spec, (epoch + 100) * 1000),
  'nmpc_computing flag must show overlay',
);
assert(
  countdown.countdownIsComputing(state({ control_computing: true }), spec, (epoch + 100) * 1000),
  'control_computing flag must show overlay',
);

const justWrapped = (epoch + 1) * 1000;
assert(
  countdown.countdownIsComputing(state({}), spec, justWrapped),
  'wrap with stale result stamp must show overlay without a poll',
);

const threeSecondsIn = (epoch + 3) * 1000;
assert(
  countdown.countdownIsComputing(state({}), spec, threeSecondsIn),
  'overlay must still show a few seconds after wrap (poll not required)',
);

const fifteenSecondsIn = (epoch + 15) * 1000;
assert(
  !countdown.countdownIsComputing(state({}), spec, fifteenSecondsIn),
  'wrap overlay must clear after the poll-gap cap when computing is false',
);
assert(
  countdown.countdownIsComputing(
    state({ nmpc_computing: true }),
    spec,
    fifteenSecondsIn,
  ),
  'nmpc flag must keep overlay after wrap catch-up',
);
assert(
  countdown.countdownIsComputing(
    state({ control_computing: true }),
    spec,
    fifteenSecondsIn,
  ),
  'control flag must keep overlay after wrap catch-up',
);

assert(
  !countdown.countdownIsComputing(
    state({ nmpc_result_ts: epoch + 0.2 }),
    spec,
    justWrapped,
  ),
  'Nonlinear overlay clears when nmpc_result_ts is in the current slot',
);
assert(
  !countdown.countdownIsComputing(
    state({ mpc_mode: 'linear', last_control_ran_ts: epoch + 0.2 }),
    spec,
    justWrapped,
  ),
  'Linear overlay clears when last_control_ran_ts is in the current slot',
);

assert(
  !countdown.countdownIsComputing(state({ nmpc_computing: true }, false), spec, justWrapped),
  'stopped system must not show overlay',
);

const host = { classList: new Set(), closest() { return this; } };
host.classList.toggle = function toggle(name, on) {
  if (on) this.add(name);
  else this.delete(name);
};
const inner = {
  classList: new Set(),
  closest(sel) { return sel === '.kpi-expand' ? host : null; },
};
inner.classList.toggle = function toggle(name, on) {
  if (on) this.add(name);
  else this.delete(name);
};
countdown.setCountdownComputing(inner, true);
assert(inner.classList.has('countdown--computing'), 'inner card must get computing class');
assert(host.classList.has('countdown--computing'), 'kpi-expand wrap must get computing class');

console.log('ok');
