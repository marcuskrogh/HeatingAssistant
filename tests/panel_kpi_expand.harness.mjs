/**
 * Expand-order helper for Overview/room KPI sections.
 * Run: node tests/panel_kpi_expand.harness.mjs
 */
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const mod = await import(
  `${pathToFileURL(join(ROOT, 'heatingassistant/app/static/js/components/kpi-expand.js')).href}`
);

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg);
    process.exit(1);
  }
}

const keys = ['overall-health', 'mpc-load', 'comfort'];

{
  const next = mod.expandStateAfterClick({ keys, openKey: null, clickedKey: 'mpc-load' });
  assert(next.openKey === 'mpc-load', 'first click must expand mpc-load');
  assert(next.order[0] === 'mpc-load', 'expanded card must move to the top');
  assert(JSON.stringify(next.order.slice(1)) === JSON.stringify(['overall-health', 'comfort']), 'remaining order is original minus clicked');
}

{
  const open = mod.expandStateAfterClick({ keys, openKey: 'mpc-load', clickedKey: 'comfort' });
  assert(open.openKey === 'comfort', 'second card must become the open card');
  assert(open.order[0] === 'comfort', 'new card must sit at the top');
  assert(open.order.includes('mpc-load'), 'previous card stays in the section');
}

{
  const closed = mod.expandStateAfterClick({ keys, openKey: 'mpc-load', clickedKey: 'mpc-load' });
  assert(nextOpenNull(closed), 'clicking the open card must collapse');
  assert(JSON.stringify(closed.order) === JSON.stringify(keys), 'collapse restores original order');
}

function nextOpenNull(result) {
  return result.openKey === null;
}

console.log('panel_kpi_expand.harness.mjs: ok');
