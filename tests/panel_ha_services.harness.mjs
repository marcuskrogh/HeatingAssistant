/**
 * Regression harness for the REAL ha-services.js and ha-connection.js:
 * service-call payload shapes, WebSocket message shapes, and the
 * error-handling contracts (which fetches return {} vs null on failure —
 * callers rely on that distinction to avoid wiping populated lists).
 *
 * Run: node tests/panel_ha_services.harness.mjs
 */
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const WWW = join(ROOT, 'heatingassistant/app/static/js');

function assert(cond, msg) {
  if (!cond) { console.error('FAIL:', msg); process.exit(1); }
}
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// Error paths intentionally console.warn; keep harness output clean and count them.
let warnings = 0;
console.warn = () => { warnings += 1; };

const svc = await import(`${pathToFileURL(join(WWW, 'ha-services.js')).href}?v=96`);
const { HaConnection } = await import(`${pathToFileURL(join(WWW, 'ha-connection.js')).href}?v=96`);

// ---- ha-services: payload shapes ---------------------------------------------
{
  const calls = [];
  const hass = { callService: (...args) => { calls.push(args); return Promise.resolve('ok'); } };

  await svc.setSystemEnabled(hass, true);
  assert(eq(calls.at(-1), ['heating_assistant', 'set_system_enabled', { enabled: true }]),
    'setSystemEnabled payload shape');

  await svc.setRoomSetpoint(hass, 'living', 21.5);
  assert(eq(calls.at(-1), ['heating_assistant', 'set_room_setpoint', { room_name: 'living', setpoint: 21.5 }]),
    'setRoomSetpoint payload shape');

  await svc.setRoomComfortOffset(hass, 'living', 1.5);
  assert(eq(calls.at(-1), ['heating_assistant', 'set_room_comfort_offset', { room_name: 'living', comfort_offset: 1.5 }]),
    'setRoomComfortOffset payload shape');

  await svc.setRoomEnabled(hass, 'living', false);
  assert(eq(calls.at(-1), ['heating_assistant', 'set_room_enabled',
    { room_name: 'living', enabled: false, restore_default_setpoint: false }]),
    'setRoomEnabled must default restore_default_setpoint to false');

  await svc.setRoomEnabled(hass, 'living', true, { restoreDefaultSetpoint: true });
  assert(calls.at(-1)[2].restore_default_setpoint === true,
    'setRoomEnabled must pass restore_default_setpoint through');

  await svc.revertParameters(hass, 3);
  assert(eq(calls.at(-1), ['heating_assistant', 'revert_parameters', { history_index: 3 }]),
    'revertParameters payload shape');

  await svc.updateRoomSchedule(hass, 'living', [{ start: '06:00', end: '08:00' }]);
  assert(eq(calls.at(-1)[2], { room_name: 'living', periods: [{ start: '06:00', end: '08:00' }] }),
    'updateRoomSchedule payload shape');

  await svc.cancelExperiment(hass, 'exp-1');
  assert(eq(calls.at(-1), ['heating_assistant', 'cancel_experiment', { experiment_id: 'exp-1' }]),
    'cancelExperiment payload shape');

  await svc.resetEstimatedParameters(hass);
  assert(eq(calls.at(-1), ['heating_assistant', 'reset_estimated_parameters', {}]),
    'resetEstimatedParameters must send an empty data object');

  // create_dataset requests a service response (5th positional arg true).
  await svc.createDataset(hass, { name: 'ds' });
  const dsCall = calls.at(-1);
  assert(dsCall[0] === 'heating_assistant' && dsCall[1] === 'create_dataset'
    && eq(dsCall[2], { name: 'ds' }) && dsCall[3] === undefined && dsCall[4] === true,
    'createDataset must request a service response (return_response flag)');

  // Climate-domain wrappers.
  await svc.setClimateTemperature(hass, 'climate.heating_assistant_living', 22);
  assert(eq(calls.at(-1), ['climate', 'set_temperature',
    { entity_id: 'climate.heating_assistant_living', temperature: 22 }]),
    'setClimateTemperature must call the climate domain');
  await svc.turnClimateOff(hass, 'climate.x');
  assert(eq(calls.at(-1), ['climate', 'turn_off', { entity_id: 'climate.x' }]), 'turnClimateOff shape');
  await svc.turnClimateOn(hass, 'climate.x');
  assert(eq(calls.at(-1), ['climate', 'turn_on', { entity_id: 'climate.x' }]), 'turnClimateOn shape');
}

