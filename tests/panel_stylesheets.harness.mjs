/**
 * Regression harness: panel page CSS must be linked explicitly in the shadow
 * root, not via @import inside industrial.css.
 *
 * @import in a stylesheet loaded inside shadow DOM is unreliable (notably mobile
 * Safari), which leaves climate cards and other page components as unstyled text
 * boxes while base KPI gauges still render.
 *
 * Run: node tests/panel_stylesheets.harness.mjs
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const DASHBOARD = join(ROOT, 'heatingassistant/app/static/industrial-dashboard.js');
const INDUSTRIAL_CSS = join(ROOT, 'heatingassistant/app/static/css/industrial.css');
const CLIMATE_CSS = join(ROOT, 'heatingassistant/app/static/css/pages/climate-card.css');
const INDEX_HTML = join(ROOT, 'heatingassistant/app/static/index.html');

const REQUIRED_STYLESHEETS = [
  'css/industrial.css',
  'css/pages/tuning.css',
  'css/pages/identification.css',
  'css/pages/schedules.css',
  'css/pages/climate-card.css',
  'css/pages/configuration.css',
  'css/pages/system-status.css',
];

const LAYOUT_MARKERS = [
  '.climate-card__body',
  'display: flex',
  '.climate-card__control',
  '.climate-card__current-num',
  '.room-climate-tile',
];

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const dashboard = readFileSync(DASHBOARD, 'utf8');
const industrialCss = readFileSync(INDUSTRIAL_CSS, 'utf8');
const climateCss = readFileSync(CLIMATE_CSS, 'utf8');
const indexHtml = readFileSync(INDEX_HTML, 'utf8');

const versionMatch = indexHtml.match(/industrial-dashboard\.js\?v=(\d+)/);
assert(versionMatch, 'index.html must declare industrial-dashboard.js cache-bust token');
const version = versionMatch[1];

assert(
  dashboard.includes('PANEL_STYLESHEETS'),
  'industrial-dashboard.js must declare PANEL_STYLESHEETS',
);
assert(
  dashboard.includes('panelStylesheetLinks'),
  'industrial-dashboard.js must build explicit stylesheet links for the shadow root',
);
assert(
  /panelStylesheetLinks\(PANEL_VERSION\)/.test(dashboard),
  'industrial-dashboard.js must inject stylesheet links into the shadow root',
);

for (const path of REQUIRED_STYLESHEETS) {
  assert(
    dashboard.includes(`'${path}'`),
    `industrial-dashboard.js PANEL_STYLESHEETS must include '${path}'`,
  );
}

assert(
  !/@import\s+url\(/.test(industrialCss),
  'industrial.css must not use @import (page CSS is linked from industrial-dashboard.js)',
);

for (const marker of LAYOUT_MARKERS) {
  assert(
    climateCss.includes(marker),
    `climate-card.css must define layout marker: ${marker}`,
  );
}

console.log('panel stylesheets harness: ok');
