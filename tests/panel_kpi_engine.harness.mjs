/**
 * Regression harness for the REAL kpi-engine.js: hand-checkable KPI numbers
 * from synthetic entity-state snapshots, backend-attribute precedence, and
 * empty/missing-input behaviour.
 *
 * Run: node tests/panel_kpi_engine.harness.mjs
 */
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const WWW = join(ROOT, 'heatingassistant/app/static/js');

function assert(cond, msg) {
  if (!cond) { console.error('FAIL:', msg); process.exit(1); }
}
function approx(a, b, eps = 1e-9) {
  return a !== null && b !== null && Math.abs(a - b) < eps;
}

const kpi = await import(`${pathToFileURL(join(WWW, 'kpi-engine.js')).href}?v=96`);

const SUMMARY = 'sensor.heating_assistant_system_summary';
const MPC = 'sensor.heating_assistant_mpc_performance';
const CONFIG = 'sensor.heating_assistant_controller_config';

const room = (slug) => ({
  slug,
  name: slug,
  entities: {
    temperature_filtered: `sensor.heating_assistant_${slug}_temperature_filtered`,
    temperature_measured: `sensor.heating_assistant_${slug}_temperature_measured`,
    setpoint: `sensor.heating_assistant_${slug}_setpoint`,
    constraint_lower: `sensor.heating_assistant_${slug}_constraint_lower`,
    constraint_upper: `sensor.heating_assistant_${slug}_constraint_upper`,
    model_fit_quality: `sensor.heating_assistant_${slug}_model_fit_quality`,
    heat_loss: `sensor.heating_assistant_${slug}_heat_loss`,
  },
});
const ent = (state, attributes = {}) => ({ state: String(state), attributes });

// ---- comfortDeviationC -------------------------------------------------------
assert(kpi.comfortDeviationC(21, 20, 22) === 0, 'in-band temperature must have zero deviation');
assert(kpi.comfortDeviationC(19, 20, 22) === 1, 'temperature 1C below band must deviate by 1');
assert(kpi.comfortDeviationC(23.5, 20, 22) === 1.5, 'temperature 1.5C above band must deviate by 1.5');

// ---- comfortIndexPct: one room in band, one out → 50% ------------------------
const roomA = room('living');
const roomB = room('kitchen');
{
  const state = {
    [roomA.entities.temperature_filtered]: ent('21.0'),
    [roomA.entities.constraint_lower]: ent('20.0'),
    [roomA.entities.constraint_upper]: ent('22.0'),
    [roomB.entities.temperature_filtered]: ent('18.0'),
    [roomB.entities.constraint_lower]: ent('20.0'),
    [roomB.entities.constraint_upper]: ent('22.0'),
  };
  assert(approx(kpi.comfortIndexPct(state, [roomA, roomB]), 50), 'one of two rooms in band must yield 50%');
  assert(kpi.comfortIndexPct(state, []) === null, 'empty room list must yield null comfort index');
  assert(kpi.comfortIndexPct({}, [roomA]) === null, 'no eligible rooms (missing data) must yield null');
  // houseComfortIndex falls back to the client computation without a summary attr…
  assert(approx(kpi.houseComfortIndex(state, [roomA, roomB]), 50), 'houseComfortIndex must fall back to client computation');
  // …but prefers the backend attribute when present.
  const withAttr = { ...state, [SUMMARY]: ent('0', { comfort_index_pct: '87.5' }) };
  assert(approx(kpi.houseComfortIndex(withAttr, [roomA, roomB]), 87.5), 'backend comfort_index_pct must win over client computation');
}

// ---- isRoomActive / inactive rooms excluded ----------------------------------
{
  const state = {
    [CONFIG]: ent('ok', { room_active: { living: false } }),
    [roomA.entities.temperature_filtered]: ent('15.0'),
    [roomA.entities.constraint_lower]: ent('20.0'),
    [roomA.entities.constraint_upper]: ent('22.0'),
    [roomB.entities.temperature_filtered]: ent('21.0'),
    [roomB.entities.constraint_lower]: ent('20.0'),
    [roomB.entities.constraint_upper]: ent('22.0'),
  };
  assert(kpi.isRoomActive(state, 'living') === false, 'room_active=false must mark the room inactive');
  assert(kpi.isRoomActive(state, 'kitchen') === true, 'room absent from room_active must default active');
  assert(approx(kpi.comfortIndexPct(state, [roomA, roomB]), 100), 'inactive out-of-band room must not drag the index down');
  const enabledOnly = { [CONFIG]: ent('ok', { room_enabled: { living: false } }) };
  assert(kpi.isRoomActive(enabledOnly, 'living') === false, 'legacy room_enabled map must be honoured');
}

