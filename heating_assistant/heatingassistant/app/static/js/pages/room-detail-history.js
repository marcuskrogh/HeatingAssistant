/** Initial history+forecast fill for the room-detail charts. */

import {
  historyToDataPoints,
  historyToEnabledPoints,
  forecastToDataPoints,
  forecastToEnabledPoints,
  sensorHistoriesToMinMaxSpan,
  extendDatasetToNow,
} from '../components/time-series-chart.js?v=124';
import { entityValue, systemEntity } from '../utils.js?v=127';
import {
  buildTemperatureChart,
  buildPowerChart,
  buildDisturbanceChart,
} from '../charts/room-charts.js?v=140';

function appendCurrentValue(dataPoints, state, entityId) {
  extendDatasetToNow(dataPoints, entityValue(state, entityId));
  return dataPoints;
}

function slugifyRoomName(name) {
  return (name || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
}

// HA's history_during_period returns the initial boundary state with its
// original lu (last_updated) timestamp, which may predate the chart window by
// days for slowly-changing sensors like setpoint/constraints. Clamp it to the
// window start so the x-axis is not distorted.
function clampFirstToWindow(pts, windowStart) {
  if (pts.length > 0 && pts[0].x < windowStart) {
    pts[0] = { ...pts[0], x: windowStart };
  }
  return pts;
}

// Chart.js stepped:'before' + spanGaps:false draws each step toward the NEXT
// VALID point — not toward the next null. When the history has only one valid
// entry before an off-period null (common for sensors that rarely change), the
// segment has a single point and produces zero-width fill. Insert a synthetic
// closing point at (null.x − 1 ms) with the same y so every valid run has at
// least two points, matching the dense-point behaviour of the forecast data.
function closeStepSegments(pts) {
  const out = [];
  for (let i = 0; i < pts.length; i++) {
    out.push(pts[i]);
    if (pts[i].y !== null && i + 1 < pts.length && pts[i + 1].y === null) {
      out.push({ x: pts[i + 1].x - 1, y: pts[i].y });
    }
  }
  return out;
}

export async function loadChartsData(
  room,
  state,
  connection,
  tempChart,
  powerChart,
  disturbChart,
  lastRunTs,
  plotSettings,
  onPowerBounds,
  mpcForecastStamp,
) {
  const tempFilteredEntity = room.entities['temperature_filtered'];
  const tempMeasuredEntity = room.entities['temperature_measured'];
  const setpointEntity = room.entities['setpoint'];
  const constraintUpperEntity = room.entities['constraint_upper'];
  const constraintLowerEntity = room.entities['constraint_lower'];
  const powerMeasuredEntity = room.entities['heating_power_measured'];
  const solarMeasuredEntity = room.entities['solar_gain_measured'];
  const outdoorEntity = systemEntity('outdoor_temperature_measured');
  const priceEntity = systemEntity('electricity_price');

  const historyEntities = [
    tempFilteredEntity,
    tempMeasuredEntity,
    setpointEntity,
    constraintUpperEntity,
    constraintLowerEntity,
    powerMeasuredEntity,
    solarMeasuredEntity,
    outdoorEntity,
    priceEntity,
  ].filter(Boolean);

  const historyHours = (plotSettings && plotSettings.historyHours) || 12;
  const forecastHours = (plotSettings && plotSettings.forecastHours) || 0;
  const windowStart = Date.now() - historyHours * 3600 * 1000;

  const [history, forecasts] = await Promise.all([
    connection.getHistory(historyEntities, historyHours),
    connection.getForecasts(forecastHours),
  ]);

  const measuredHistory = historyToDataPoints(history[tempMeasuredEntity]);

  // Fetch raw sensor histories and build a min/max span when the room has
  // multiple configured temperature sensors. Align the band to the control
  // measurement timeline so it renders as one continuous shaded region.
  let sensorSpan = null;
  try {
    const modelCfg = await connection.getModelConfig();
    const roomCfg = modelCfg?.rooms?.find((r) => slugifyRoomName(r.name) === room.slug);
    let rawSensorEntities = [];
    if (roomCfg) {
      rawSensorEntities = Array.isArray(roomCfg.temp_sensors) ? [...roomCfg.temp_sensors] : [];
      if (roomCfg.temp_sensor && !rawSensorEntities.includes(roomCfg.temp_sensor)) {
        rawSensorEntities.unshift(roomCfg.temp_sensor);
      }
    }
    if (rawSensorEntities.length > 1 && measuredHistory.length > 0) {
      const rawHistory = await connection.getHistory(rawSensorEntities, historyHours);
      const sensorSeries = [];
      for (const entityId of rawSensorEntities) {
        const pts = historyToDataPoints(rawHistory[entityId]);
        if (pts.length > 0) sensorSeries.push(pts);
      }
      if (sensorSeries.length > 1) {
        sensorSpan = sensorHistoriesToMinMaxSpan(sensorSeries, measuredHistory);
        lastRunTs.sensorEntities = rawSensorEntities;
      }
    }
  } catch (e) {
    // Graceful degradation: skip the sensor span if config fetch fails.
  }

  // Seed lastRunTs so the first state-change event that matches the initial
  // control / NMPC stamps is treated as a no-op rather than an immediate re-fetch.
  lastRunTs.value = mpcForecastStamp(state);

  const filteredHistory = historyToDataPoints(history[tempFilteredEntity]);
  const setpointHistory = closeStepSegments(clampFirstToWindow(historyToEnabledPoints(history[setpointEntity]), windowStart));
  const constraintUpperHistory = closeStepSegments(clampFirstToWindow(historyToEnabledPoints(history[constraintUpperEntity]), windowStart));
  const constraintLowerHistory = closeStepSegments(clampFirstToWindow(historyToEnabledPoints(history[constraintLowerEntity]), windowStart));
  const powerHistory = appendCurrentValue(historyToDataPoints(history[powerMeasuredEntity]), state, powerMeasuredEntity);
  const solarHistory = appendCurrentValue(historyToDataPoints(history[solarMeasuredEntity]), state, solarMeasuredEntity);
  const outdoorHistory = appendCurrentValue(historyToDataPoints(history[outdoorEntity]), state, outdoorEntity);
  const priceHistory = appendCurrentValue(historyToDataPoints(history[priceEntity]), state, priceEntity);

  const roomForecast = forecasts.rooms?.[room.slug];

  const forecastData = roomForecast?.forecast || [];
  const priceForecastData = forecasts.price_forecast || [];
  const priceForecast = forecastToDataPoints(priceForecastData, 'price');

  // Hand the room capacity and the price-forecast range to the KPI gauges.
  if (onPowerBounds) onPowerBounds(roomForecast, priceForecast);

  const tempForecastNonlinear = forecastToDataPoints(forecastData, 'temperature');
  const tempForecastLinearised = forecastToDataPoints(forecastData, 'linearised_temperature');
  const setpointForecast = forecastToEnabledPoints(forecastData, 'setpoint');
  const constraintUpperForecast = forecastToEnabledPoints(forecastData, 'constraint_upper');
  const constraintLowerForecast = forecastToEnabledPoints(forecastData, 'constraint_lower');
  const powerForecast = forecastToDataPoints(forecastData, 'heating_power');
  const solarForecast = forecastToDataPoints(forecastData, 'solar_gain');
  const outdoorForecast = forecastToDataPoints(forecastData, 'outdoor_temp');

  buildTemperatureChart(
    tempChart,
    filteredHistory, measuredHistory,
    setpointHistory, setpointForecast,
    tempForecastNonlinear, tempForecastLinearised,
    constraintUpperHistory, constraintUpperForecast,
    constraintLowerHistory, constraintLowerForecast,
    sensorSpan,
  );
  buildPowerChart(powerChart, powerHistory, powerForecast, priceHistory, priceForecast, roomForecast, windowStart);
  buildDisturbanceChart(disturbChart, outdoorHistory, outdoorForecast, solarHistory, solarForecast);
}
