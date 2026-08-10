/**
 * Harness: sysid detail must import chart builders + formatMass from datasets.
 *
 * Without those imports, EKF / open-loop result rendering throws ReferenceError
 * (Safari: "Can't find variable: buildEkfChart") and Stored Datasets setup fails
 * when createCollapsible / makeDataset are missing in sysid-datasets.js.
 *
 * Run: node tests/panel_sysid_detail_imports.harness.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const DETAIL = join(ROOT, 'heatingassistant/app/static/js/identification/sysid-detail.js');
const DATASETS = join(ROOT, 'heatingassistant/app/static/js/identification/sysid-datasets.js');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const detail = readFileSync(DETAIL, 'utf8');
const datasets = readFileSync(DATASETS, 'utf8');

assert(/export function buildEkfChart\b/.test(datasets), 'sysid-datasets.js must export buildEkfChart');
assert(/export function buildOlChart\b/.test(datasets), 'sysid-datasets.js must export buildOlChart');
assert(/export function formatMass\b/.test(datasets), 'sysid-datasets.js must export formatMass');
assert(/from\s*['"]\.\.\/components\/collapsible\.js[^'"]*['"]/.test(datasets), 'sysid-datasets.js must import createCollapsible');
assert(/from\s*['"]\.\.\/components\/time-series-chart\.js[^'"]*['"]/.test(datasets), 'sysid-datasets.js must import makeDataset');
assert(/\bcreateCollapsible\b/.test(datasets.match(/import\s*\{([^}]+)\}\s*from\s*['"]\.\.\/components\/collapsible\.js[^'"]*['"]/)?.[1] || ''), 'createCollapsible must be in collapsible import');
assert(/\bmakeDataset\b/.test(datasets.match(/import\s*\{([^}]+)\}\s*from\s*['"]\.\.\/components\/time-series-chart\.js[^'"]*['"]/)?.[1] || ''), 'makeDataset must be in time-series-chart import');

const dsImport = detail.match(/import\s*\{([^}]+)\}\s*from\s*['"]\.\/sysid-datasets\.js[^'"]*['"]/);
assert(dsImport, 'sysid-detail.js must import from sysid-datasets.js');
for (const name of ['setupDatasetsAndExperiments', 'buildEkfChart', 'buildOlChart', 'formatMass']) {
  assert(new RegExp(`\\b${name}\\b`).test(dsImport[1]), `${name} must be imported in sysid-detail.js`);
}

console.log('panel_sysid_detail_imports.harness.mjs: ok');
