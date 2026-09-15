/**
 * Shared plot-box and time-domain math for stacked Chart.js time plots.
 * Dual Y-axes change chartArea independently; this equalises left/right edges
 * and the x min/max so vertical grids and NOW line up.
 */

const PAD_EPS = 0.5;
const TIME_EPS = 1;

/** @type {Map<string, Set<object>>} */
const alignGroups = new Map();
/** @type {Set<string>} */
const syncLocks = new Set();

/**
 * @param {{ left: number, right: number, width: number }[]} areas
 * @returns {{ left: number, rightInset: number } | null}
 */
export function unionPlotBox(areas) {
  if (!areas.length) return null;
  let left = -Infinity;
  let rightInset = -Infinity;
  for (const area of areas) {
    if (area == null || !Number.isFinite(area.left) || !Number.isFinite(area.right)
        || !Number.isFinite(area.width)) {
      continue;
    }
    left = Math.max(left, area.left);
    rightInset = Math.max(rightInset, area.width - area.right);
  }
  if (!Number.isFinite(left) || !Number.isFinite(rightInset)) return null;
  return { left, rightInset };
}

/**
 * Extra layout padding so `area` matches `union` after Chart.js layout.
 * @param {{ left: number, right: number, width: number }} area
 * @param {{ left: number, rightInset: number }} union
 * @returns {{ left: number, right: number }}
 */
export function extraPaddingToMatch(area, union) {
  if (area == null || union == null) return { left: 0, right: 0 };
  const rightInset = area.width - area.right;
  return {
    left: Math.max(0, union.left - area.left),
    right: Math.max(0, union.rightInset - rightInset),
  };
}

/**
 * Plot box implied by applying extra padding (for tests without Chart.js).
 * @param {{ left: number, right: number, width: number }} area
 * @param {{ left: number, right: number }} pad
 * @returns {{ left: number, right: number }}
 */
export function applyExtraPadding(area, pad) {
  return {
    left: area.left + (pad?.left || 0),
    right: area.right - (pad?.right || 0),
  };
}

/**
 * Undo plugin padding so union is computed from the natural axis reservation.
 * @param {{ chartArea?: { left: number, right: number }, width: number, $alignPad?: { left: number, right: number } }} chart
 * @returns {{ left: number, right: number, width: number } | null}
 */
export function naturalPlotArea(chart) {
  const box = chart?.chartArea;
  if (!box || !Number.isFinite(box.left) || !Number.isFinite(box.right)) return null;
  const pad = chart.$alignPad || { left: 0, right: 0 };
  return {
    left: box.left - pad.left,
    right: box.right + pad.right,
    width: chart.width,
  };
}

/**
 * Inclusive wall-clock domain across dataset point lists.
 * @param {Iterable<{ x?: number } | null | undefined>[]} seriesLists
 * @returns {{ xMin: number, xMax: number } | null}
 */
export function sharedTimeDomain(seriesLists) {
  let xMin = Infinity;
  let xMax = -Infinity;
  for (const series of seriesLists) {
    if (!series) continue;
    for (const point of series) {
      const x = point && point.x;
      if (x == null || !Number.isFinite(x)) continue;
      if (x < xMin) xMin = x;
      if (x > xMax) xMax = x;
    }
  }
  if (!Number.isFinite(xMin) || !Number.isFinite(xMax)) return null;
  return { xMin, xMax };
}

function groupIdOf(chart) {
  return chart?.options?.plugins?.alignPlotBox?.group || null;
}

function registerChart(chart) {
  const group = groupIdOf(chart);
  if (!group) return;
  let set = alignGroups.get(group);
  if (!set) {
    set = new Set();
    alignGroups.set(group, set);
  }
  set.add(chart);
}

function unregisterChart(chart) {
  const group = groupIdOf(chart);
  if (!group) return;
  const set = alignGroups.get(group);
  if (!set) return;
  set.delete(chart);
  if (set.size === 0) alignGroups.delete(group);
}

function rememberBasePadding(chart) {
  if (chart.$alignBasePad) return;
  const prev = chart.options.layout?.padding;
  if (typeof prev === 'number') {
    chart.$alignBasePad = { top: prev, bottom: prev, left: prev, right: prev };
    return;
  }
  chart.$alignBasePad = {
    top: prev?.top || 0,
    bottom: prev?.bottom || 0,
    left: prev?.left || 0,
    right: prev?.right || 0,
  };
}

function writePad(chart, pad) {
  rememberBasePadding(chart);
  const base = chart.$alignBasePad;
  if (!chart.options.layout) chart.options.layout = {};
  chart.options.layout.padding = {
    top: base.top,
    bottom: base.bottom,
    left: base.left + pad.left,
    right: base.right + pad.right,
  };
  chart.$alignPad = pad;
}

function padChanged(a, b) {
  const left = a?.left || 0;
  const right = a?.right || 0;
  return Math.abs(left - (b?.left || 0)) > PAD_EPS
    || Math.abs(right - (b?.right || 0)) > PAD_EPS;
}

function datasetSeries(chart) {
  const out = [];
  for (const ds of chart.data?.datasets || []) {
    if (ds?.data) out.push(ds.data);
  }
  return out;
}

function syncGroup(chart) {
  const group = groupIdOf(chart);
  if (!group || syncLocks.has(group)) return;
  const set = alignGroups.get(group);
  if (!set || set.size < 2) return;
  syncLocks.add(group);
  try {
    const members = [...set];
    const areas = [];
    for (const member of members) {
      const area = naturalPlotArea(member);
      if (area) areas.push(area);
    }
    const union = unionPlotBox(areas);
    if (!union) return;

    const series = [];
    for (const member of members) series.push(...datasetSeries(member));
    const domain = sharedTimeDomain(series);

    let relayout = false;
    let rescale = false;
    for (const member of members) {
      const area = naturalPlotArea(member);
      if (!area) continue;
      const pad = extraPaddingToMatch(area, union);
      if (padChanged(pad, member.$alignPad)) {
        writePad(member, pad);
        relayout = true;
      }
      if (domain && member.options?.scales?.x) {
        const x = member.options.scales.x;
        if (x.min == null || x.max == null
            || Math.abs(x.min - domain.xMin) > TIME_EPS
            || Math.abs(x.max - domain.xMax) > TIME_EPS) {
          x.min = domain.xMin;
          x.max = domain.xMax;
          rescale = true;
        }
      }
    }

    if (!relayout && !rescale) return;
    for (const member of members) {
      member.update('none');
    }
  } finally {
    syncLocks.delete(group);
  }
}

/** Chart.js plugin — opt in with `options.plugins.alignPlotBox.group`. */
export function alignPlotBoxPlugin() {
  return {
    id: 'alignPlotBox',
    afterInit(chart) {
      registerChart(chart);
    },
    afterUpdate(chart) {
      syncGroup(chart);
    },
    afterDestroy(chart) {
      unregisterChart(chart);
    },
  };
}
