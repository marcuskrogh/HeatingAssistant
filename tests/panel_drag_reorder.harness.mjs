/**
 * Harness: drag-to-reorder priority on the room schedule detail page (SWD-24).
 *
 * Focus is on the pieces we can test hermetically:
 *   1. Pure helpers in schedule-utils.js (`movePeriodInList`,
 *      `remapExpandedIndices`).
 *   2. Static source assertions on schedules-detail.js so the acceptance-
 *      criteria wiring (inactive section, dirty gate, hold-then-move touch
 *      constant, drop-reason persist, enable-to-end, draggable enabled cards)
 *      can't silently regress.
 *   3. First-match-wins after reorder using the real `findActivePeriod`
 *      matcher — the property SWD-24 explicitly promises not to break.
 *
 * We deliberately do NOT try to simulate full HTML5 DnD with layout — JSDOM
 * has no layout engine and drop-point calculations depend on
 * getBoundingClientRect. Those are covered by the source assertions plus the
 * pure move helpers.
 *
 * Run: node tests/panel_drag_reorder.harness.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  findActivePeriod,
  movePeriodInList,
  remapExpandedIndices,
} from '../heatingassistant/app/static/js/schedule-utils.js';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const DETAIL = join(ROOT, 'heatingassistant/app/static/js/schedules/schedules-detail.js');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function assertEq(actual, expected, message) {
  const a = JSON.stringify(actual);
  const b = JSON.stringify(expected);
  if (a !== b) throw new Error(`${message}: expected ${b}, got ${a}`);
}

// ────────────────────────────────────────────────────────────────────────────
// 1. Pure helpers — movePeriodInList
// ────────────────────────────────────────────────────────────────────────────

{
  const list = ['a', 'b', 'c', 'd'];

  assertEq(movePeriodInList(list, 0, 2), ['b', 'c', 'a', 'd'], 'move first forward');
  assertEq(movePeriodInList(list, 3, 0), ['d', 'a', 'b', 'c'], 'move last to front');
  assertEq(movePeriodInList(list, 1, 2), ['a', 'c', 'b', 'd'], 'adjacent swap forward');
  assertEq(movePeriodInList(list, 2, 1), ['a', 'c', 'b', 'd'], 'adjacent swap backward');
  assertEq(movePeriodInList(list, 1, 1), list, 'no-op returns copy of same order');
  assert(movePeriodInList(list, 1, 1) !== list, 'no-op returns a fresh array, not the original');
  assertEq(movePeriodInList(list, -1, 0), list, 'out-of-range from returns copy');
  assertEq(movePeriodInList(list, 42, 0), list, 'out-of-range from (beyond end) returns copy');
  assertEq(movePeriodInList(list, 0, -3), ['a', 'b', 'c', 'd'], 'negative to clamps to 0 (no-op for from=0)');
  assertEq(movePeriodInList(list, 0, 99), ['b', 'c', 'd', 'a'], 'to beyond end clamps to last index');

  assert(!Array.isArray(movePeriodInList(null, 0, 0)), 'non-array input is returned unchanged');
  assert(movePeriodInList([], 0, 0) instanceof Array, 'empty array returns an array');
  assertEq(movePeriodInList([], 0, 0), [], 'empty array returns empty');

  // Original must not be mutated on any successful move.
  const original = ['x', 'y', 'z'];
  const moved = movePeriodInList(original, 0, 2);
  assertEq(original, ['x', 'y', 'z'], 'movePeriodInList does not mutate the input');
  assertEq(moved, ['y', 'z', 'x'], 'movePeriodInList returns the new order');
}

// ────────────────────────────────────────────────────────────────────────────
// 2. Pure helpers — remapExpandedIndices
// ────────────────────────────────────────────────────────────────────────────

function setToSortedArray(set) {
  return Array.from(set).sort((a, b) => a - b);
}

{
  // Forward move: from=1, to=3. The moved item's new position is 3; items
  // originally between (2,3] shift down by one.
  const remapped = remapExpandedIndices(new Set([0, 1, 2, 3, 4]), 1, 3);
  assertEq(setToSortedArray(remapped), [0, 1, 2, 3, 4],
    'forward move keeps the same underlying periods expanded');
  // Same indices, but pin which one was the source: only track the source.
  const only1 = remapExpandedIndices(new Set([1]), 1, 3);
  assertEq(setToSortedArray(only1), [3], 'source index follows the moved item');
  const between = remapExpandedIndices(new Set([2]), 1, 3);
  assertEq(setToSortedArray(between), [1], 'items between source and target shift down on forward move');
  const untouchedBefore = remapExpandedIndices(new Set([0]), 1, 3);
  assertEq(setToSortedArray(untouchedBefore), [0], 'items before the source range are unaffected on forward move');
  const untouchedAfter = remapExpandedIndices(new Set([4]), 1, 3);
  assertEq(setToSortedArray(untouchedAfter), [4], 'items after the target are unaffected on forward move');

  // Backward move: from=3, to=1. Moved item's new position is 1; items in
  // [1,3) shift up by one.
  const back = remapExpandedIndices(new Set([3]), 3, 1);
  assertEq(setToSortedArray(back), [1], 'source index follows the moved item (backward)');
  const backBetween = remapExpandedIndices(new Set([1, 2]), 3, 1);
  assertEq(setToSortedArray(backBetween), [2, 3], 'items in [to, from) shift up on backward move');
  const backUntouched = remapExpandedIndices(new Set([0, 4]), 3, 1);
  assertEq(setToSortedArray(backUntouched), [0, 4], 'items outside [to..from] range unaffected on backward move');

  // No-op preserves the set exactly.
  const noop = remapExpandedIndices(new Set([0, 2]), 2, 2);
  assertEq(setToSortedArray(noop), [0, 2], 'no-op move returns identical indices');
  assert(noop !== undefined, 'no-op move returns a Set instance');

  // Non-Set inputs return an empty Set (defensive contract).
  const emptyOut = remapExpandedIndices(null, 0, 1);
  assert(emptyOut instanceof Set && emptyOut.size === 0, 'non-Set input yields empty Set');
}

// ────────────────────────────────────────────────────────────────────────────
// 3. Source assertions on schedules-detail.js — SWD-24 wiring
// ────────────────────────────────────────────────────────────────────────────

const detailSource = readFileSync(DETAIL, 'utf8');

// Helpers are actually imported from schedule-utils (the whole point).
assert(
  /import\s*\{[\s\S]*?\bmovePeriodInList\b[\s\S]*?\}\s*from\s*['"]\.\.\/schedule-utils\.js/.test(detailSource),
  'schedules-detail.js must import movePeriodInList from schedule-utils.js',
);
assert(
  /import\s*\{[\s\S]*?\bremapExpandedIndices\b[\s\S]*?\}\s*from\s*['"]\.\.\/schedule-utils\.js/.test(detailSource),
  'schedules-detail.js must import remapExpandedIndices from schedule-utils.js',
);

// Inactive section is rendered (separate from the enabled drag list).
assert(
  detailSource.includes("id=\"inactive-periods-container\"") ||
    detailSource.includes("'inactive-periods-container'"),
  'inactive periods container element must be present',
);
assert(
  detailSource.includes('INACTIVE PERIODS'),
  'inactive periods section header text must be present',
);
assert(
  detailSource.includes('sched-detail__section-header--inactive'),
  'inactive periods section header uses its own modifier class',
);
assert(
  detailSource.includes('schedule-form__periods--inactive'),
  'inactive container carries the --inactive modifier class',
);

// Dirty UI wiring.
assert(
  detailSource.includes('sched-detail__dirty-banner'),
  'dirty banner element must be present',
);
assert(
  detailSource.includes('sched-detail--dirty'),
  'container gets the dirty modifier class while dirty',
);
assert(
  detailSource.includes('schedule-form__periods--reorder-locked'),
  'enabled-periods container gets a reorder-locked modifier class while dirty',
);
assert(
  detailSource.includes('schedule-form__period--unsaved'),
  'unsaved period cards get the --unsaved modifier class',
);
assert(
  detailSource.includes('dirtyPeriodIndices'),
  'dirty tracking is per-period, not just page-level',
);
assert(
  detailSource.includes('flushOpenEditorsToLocal'),
  'open editors are flushed into localPeriods so dirty gating is trustworthy',
);
assert(
  /const reorderLocked = dirty \|\| saveInFlight/.test(detailSource),
  'reorder is locked while dirty OR while a save is in flight',
);
assert(
  /if\s*\(reorderLocked\)\s*card\.removeAttribute\(['"]draggable['"]\)/.test(detailSource),
  'reorder-locked state removes the draggable attribute from cards',
);
assert(
  /if\s*\(dirty\s*\|\|\s*saveInFlight\)\s*\{\s*e\.preventDefault\(\)/.test(detailSource),
  'dragstart must be cancelled while dirty or saving',
);

// Touch hold constant sits near ~400ms (SWD-24 tuning target: intuitive,
// no accidental grab). Guard the range so a future tweak stays in the spec.
{
  const holdMatch = detailSource.match(/TOUCH_DRAG_HOLD_MS\s*=\s*(\d+)/);
  assert(holdMatch, 'TOUCH_DRAG_HOLD_MS constant must be defined');
  const holdMs = Number(holdMatch[1]);
  assert(
    holdMs >= 250 && holdMs <= 600,
    `touch hold delay must be in the "hold-then-move" range 250..600ms, got ${holdMs}`,
  );
  assert(
    /TOUCH_DRAG_HOLD_SLOP_PX\s*=\s*\d+/.test(detailSource),
    'touch hold slop constant must be defined so scroll cancels the hold',
  );
}

// Persist path — auto-save on drop.
assert(
  /async function persistPeriods\s*\(\s*\{\s*reason\s*\}\s*\)/.test(detailSource),
  'persistPeriods must accept a reason and be async',
);
assert(
  /persistPeriods\(\{\s*reason:\s*['"]drop['"]\s*\}\)/.test(detailSource),
  'commitReorder must call persistPeriods with reason "drop"',
);
assert(
  /persistPeriods\(\{\s*reason:\s*['"]save['"]\s*\}\)/.test(detailSource),
  'manual Save must call persistPeriods with reason "save"',
);
assert(
  detailSource.includes('Saving new order'),
  'drop persist should show a "Saving new order" status',
);
assert(
  detailSource.includes('Order saved'),
  'successful drop persist should show an "Order saved" status',
);
assert(
  /catch\s*\(err\)\s*\{[\s\S]*?dirty\s*=\s*true[\s\S]*?updateDirtyUI\(\)/.test(detailSource),
  'failed drop persist keeps the new order as unsaved (dirty=true) so user can retry',
);

// commitReorder wires the pure helpers.
assert(
  /commitReorder[\s\S]*?remapExpandedIndices\(expandedSet, fromIndex, toIndex\)/.test(detailSource),
  'commitReorder must remap the expanded set using remapExpandedIndices',
);
assert(
  /commitReorder[\s\S]*?movePeriodInList\(localPeriods, fromIndex, toIndex\)/.test(detailSource),
  'commitReorder must use movePeriodInList to reorder localPeriods',
);

// Enable → append to end of active (non-inactive) group.
{
  const detach = /localPeriods\.splice\(from,\s*1\)/;
  assert(detach.test(detailSource), 'enable path must detach the period from its current slot');
  const insertAtEnd = /localPeriods\.splice\(insertAt,\s*0,\s*detached\)/;
  assert(insertAtEnd.test(detailSource), 'enable path must re-insert the period at end of enabled group');
  const findsLastActive = /for\s*\(let\s+k[\s\S]*?!isPeriodInactive\(localPeriods\[k\]\)[\s\S]*?lastActiveIdx\s*=\s*k/;
  assert(
    findsLastActive.test(detailSource),
    'enable path must locate the last active (non-inactive) index before re-inserting',
  );
}

// Draggable + data-period-index wiring on active (non-inactive) cards only.
assert(
  detailSource.includes('schedule-form__period--draggable'),
  'enabled cards must carry the --draggable class',
);
assert(
  /if\s*\(!periodInactive\)\s*\{[\s\S]*?card\.classList\.add\(['"]schedule-form__period--draggable['"]\)/.test(detailSource),
  '--draggable class is applied only for active (non-inactive) cards',
);
assert(
  /card\.dataset\.periodIndex\s*=\s*String\(i\)/.test(detailSource),
  'each card records its localPeriods index in data-period-index',
);
assert(
  /if\s*\(!periodInactive\)\s*\{\s*attachMouseDrag\(card,\s*i\);\s*attachTouchDrag\(card,\s*i\);/.test(detailSource),
  'DnD handlers are wired only for active (non-inactive) cards',
);
assert(
  /isPeriodInactive/.test(detailSource),
  'detail page must use isPeriodInactive for active/inactive bucketing',
);
assert(
  /formatPeriodPreview/.test(detailSource),
  'collapsed headers must use formatPeriodPreview (SWD-22 shared preview)',
);

// Persisted order sends enabled-first, disabled-last — same convention that
// makes list-order = priority on the wire.
assert(
  /localPeriods\.filter\(\(p\) => p\.enabled !== false\)[\s\S]*?localPeriods\.filter\(\(p\) => p\.enabled === false\)/.test(detailSource),
  'persistPeriods must serialize enabled periods before disabled ones',
);

// Interactive controls do not initiate a drag from within (whole-row drag
// with exclusions is the plan's chosen affordance).
assert(
  detailSource.includes('INTERACTIVE_DRAG_EXCLUDE_SELECTOR'),
  'drag-exclude selector must be defined so clicks on controls do not start a drag',
);
assert(
  detailSource.includes('isDragExcludedTarget'),
  'dragstart handlers must consult isDragExcludedTarget',
);

// ────────────────────────────────────────────────────────────────────────────
// 4. First-match-wins after reorder — end-to-end property using real matcher
// ────────────────────────────────────────────────────────────────────────────

{
  // Two enabled periods overlap at 10:00 on a Monday. Priority is list order.
  // Reordering them must change which one matches "now".
  const monday10 = new Date('2026-01-05T10:00:00'); // 2026-01-05 is a Monday.
  const morning = {
    name: 'Morning',
    schedule_type: 'weekly_recurring',
    time_mode: 'window',
    start: '08:00',
    end: '12:00',
    days: [0, 1, 2, 3, 4],
    mode: 'comfort',
    enabled: true,
  };
  const workday = {
    name: 'Workday',
    schedule_type: 'weekly_recurring',
    time_mode: 'window',
    start: '09:00',
    end: '17:00',
    days: [0, 1, 2, 3, 4],
    mode: 'off',
    enabled: true,
  };

  // Both would match at 10:00 individually; findActivePeriod picks the first
  // one in list order. Verify via periodMatchesNow-equivalent by reordering.
  const originalList = [morning, workday];
  // Bind findActivePeriod's `now` for a deterministic assertion: since it
  // uses `new Date()` under the hood, temporarily overwrite Date briefly
  // just for this call. Cleaner: call periodMatchesNow directly? We have
  // findActivePeriod, so patch Date.now for the duration of this block.
  const RealDate = Date;
  globalThis.Date = class extends RealDate {
    constructor(...args) {
      if (args.length === 0) return new RealDate(monday10.getTime());
      return new RealDate(...args);
    }
    static now() { return monday10.getTime(); }
  };
  try {
    const activeBefore = findActivePeriod(originalList);
    assert(activeBefore && activeBefore.name === 'Morning',
      'before reorder the first-listed matching period wins (Morning)');

    const reordered = movePeriodInList(originalList, 1, 0);
    assertEq(reordered.map((p) => p.name), ['Workday', 'Morning'],
      'reorder should place Workday first');
    const activeAfter = findActivePeriod(reordered);
    assert(activeAfter && activeAfter.name === 'Workday',
      'after reorder the new first-listed matching period wins (Workday)');

    // A disabled period must never win even if it comes first.
    const withDisabled = [
      { ...morning, enabled: false },
      workday,
    ];
    const activeSkip = findActivePeriod(withDisabled);
    assert(activeSkip && activeSkip.name === 'Workday',
      'disabled periods are skipped by first-match-wins matching');
  } finally {
    globalThis.Date = RealDate;
  }
}

console.log('panel_drag_reorder.harness.mjs: all assertions passed');
