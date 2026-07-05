/**
 * Regression harness: schedules-detail must not import and redeclare the same
 * binding (patchStateSchedule), which breaks module load with:
 *   Cannot declare a function that shadows a let/const/class/function variable
 *
 * Run: node tests/panel_schedules_module.harness.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const DETAIL_PATH = join(
  ROOT,
  'custom_components/heating_assistant/www/js/schedules/schedules-detail.js',
);
const SHARED_PATH = join(
  ROOT,
  'custom_components/heating_assistant/www/js/schedules/schedules-shared.js',
);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function parseNamedImports(source) {
  const imports = new Set();
  const importRe = /import\s*\{([^}]+)\}\s*from\s*['"][^'"]+['"]/g;
  let match;
  while ((match = importRe.exec(source)) !== null) {
    for (const part of match[1].split(',')) {
      const name = part.trim().split(/\s+as\s+/).pop().trim();
      if (name) imports.add(name);
    }
  }
  return imports;
}

function parseExportedFunctions(source) {
  const names = new Set();
  const exportFnRe = /export\s+function\s+(\w+)/g;
  let match;
  while ((match = exportFnRe.exec(source)) !== null) {
    names.add(match[1]);
  }
  return names;
}

const detailSource = readFileSync(DETAIL_PATH, 'utf8');
const sharedSource = readFileSync(SHARED_PATH, 'utf8');

const detailImports = parseNamedImports(detailSource);
const detailExports = parseExportedFunctions(detailSource);
const sharedExports = parseExportedFunctions(sharedSource);

for (const name of detailImports) {
  assert(
    !detailExports.has(name),
    `schedules-detail.js must not export '${name}' when it is also imported`,
  );
}

assert(
  sharedExports.has('patchStateSchedule'),
  'schedules-shared.js must export patchStateSchedule',
);
assert(
  detailImports.has('patchStateSchedule'),
  'schedules-detail.js must import patchStateSchedule from schedules-shared.js',
);

console.log('panel schedules module harness: ok');
