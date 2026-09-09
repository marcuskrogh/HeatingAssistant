/**
 * Execute MPC load detail payloads from the production catalog.
 * Run: node tests/panel_kpi_load_catalog.harness.mjs
 */
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const catalog = await import(
  `${pathToFileURL(join(ROOT, 'heatingassistant/app/static/js/kpi-detail-catalog.js')).href}`
);

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg);
    process.exit(1);
  }
}

const MPC = 'sensor.heating_assistant_mpc_performance';
const state = {
  [MPC]: {
    state: '0.18',
    attributes: {
      last_nmpc_duration_s: 24.7,
      last_planner_duration_s: 24.7,
      nmpc_period_s: 7200,
      dt_s: 7200,
      mpc_mode: 'nmpc',
      nmpc_computing: false,
      control_computing: false,
      nmpc_result_ts: 1700000000,
      last_control_ran_ts: 1700000100,
    },
  },
};

const mpc = catalog.mpcLoadDetail(state);
assert(mpc.description.includes('Linear and Nonlinear'), 'MPC expand copy must be mode-general');
assert(!mpc.sections, 'MPC expand uses rows, not NMPC-only sections');
const load = mpc.rows.find((row) => row.label === 'Load');
assert(load.value === '3%', '24.7 s of a 720 s budget must paint 3%');
const budget = mpc.rows.find((row) => row.label === 'Load budget');
assert(budget.value.includes('720'), 'budget row must show 10% of the sample interval');
assert(mpc.rows.some((row) => row.label === 'Sample interval'), 'sample interval row must be present');

console.log('panel_kpi_load_catalog.harness.mjs: ok');
