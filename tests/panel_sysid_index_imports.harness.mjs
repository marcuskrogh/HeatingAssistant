/**
 * Harness: system identification index must import formatNumber/modelFitLabel.
 *
 * Without those imports, buildTiles() throws ReferenceError after clearing the
 * room grid whenever any room exists — so the identification page stays blank
 * and rooms cannot be selected.
 *
 * Run: node tests/panel_sysid_index_imports.harness.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const INDEX = join(
  ROOT,
  'custom_components/heating_assistant/www/js/identification/sysid-index.js',
);
const UTILS = join(
  ROOT,
  'custom_components/heating_assistant/www/js/utils.js',
);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const source = readFileSync(INDEX, 'utf8');
const utils = readFileSync(UTILS, 'utf8');

assert(
  /export function formatNumber\b/.test(utils),
  'utils.js must export formatNumber',
);
assert(
  /export function modelFitLabel\b/.test(utils),
  'utils.js must export modelFitLabel',
);

// Import binding must include both helpers used when building room cards.
const utilsImport = source.match(
  /import\s*\{([^}]+)\}\s*from\s*['"]\.\.\/utils\.js[^'"]*['"]/,
);
assert(utilsImport, 'sysid-index.js must import from utils.js');
const imported = utilsImport[1];
assert(/\bformatNumber\b/.test(imported), 'formatNumber must be imported');
assert(/\bmodelFitLabel\b/.test(imported), 'modelFitLabel must be imported');

// Card body still uses both helpers for fit badge / KPI values.
assert(
  /\bmodelFitLabel\s*\(/.test(source),
  'card body must call modelFitLabel',
);
assert(
  /\bformatNumber\s*\(/.test(source),
  'card body must call formatNumber',
);

console.log('panel_sysid_index_imports.harness.mjs: ok');
