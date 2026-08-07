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
  'heatingassistant/app/static/js/schedules/schedules-experiments.js',
);
const EXPERIMENT_UTILS = join(
  ROOT,
  'heatingassistant/app/static/js/experiment-utils.js',
);
const SCHEDULES_CSS = join(
  ROOT,
  'heatingassistant/app/static/css/pages/schedules.css',
);
const INDUSTRIAL_CSS = join(
  ROOT,
  'heatingassistant/app/static/css/industrial.css',
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

// Card list header-main: name → signal → status → window (not the form header).
const cardMainMatch = source.match(
  /schedule-form__period-header-main[\s\S]*?schedule-form__period-name[\s\S]*?exp-signal-badge[\s\S]*?exp-status-badge[\s\S]*?schedule-form__period-time[\s\S]*?<\/div>/,
);
assert(
  cardMainMatch,
  'card header-main must contain name → signal → status → window in order',
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

// Form prefill name and room placeholder must be escaped.
assert(
  source.includes('escapeHtml(prefill?.name') || source.includes('escapeHtml(prefill?.name ||'),
  'form name input value must use escapeHtml',
);
assert(
  source.includes('escapeHtml(room.name)'),
  'form placeholder must escape room.name',
);

// Dead marker class and obsolete two-row markup must be gone.
assert(
  !source.includes('exp-card__header'),
  'dead exp-card__header class must be removed from schedules-experiments.js',
);
assert(
  !source.includes('exp-card__header-row'),
  'obsolete .exp-card__header-row must be removed from schedules-experiments.js',
);
assert(
  !source.includes('exp-card__header-window'),
  'obsolete .exp-card__header-window must be removed from schedules-experiments.js',
);
assert(
  !industrialCss.includes('.exp-card__header'),
  'obsolete .exp-card__header rules must be removed from industrial.css',
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

// SWD-58: name/time flex contract shared with schedule rows.
assert(
  /\/\* Shared name\/time flex contract[\s\S]*\.sched-row__name,\s*\r?\n\.schedule-form__period-name/.test(css)
    || css.includes('.sched-row__name,\n.schedule-form__period-name')
    || css.includes('.sched-row__name,\r\n.schedule-form__period-name'),
  'name flex contract must be shared between sched-row and period-name',
);
const nameBlock = css.match(
  /\.sched-row__name,\s*\r?\n\.schedule-form__period-name\s*\{[^}]*\}/,
);
assert(nameBlock, 'shared name rule block must exist');
assert(nameBlock[0].includes('flex: 1 1 6em'), 'name must use flex: 1 1 6em');
assert(nameBlock[0].includes('min-width: 4em'), 'name must use min-width: 4em');
const timeBlock = css.match(
  /\.sched-row__time,\s*\r?\n\.schedule-form__period-time\s*\{[^}]*\}/,
);
assert(timeBlock, 'shared time rule block must exist');
assert(timeBlock[0].includes('flex: 0 1 auto'), 'timing must use flex: 0 1 auto');

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
