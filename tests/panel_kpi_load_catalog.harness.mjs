/**
 * Execute NMPC / Regulator load detail payloads from the production catalog.
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
      nmpc_period_s: 7200,
      nmpc_computing: false,
      control_computing: false,
      dt_s: 10,
      nmpc_result_ts: 1700000000,
      last_control_ran_ts: 1700000100,
    },
  },
};

const nmpc = catalog.nmpcLoadDetail(state);
assert(nmpc.description.includes('NMPC only'), 'NMPC expand copy must stay NMPC-only');
assert(nmpc.sections.length === 1, 'NMPC expand must have one section');
assert(nmpc.sections[0].title === 'NMPC', 'NMPC expand section title must be NMPC');
assert(!nmpc.sections.some((section) => section.title === 'Regulator'), 'NMPC expand must not include Regulator rows');
const nmpcLoad = nmpc.sections[0].rows.find((row) => row.label === 'Load');
assert(nmpcLoad.value === '3%', '24.7 s of a 720 s NMPC budget must paint 3%');
const nmpcBudget = nmpc.sections[0].rows.find((row) => row.label === 'Load budget');
assert(nmpcBudget.value.includes('720'), 'NMPC budget row must show 10% of the period');

const regulator = catalog.regulatorLoadDetail(state);
assert(regulator.description.includes('P-cycle'), 'Regulator expand copy must describe the P-cycle');
assert(regulator.sections.map((section) => section.title).join(',') === 'Regulator,NMPC', 'Regulator expand must list Regulator then NMPC');
const regLoad = regulator.sections[0].rows.find((row) => row.label === 'Load');
assert(regLoad.value === '9%', '0.18 s of a 2 s regulator budget must paint 9%');
assert(regulator.sections[1].title === 'NMPC', 'Regulator expand must still list NMPC figures');

console.log('panel_kpi_load_catalog.harness.mjs: ok');
