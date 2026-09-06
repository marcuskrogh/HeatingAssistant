/** Shared Chart.js + canvas plot chrome. Keep in lockstep with APP-UI-STYLEGUIDE.md. */

export const CHART_FONT_SANS = "system-ui, -apple-system, 'Segoe UI', sans-serif";
export const CHART_FONT_MONO = "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace";
export const CHART_TICK_SIZE = 11;
export const CHART_LEGEND_SIZE = 11;
export const CHART_TITLE_SIZE = 11;
export const CHART_HEIGHT_PRIMARY = 240;
export const CHART_HEIGHT_SECONDARY = 200;
export const CHART_LINE_WIDTH = 2;
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
  };
}

export function tickFont(family = CHART_FONT_MONO, size = CHART_TICK_SIZE) {
  return { size, family };
}

export function labelFont(family = CHART_FONT_SANS, size = CHART_TICK_SIZE) {
  return { size, family };
}
