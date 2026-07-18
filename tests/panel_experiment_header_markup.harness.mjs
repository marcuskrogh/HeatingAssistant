/**
 * Harness: experiment card/form headers use the SWD-51/SWD-55 two-cluster layout.
 *
 * Run: node tests/panel_experiment_header_markup.harness.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const EXPERIMENTS = join(
  ROOT,
  'custom_components/heating_assistant/www/js/schedules/schedules-experiments.js',
);
const EXPERIMENT_UTILS = join(
  ROOT,
  'custom_components/heating_assistant/www/js/experiment-utils.js',
);
const SCHEDULES_CSS = join(
  ROOT,
  'custom_components/heating_assistant/www/css/pages/schedules.css',
);
const INDUSTRIAL_CSS = join(
  ROOT,
  'custom_components/heating_assistant/www/css/industrial.css',
);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const source = readFileSync(EXPERIMENTS, 'utf8');
const utils = readFileSync(EXPERIMENT_UTILS, 'utf8');
const css = readFileSync(SCHEDULES_CSS, 'utf8');
const industrialCss = readFileSync(INDUSTRIAL_CSS, 'utf8');

// SWD-55: collapsed experiment card header uses two clusters.
assert(
  source.includes('schedule-form__period-header-main'),
  'experiment card header must contain a header-main cluster',
);
assert(
  source.includes('schedule-form__period-header-actions'),
  'experiment card header must contain a header-actions cluster',
);

// Actions cluster: cancel/delete before expand-chevron when present.
const actionsMatch = source.match(
  /schedule-form__period-header-actions[\s\S]*?(?:data-cancel|data-delete)[\s\S]*?schedule-form__expand-chevron[\s\S]*?<\/div>/,
);
assert(actionsMatch, 'header-actions must contain cancel/delete then expand-chevron');
assert(
  actionsMatch[0].indexOf('data-delete') < actionsMatch[0].indexOf('schedule-form__expand-chevron')
    || actionsMatch[0].indexOf('data-cancel') < actionsMatch[0].indexOf('schedule-form__expand-chevron'),
  'cancel/delete must appear before expand-chevron in actions',
);

// Form header pins discard in the actions cluster.
assert(
  /schedule-form__period-header-actions[\s\S]*?id="ef-discard"/.test(source),
  'new/edit form header must pin discard (ef-discard) in header-actions',
);

// Obsolete two-row experiment header markup must be gone.
assert(
  !source.includes('exp-card__header-row'),
  'obsolete .exp-card__header-row must be removed from schedules-experiments.js',
);
assert(
  !source.includes('exp-card__header-window'),
  'obsolete .exp-card__header-window must be removed from schedules-experiments.js',
);
assert(
  !industrialCss.includes('.exp-card__header-row'),
  'obsolete .exp-card__header-row must be removed from industrial.css',
);
assert(
  !industrialCss.includes('.exp-card__header-window'),
  'obsolete .exp-card__header-window must be removed from industrial.css',
);

// Shared period-header pin rules (from SWD-51) must still be present.
const actionsCssRule = css.match(/\.schedule-form__period-header-actions\s*\{[^}]*\}/);
assert(actionsCssRule, 'CSS must define .schedule-form__period-header-actions');
assert(
  actionsCssRule[0].includes('flex-shrink: 0'),
  'header-actions must have flex-shrink: 0 so it never wraps',
);

const headerBlock = css.match(/\.schedule-form__period-header\s*\{[^}]*\}/);
assert(headerBlock, 'CSS must define .schedule-form__period-header');
assert(
  headerBlock[0].includes('flex-wrap: nowrap'),
  'period-header must use flex-wrap: nowrap to keep actions pinned',
);

// Index/overview experimentRowHtml: status → name → time (SWD-51 sched-row contract).
const rowFn = utils.match(/export function experimentRowHtml[\s\S]*?\n\}/);
assert(rowFn, 'experimentRowHtml must exist in experiment-utils.js');
const statusIdx = rowFn[0].indexOf('exp-status-badge');
const nameIdx = rowFn[0].indexOf('sched-row__name');
const timeIdx = rowFn[0].indexOf('sched-row__time');
assert(statusIdx >= 0 && nameIdx >= 0 && timeIdx >= 0, 'experimentRowHtml must render status, name, time');
assert(
  statusIdx < nameIdx && nameIdx < timeIdx,
  'experimentRowHtml markup order must be status → name → time',
);

console.log('panel_experiment_header_markup.harness.mjs: ok');
