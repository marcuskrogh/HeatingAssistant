/**
 * Harness: schedule detail period editor exposes the SWD-23/SWD-39 controls.
 *
 * Run: node tests/panel_period_editor_markup.harness.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const DETAIL = join(ROOT, 'custom_components/heating_assistant/www/js/schedules/schedules-detail.js');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const source = readFileSync(DETAIL, 'utf8');

const sectionOrder = ['>Type<', '>Name<', '>When ', '>Behaviour<', '>Overrides<']
  .map((needle) => source.indexOf(needle));
assert(sectionOrder.every((idx) => idx >= 0), 'period editor must render all required sections');
for (let i = 1; i < sectionOrder.length; i++) {
  assert(sectionOrder[i - 1] < sectionOrder[i], 'period editor sections must be Type -> Name -> When -> Behaviour -> Overrides');
}

assert(source.includes("label: 'Weekly recurring'"), 'type picker must use Weekly recurring label');
assert(source.includes("label: 'Date range'"), 'type picker must use Date range label');
assert(source.includes("label: 'Continuous span'"), 'type picker must use Continuous span label');
assert(source.includes('Start datetime'), 'continuous span must expose start datetime');
assert(source.includes('End datetime'), 'continuous span must expose end datetime');
assert(source.includes('data-action="add-override"'), 'override add picker must be present');
assert(source.includes('data-remove-override'), 'override remove control must be present');
assert(!source.includes('data-field="recurring"'), 'legacy recurring checkbox must be removed');
assert(!source.includes('data-field="all_day"'), 'legacy all-day checkbox must be removed');
assert(!source.includes('Recurring weekly'), 'legacy Recurring weekly label must be removed');

console.log('panel_period_editor_markup.harness.mjs: ok');