// ---- ha-services: rejection must propagate to the caller -----------------------
{
  const hass = { callService: () => Promise.reject(new Error('service failed')) };
  let rejected = false;
  await svc.setRoomSetpoint(hass, 'living', 21).catch(() => { rejected = true; });
  assert(rejected, 'service wrapper must propagate callService rejection to the caller');
}

// ---- HaConnection: happy-path WS payload shapes --------------------------------
{
  const wsCalls = [];
  const responses = {
    'heating_assistant/get_schedules': { room_schedules: { living: { periods: [] } } },
    'heating_assistant/list_datasets': { datasets: [{ id: 'd1' }] },
    'heating_assistant/get_dataset': { dataset: { id: 'd1' } },
    'heating_assistant/list_experiments': { experiments: [{ id: 'e1' }] },
    'heating_assistant/get_forecasts': { rooms: {} },
    'heating_assistant/preview_tuning_forecast': { rooms: {} },
    'heating_assistant/get_ui_settings': { ui_settings: { plot_history_hours: 12 } },
    'heating_assistant/get_model_config': { rooms: [] },
    'heating_assistant/get_controller_config': { config: { horizon: 24 } },
    'history/history_during_period': { 'sensor.a': [] },
  };
  const hass = {
    states: { 'sensor.a': { state: '1' } },
    callWS: (msg) => { wsCalls.push(msg); return Promise.resolve(responses[msg.type]); },
  };
  const conn = new HaConnection(hass);

  assert(eq(await conn.getSchedules(), { living: { periods: [] } }), 'getSchedules must unwrap room_schedules');
  assert(eq(await conn.listDatasets(), [{ id: 'd1' }]), 'listDatasets must unwrap datasets');
  assert(!('room_slug' in wsCalls.at(-1)), 'listDatasets without a slug must omit room_slug');
  await conn.listDatasets('living');
  assert(wsCalls.at(-1).room_slug === 'living', 'listDatasets must pass room_slug when given');
  assert((await conn.getDataset('d1')).id === 'd1', 'getDataset must unwrap dataset');
  assert(wsCalls.at(-1).dataset_id === 'd1', 'getDataset must send dataset_id');
  assert(eq(await conn.listExperiments(), [{ id: 'e1' }]), 'listExperiments must unwrap experiments');
  assert(eq(await conn.getControllerConfig(), { horizon: 24 }), 'getControllerConfig must unwrap config');
  assert((await conn.getUiSettings()).plot_history_hours === 12, 'getUiSettings must unwrap ui_settings');
  assert((await conn.getModelConfig()) !== null, 'getModelConfig must return the raw object');

  await conn.getForecasts();
  assert(!('plot_forecast_hours' in wsCalls.at(-1)), 'getForecasts must omit plot_forecast_hours by default');
  await conn.getForecasts(0);
  assert(!('plot_forecast_hours' in wsCalls.at(-1)), 'getForecasts must treat 0 as full horizon');
  await conn.getForecasts('48');
  assert(wsCalls.at(-1).plot_forecast_hours === 48, 'getForecasts must coerce a positive horizon to Number');

  await conn.previewTuningForecast({ tracking_weight: 2.0 }, 24);
  const preview = wsCalls.at(-1);
  assert(preview.type === 'heating_assistant/preview_tuning_forecast'
    && preview.tracking_weight === 2.0 && preview.plot_forecast_hours === 24,
    'previewTuningForecast must spread tuning params into the WS message');

  const start = new Date('2026-07-12T00:00:00Z');
  await conn.getHistorySince(['sensor.a'], start);
  const hist = wsCalls.at(-1);
  assert(hist.type === 'history/history_during_period'
    && hist.start_time === start.toISOString()
    && eq(hist.entity_ids, ['sensor.a'])
    && hist.minimal_response === true
    && hist.significant_changes_only === false,
    'getHistorySince WS message shape');

  await conn.getHistoryRange(['sensor.a'], start, new Date('2026-07-12T06:00:00Z'));
  assert(wsCalls.at(-1).end_time === '2026-07-12T06:00:00.000Z', 'getHistoryRange must send the explicit end');

  assert(conn.getEntityState('sensor.a').state === '1', 'getEntityState must read hass.states');
  assert(conn.getEntityState('sensor.missing') === null, 'getEntityState must return null when missing');

  // updateHass swaps the backing hass object for subsequent calls.
  conn.updateHass({ states: {}, callWS: () => Promise.resolve({ room_schedules: { x: 1 } }) });
  assert(eq(await conn.getSchedules(), { x: 1 }), 'updateHass must swap the backing hass');
}

