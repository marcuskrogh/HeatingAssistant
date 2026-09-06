/** Shared plot constants. Room-view Chart.js (`time-series-chart.js` +
 *  `room-charts.js`) is the guide — do not change those defaults to match
 *  other screens. Canvas plots (PE) copy these CSS-pixel values instead. */

export const CHART_FONT_SANS = "system-ui, -apple-system, 'Segoe UI', sans-serif";
export const CHART_FONT_MONO = "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace";
/** Room-view tick / legend size (Chart.js `font.size: 10`). */
export const CHART_TICK_SIZE = 10;
export const CHART_LEGEND_SIZE = 10;
export const CHART_TITLE_SIZE = 10;
export const CHART_HEIGHT_PRIMARY = 240;
export const CHART_HEIGHT_SECONDARY = 200;
/** Primary room series (`borderWidth: 2` on temp/power/disturbance history). */
export const CHART_LINE_WIDTH = 2;
/** `makeDataset` dashed overlays (`borderWidth` 1.5, `borderDash: [5, 5]`). */
export const CHART_DASH_WIDTH = 1.5;
export const CHART_DASH_PATTERN = [5, 5];
export const CHART_COLOR_TICK = '#6b7280';
export const CHART_COLOR_LEGEND = '#9aa0a8';
export const CHART_COLOR_GRID_X = 'rgba(54, 59, 68, 0.5)';
export const CHART_COLOR_GRID_Y = 'rgba(54, 59, 68, 0.3)';

export function readTheme(el) {
  const cs = (typeof getComputedStyle === 'function' && el)
    ? getComputedStyle(el)
    : null;
  const read = (name, fallback) => {
    const v = cs?.getPropertyValue(name).trim();
    return v || fallback;
  };
  return {
    bg: read('--bg-primary', '#1a1d23'),
    card: read('--bg-card', '#22262e'),
    grid: read('--border', '#363b44'),
    tick: read('--text-dim', CHART_COLOR_TICK),
    legend: read('--text-secondary', CHART_COLOR_LEGEND),
    series: read('--chart-temp', '#4fc3f7'),
    warn: read('--warning', '#f5a623'),
    fontSans: read('--font-sans', CHART_FONT_SANS),
    fontMono: read('--font-mono', CHART_FONT_MONO),
    tickSize: CHART_TICK_SIZE,
    legendSize: CHART_LEGEND_SIZE,
    lineWidth: CHART_LINE_WIDTH,
    dashWidth: CHART_DASH_WIDTH,
  };
}

export function tickFont(family = CHART_FONT_MONO, size = CHART_TICK_SIZE) {
  return { size, family };
}

export function labelFont(family = CHART_FONT_SANS, size = CHART_TICK_SIZE) {
  return { size, family };
}

/** Size a 2d canvas to its wrapper in CSS pixels, then scale by DPR so a
 *  lineWidth of 2 stays 2 CSS pixels (CSS `width:100%` on a 680px bitmap
 *  shrinks strokes). */
export function sizePlotCanvas(canvas) {
  const wrap = canvas.parentElement;
  const cssW = Math.max(1, Math.round((wrap && wrap.clientWidth) || canvas.clientWidth || 680));
  const cssH = Math.max(1, Math.round((wrap && wrap.clientHeight) || canvas.clientHeight || CHART_HEIGHT_SECONDARY));
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  canvas.style.width = `${cssW}px`;
  canvas.style.height = `${cssH}px`;
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, cssW, cssH, dpr };
}
