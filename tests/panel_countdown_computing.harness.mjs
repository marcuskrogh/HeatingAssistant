/**
 * Wrap vs flag overlay for NEXT CONTROL / NEXT NMPC.
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
const period = 3600;
const dt = 900;

function state(attrs, enabled = true) {
  return {
    [SUMMARY]: { state: '1', attributes: { system_enabled: enabled } },
    [MPC]: {
      state: '0.2',
      attributes: {
        dt_s: dt,
        nmpc_period_s: period,
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

const nmpc = countdown.COUNTDOWN_NMPC;
const control = countdown.COUNTDOWN_CONTROL;

assert(
  countdown.countdownIsComputing(state({ nmpc_computing: true }), nmpc, (epoch + 100) * 1000),
  'nmpc_computing flag must show overlay',
);
assert(
  !countdown.countdownIsComputing(state({ nmpc_computing: true }), control, (epoch + 100) * 1000),
  'NMPC flag must not force CONTROL overlay',
);
assert(
  countdown.countdownIsComputing(state({ control_computing: true }), control, (epoch + 100) * 1000),
  'control_computing flag must show overlay',
);

const justWrapped = (epoch + 1) * 1000;
assert(
  countdown.countdownIsComputing(state({}), nmpc, justWrapped),
  'NMPC wrap with stale nmpc_result_ts must show overlay without a poll',
);
assert(
  countdown.countdownIsComputing(state({}), control, justWrapped),
  'CONTROL wrap with stale last_control_ran_ts must show overlay',
);

const threeSecondsIn = (epoch + 3) * 1000;
assert(
  countdown.countdownIsComputing(state({}), nmpc, threeSecondsIn),
  'NMPC overlay must still show a few seconds after wrap (poll not required)',
);

const fifteenSecondsIn = (epoch + 15) * 1000;
assert(
  !countdown.countdownIsComputing(state({}), nmpc, fifteenSecondsIn),
  'NMPC wrap overlay must clear after the poll-gap cap when computing is false',
);
assert(
  countdown.countdownIsComputing(
    state({ nmpc_computing: true }),
    nmpc,
    fifteenSecondsIn,
  ),
  'NMPC flag must keep overlay after wrap catch-up',
);
assert(
  !countdown.countdownIsComputing(state({}), control, fifteenSecondsIn),
  'CONTROL wrap overlay must clear after the short P cap',
);

assert(
  !countdown.countdownIsComputing(
    state({ nmpc_result_ts: undefined }),
    nmpc,
    fifteenSecondsIn,
  ),
  'missing nmpc_result_ts must not keep overlay after catch-up',
);

assert(
  !countdown.countdownIsComputing(
    state({ nmpc_result_ts: epoch + 0.2 }),
    nmpc,
    justWrapped,
  ),
  'NMPC overlay clears when result stamp is in the current slot',
);
assert(
  !countdown.countdownIsComputing(
    state({ last_control_ran_ts: epoch + 0.2 }),
    control,
    justWrapped,
  ),
  'CONTROL overlay clears when last_control_ran_ts is in the current slot',
);

assert(
  !countdown.countdownIsComputing(state({}), nmpc, (epoch + 700) * 1000),
  'NMPC wrap overlay must not stick past the duration cap',
);

assert(
  !countdown.countdownIsComputing(state({ nmpc_computing: true }, false), nmpc, justWrapped),
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