// ---- HaConnection: getControllerConfig legacy sendMessagePromise fallback ------
{
  const sent = [];
  const conn = new HaConnection({
    connection: { sendMessagePromise: (msg) => { sent.push(msg); return Promise.resolve({ config: { horizon: 6 } }); } },
  });
  assert(eq(await conn.getControllerConfig(), { horizon: 6 }),
    'getControllerConfig must fall back to connection.sendMessagePromise without callWS');
  assert(sent.length === 1, 'fallback must send exactly one message');
}

// ---- HaConnection: error handling — {} vs null contracts ------------------------
{
  const failing = new HaConnection({ callWS: () => Promise.reject(new Error('disconnected')) });
  const before = warnings;
  assert(eq(await failing.getSchedules(), {}), 'getSchedules must return {} on failure');
  assert((await failing.listDatasets()) === null, 'listDatasets must return null (not []) on failure');
  assert((await failing.listExperiments()) === null, 'listExperiments must return null (not []) on failure');
  assert((await failing.getDataset('d1')) === null, 'getDataset must return null on failure');
  assert((await failing.getControllerConfig()) === null, 'getControllerConfig must return null on failure');
  assert((await failing.getUiSettings()) === null, 'getUiSettings must return null on failure');
  assert((await failing.getModelConfig()) === null, 'getModelConfig must return null on failure');
  assert(eq(await failing.getForecasts(12), {}), 'getForecasts must return {} on failure');
  assert((await failing.previewTuningForecast({}, 12)) === null, 'previewTuningForecast must return null on failure');
  assert(eq(await failing.getHistory(['sensor.a']), {}), 'getHistory must return {} on failure');
  assert(eq(await failing.getHistorySince(['sensor.a'], new Date()), {}), 'getHistorySince must return {} on failure');
  assert(eq(await failing.getHistoryRange(['sensor.a'], new Date(), new Date()), {}), 'getHistoryRange must return {} on failure');
  assert(warnings > before, 'failed fetches must warn instead of throwing');
}

// ---- HaConnection: malformed (non-error) responses ------------------------------
{
  const odd = new HaConnection({ callWS: () => Promise.resolve({ unexpected: true }) });
  assert(eq(await odd.getSchedules(), {}), 'getSchedules must return {} when room_schedules is missing');
  assert((await odd.listDatasets()) === null, 'listDatasets must return null when datasets is not an array');
  assert((await odd.listExperiments()) === null, 'listExperiments must return null when experiments is not an array');
  assert(eq(await odd.getControllerConfig(), {}), 'getControllerConfig must return {} for a shape mismatch');
  assert((await odd.getUiSettings()) === null, 'getUiSettings must return null when ui_settings is missing');
}

// ---- HaConnection: subscribe wiring ---------------------------------------------
{
  let unsubbed = 0;
  const events = [];
  const conn = new HaConnection({
    connection: {
      subscribeEvents: (cb, type) => { events.push(type); cb('evt'); return Promise.resolve(() => { unsubbed += 1; }); },
    },
  });
  const unsub = await conn.subscribe(() => {});
  assert(eq(events, ['state_changed']), 'subscribe must listen for state_changed');
  unsub();
  assert(unsubbed === 1, 'returned unsubscribe must call through');
}

console.log('panel ha services harness: ok');