// ---- roomTemperature precedence ----------------------------------------------
{
  const state = {
    [roomA.entities.temperature_filtered]: ent('21.4'),
    [roomA.entities.temperature_measured]: ent('20.0'),
  };
  assert(approx(kpi.roomTemperature(state, roomA), 21.4), 'filtered temperature must be preferred');
  const measuredOnly = { [roomA.entities.temperature_measured]: ent('20.0') };
  assert(approx(kpi.roomTemperature(measuredOnly, roomA), 20.0), 'must fall back to measured temperature');
  assert(kpi.roomTemperature({}, roomA) === null, 'no temperature entities must yield null');
}

// ---- tracking error: mean(|21.5-21|, |19-20|) = 0.75 --------------------------
{
  const state = {
    [roomA.entities.temperature_filtered]: ent('21.5'),
    [roomA.entities.setpoint]: ent('21.0'),
    [roomB.entities.temperature_filtered]: ent('19.0'),
    [roomB.entities.setpoint]: ent('20.0'),
  };
  assert(approx(kpi.computeMeanTrackingError(state, [roomA, roomB]), 0.75), 'mean tracking error must be 0.75');
  assert(kpi.computeMeanTrackingError({}, [roomA]) === null, 'no valid data must yield null tracking error');
  assert(approx(kpi.houseMeanTrackingError(state, [roomA, roomB]), 0.75), 'houseMeanTrackingError must fall back to client computation');
  const withAttr = { ...state, [MPC]: ent('0.5', { mean_tracking_error: 0.42 }) };
  assert(approx(kpi.houseMeanTrackingError(withAttr, [roomA, roomB]), 0.42), 'backend mean_tracking_error must win');
}

// ---- model fit aggregation -----------------------------------------------------
{
  const fit = kpi.aggregateModelFit([0.9, 0.7]);
  assert(approx(fit.value, 0.8, 1e-12), 'mean R2 of [0.9, 0.7] must be 0.8');
  assert(fit.label === 'ACCEPTABLE', `mean R2 0.8 must label ACCEPTABLE (label boundary is >0.8), got ${fit.label}`);
  const good = kpi.aggregateModelFit([0.95, null, 0.85]);
  assert(approx(good.value, 0.9), 'null entries must be excluded from the mean');
  assert(good.label === 'GOOD', 'mean R2 0.9 must label GOOD');
  const empty = kpi.aggregateModelFit([]);
  assert(empty.value === 0 && empty.label === '—', 'empty input must yield value 0 and em-dash label');

  const state = { [roomA.entities.model_fit_quality]: ent('0.6') };
  const house = kpi.houseModelFit(state, [roomA, roomB]);
  assert(approx(house.value, 0.6) && house.label === 'ACCEPTABLE', 'houseModelFit must use only rooms with data');
  const roomFit = kpi.roomModelFit(state, roomB);
  assert(roomFit.value === null && roomFit.label === '—', 'room without fit entity data must yield null/em-dash');
}

// ---- heating power -------------------------------------------------------------
{
  const state = { [SUMMARY]: ent('3456') };
  assert(approx(kpi.houseHeatingPowerW(state), 3456), 'summary state must be read as total W');
  assert(approx(kpi.houseHeatingPowerKw(state), 3.5), '3456 W must round to 3.5 kW');
  assert(kpi.houseHeatingPowerW({}) === null, 'missing summary must yield null power');
  const attrOnly = { [SUMMARY]: ent('unknown', { total_heating_power: 1200 }) };
  assert(approx(kpi.houseHeatingPowerW(attrOnly), 1200), 'total_heating_power attribute must be the fallback');
  // Fill: 5000 W live, no rated capacity → max = 10000 W default → 0.5
  const live = { [SUMMARY]: ent('5000') };
  assert(approx(kpi.houseHeatingPowerGaugeFill(live), 0.5), '5000 W over 10 kW default max must fill 0.5');
  assert(kpi.houseHeatingPowerGaugeFill({}) === null, 'missing power must yield null fill');
}

