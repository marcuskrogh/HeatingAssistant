/**
 * Spec lock: stacked room plots share one plot box after extra padding.
 * Screenshot defect: Temperature left-only vs dual-Y siblings.
 * Run: node tests/panel_chart_align.harness.mjs
 */
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const mod = await import(
  `${pathToFileURL(join(ROOT, 'heatingassistant/app/static/js/components/chart-align.js')).href}`
);

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg);
    process.exit(1);
  }
}

// Unaligned boxes from the operator screenshot shape (left-only vs y+y2).
const temp = { left: 48, right: 980, width: 1000 };
const power = { left: 72, right: 910, width: 1000 };
const disturb = { left: 64, right: 908, width: 1000 };

assert(temp.left !== power.left, 'defect: left-only plot box starts left of dual-Y');
assert(temp.right !== power.right, 'defect: dual-Y plot box ends left of temperature');

const union = mod.unionPlotBox([temp, power, disturb]);
assert(union.left === 72, `union left is the max left, got ${union.left}`);
assert(union.rightInset === 1000 - 908, `union rightInset is the max inset, got ${union.rightInset}`);

const boxes = [temp, power, disturb].map((area) => {
  const pad = mod.extraPaddingToMatch(area, union);
  return mod.applyExtraPadding(area, pad);
});

assert(boxes[0].left === boxes[1].left && boxes[1].left === boxes[2].left,
  `aligned lefts ${boxes.map((b) => b.left).join(',')}`);
assert(boxes[0].right === boxes[1].right && boxes[1].right === boxes[2].right,
  `aligned rights ${boxes.map((b) => b.right).join(',')}`);

const already = { left: 72, right: 908, width: 1000 };
const zero = mod.extraPaddingToMatch(already, union);
assert(zero.left === 0 && zero.right === 0, 'already-aligned area needs no extra pad');

const natural = mod.naturalPlotArea({
  chartArea: { left: 80, right: 900 },
  width: 1000,
  $alignPad: { left: 8, right: 8 },
});
assert(natural.left === 72 && natural.right === 908, 'natural area subtracts plugin pad');

const domain = mod.sharedTimeDomain([
  [{ x: 1000, y: 1 }, { x: 2000, y: 2 }],
  [{ x: 1500, y: 3 }, { x: 4000, y: 4 }],
  [{ x: null, y: 0 }],
]);
assert(domain.xMin === 1000 && domain.xMax === 4000, 'time domain is min/max x across plots');

assert(mod.unionPlotBox([]) === null, 'empty areas have no union');
assert(mod.sharedTimeDomain([[], [{ y: 1 }]]) === null, 'no timestamps → no domain');

console.log('ok: panel_chart_align');
