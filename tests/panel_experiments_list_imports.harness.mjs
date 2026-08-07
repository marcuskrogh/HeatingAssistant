/**
 * Harness: schedule-detail experiment list must import fmtExpDate/fmtExpTime (SWD-59).
 *
 * Without those imports, renderList() throws ReferenceError after clearing the
 * container whenever any experiment exists — so the EXPERIMENTS section stays blank.
 *
 * Run: node tests/panel_experiments_list_imports.harness.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const EXPERIMENTS = join(
  ROOT,
  'heatingassistant/app/static/js/schedules/schedules-experiments.js',
);
const SHARED = join(
  ROOT,
  'heatingassistant/app/static/js/schedules/schedules-shared.js',
);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const source = readFileSync(EXPERIMENTS, 'utf8');
const shared = readFileSync(SHARED, 'utf8');

assert(
  /export function fmtExpDate\b/.test(shared),
  'schedules-shared.js must export fmtExpDate',
);
assert(
  /export function fmtExpTime\b/.test(shared),
  'schedules-shared.js must export fmtExpTime',
);

// Import binding must include both helpers used in the card body template.
const sharedImport = source.match(
  /import\s*\{([^}]+)\}\s*from\s*['"]\.\/schedules-shared\.js[^'"]*['"]/,
);
assert(sharedImport, 'schedules-experiments.js must import from schedules-shared.js');
const imported = sharedImport[1];
assert(/\bfmtExpDate\b/.test(imported), 'fmtExpDate must be imported (SWD-59)');
assert(/\bfmtExpTime\b/.test(imported), 'fmtExpTime must be imported (SWD-59)');

// Card body still uses both helpers for window start/end display.
assert(
  source.includes('fmtExpDate(exp.start_ts)') && source.includes('fmtExpTime(exp.start_ts)'),
  'card body must format window start with fmtExpDate/fmtExpTime',
);
assert(
  source.includes('fmtExpDate(exp.end_ts)') && source.includes('fmtExpTime(exp.end_ts)'),
  'card body must format window end with fmtExpDate/fmtExpTime',
);

console.log('panel_experiments_list_imports.harness.mjs: ok');