// ---- gauge max helpers -----------------------------------------------------------
assert(approx(kpi.heatLossGaugeMax(1000), 3000), 'heat-loss gauge max must floor at 3000 W');
assert(approx(kpi.heatLossGaugeMax(5000), 6000), 'heat-loss gauge max must scale 1.2x above the floor');
assert(approx(kpi.heatLossGaugeMax(null), 3000), 'invalid heat loss must fall back to the floor');
assert(approx(kpi.solarGainGaugeMax(200), 1000), 'solar gauge max must floor at 1000 W');
assert(approx(kpi.solarGainGaugeMax(2500), 2500), 'solar gauge max must track large gains');
assert(approx(kpi.houseHeatingPowerGaugeMax(500, 2000), 3000), 'rated capacity below the floor must clamp to 3000 W');
assert(approx(kpi.houseHeatingPowerGaugeMax(20000), 24000), 'live 20 kW must scale the max to 24 kW');
assert(approx(kpi.houseHeatingPowerGaugeMax(null), 10000), 'invalid live power must use the 10 kW default');

// ---- COP ------------------------------------------------------------------------
{
  const hp = { [SUMMARY]: ent('0', { has_heat_pump: true, effective_system_cop: '3.2' }) };
  assert(approx(kpi.houseEffectiveCop(hp), 3.2), 'COP must parse from the summary attribute');
  const resistive = { [SUMMARY]: ent('0', { has_heat_pump: false, effective_system_cop: 3.0 }) };
  assert(kpi.houseEffectiveCop(resistive) === null, 'resistive-only systems must hide the COP gauge');
  const zero = { [SUMMARY]: ent('0', { effective_system_cop: 0 }) };
  assert(kpi.houseEffectiveCop(zero) === null, 'COP of zero must hide the gauge');
  assert(kpi.houseEffectiveCop({}) === null, 'missing summary must hide the gauge');
}

// ---- MPC load ---------------------------------------------------------------------
{
  assert(approx(kpi.mpcLoadPercent({ [MPC]: ent('1.0') }), 50), '1.0 s solve of 2.0 s budget must be 50%');
  assert(approx(kpi.mpcLoadPercent({ [MPC]: ent('5.0') }), 100), 'over-budget solves must cap at 100%');
  assert(kpi.mpcLoadPercent({}) === 0, 'missing mpc_performance must read as 0% load');
}

// ---- room comfort deviation --------------------------------------------------------
{
  const state = {
    [roomA.entities.temperature_filtered]: ent('19.0'),
    [roomA.entities.constraint_lower]: ent('20.0'),
    [roomA.entities.constraint_upper]: ent('22.0'),
  };
  assert(approx(kpi.roomComfortDeviation(state, roomA, true), 1.0), 'computed comfort deviation must be 1.0');
  assert(kpi.roomComfortDeviation(state, roomA, false) === null, 'inactive room must yield null deviation');
  const withAttr = {
    ...state,
    [roomA.entities.temperature_filtered]: ent('19.0', { comfort_deviation: '0.25' }),
  };
  assert(approx(kpi.roomComfortDeviation(withAttr, roomA, true), 0.25), 'backend comfort_deviation attribute must win');
}

// ---- roomHeatLoss / roomTimeInRangePct ----------------------------------------------
{
  const state = { [roomA.entities.heat_loss]: ent('740') };
  assert(approx(kpi.roomHeatLoss(state, roomA), 740), 'roomHeatLoss must read the entity state');
  assert(kpi.roomHeatLoss(state, { slug: 'x', entities: {} }) === null, 'missing heat-loss entity must yield null');
  const tir = { [roomA.entities.temperature_filtered]: ent('21.0', { time_in_range_pct_24h: 96 }) };
  assert(approx(kpi.roomTimeInRangePct(tir, roomA, true), 96), 'time_in_range_pct_24h attribute must be read');
  assert(kpi.roomTimeInRangePct({}, roomA, true) === null, 'no attribute and no data must yield null');
}

console.log('panel kpi engine harness: ok');
