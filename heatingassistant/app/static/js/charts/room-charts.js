import { makeDataset } from '../components/time-series-chart.js?v=124';
import { wattsToKw, wattsToKwPoints } from '../utils.js?v=124';

export function extendDatasetToNow(pts, value, now = Date.now()) {
  if (value === null || value === undefined) return;
  if (pts.length === 0 || pts[pts.length - 1].x < now) {
    pts.push({ x: now, y: value });
  } else {
    pts[pts.length - 1] = { x: now, y: value };
  }
}

export function computeYLimits(allDataPoints, bounds, marginFraction = 0.05) {
  let minVal = Infinity;
  let maxVal = -Infinity;

  for (const points of allDataPoints) {
    for (const p of points) {
      if (p && p.y != null) {
        if (p.y < minVal) minVal = p.y;
        if (p.y > maxVal) maxVal = p.y;
      }
    }
  }

  for (const b of bounds) {
    if (b != null) {
      if (b < minVal) minVal = b;
      if (b > maxVal) maxVal = b;
    }
  }

  if (!isFinite(minVal) || !isFinite(maxVal)) return { yMin: undefined, yMax: undefined };

  const range = maxVal - minVal || 1;
  const margin = range * marginFraction;
  return {
    yMin: minVal - margin,
    yMax: maxVal + margin,
  };
}

export function buildTemperatureChart(
  chart,
  filteredHistory, measuredHistory,
  setpointHistory, setpointForecast,
  forecastNonlinear, forecastLinearised,
  constraintUpperHistory, constraintUpperForecast,
  constraintLowerHistory, constraintLowerForecast,
  sensorSpan,
  options = {},
) {
  const forecastOnly = options.forecastOnly === true;
  const combinedSetpoint = forecastOnly
    ? setpointForecast
    : [...setpointHistory, ...setpointForecast];
  const combinedUpper = forecastOnly
    ? constraintUpperForecast
    : [...constraintUpperHistory, ...constraintUpperForecast];
  const combinedLower = forecastOnly
    ? constraintLowerForecast
    : [...constraintLowerHistory, ...constraintLowerForecast];

  const spanPts = sensorSpan ? [sensorSpan.min, sensorSpan.max] : [];
  const allData = forecastOnly
    ? [combinedSetpoint, forecastNonlinear, forecastLinearised, combinedUpper, combinedLower]
    : [
      filteredHistory, measuredHistory,
      combinedSetpoint, forecastNonlinear, forecastLinearised,
      combinedUpper, combinedLower,
      ...spanPts,
    ];
  const { yMin, yMax } = computeYLimits(allData, []);

  const datasets = [];
  if (!forecastOnly) {
    datasets.push(
      makeDataset('Filtered', filteredHistory, '#4fc3f7', { borderWidth: 2 }),
      makeDataset('Measured', measuredHistory, '#e57373', {
        borderWidth: 0, pointRadius: 3, pointHoverRadius: 5,
        pointBackgroundColor: '#e57373', pointBorderColor: '#e57373',
        showLine: false,
      }),
    );
  }
  datasets.push(
    makeDataset('Forecast', forecastNonlinear, '#4fc3f7', {
      dashed: !forecastOnly, borderWidth: 2,
    }),
  );

  if (forecastLinearised.length > 0) {
    datasets.push(
      makeDataset('Linearised', forecastLinearised, '#ab47bc', {
        dashed: !forecastOnly, borderWidth: 1.5,
      }),
    );
  }

  if (combinedSetpoint.length > 0) {
    datasets.push(
      makeDataset('Setpoint', combinedSetpoint, '#e57373', {
        dashed: !forecastOnly, borderWidth: 1, pointRadius: 0, stepped: 'before', spanGaps: false,
      }),
    );
  }

  // Shade outside the comfort corridor without drawing visible boundary lines.
  // In Chart.js 4, fill.above/below refer to whether the DATASET is above/below
  // the TARGET — not the direction of the fill area. The upper constraint is
  // always below 'end' (chart top), so 'below' color applies; the lower
  // constraint is always above 'start' (chart bottom), so 'above' color applies.
  // spanGaps: false ensures that null points (off/disabled periods) create breaks
  // in the line and fill rather than bridging across them.
  if (combinedUpper.length > 0) {
    datasets.push(
      makeDataset('Constraint Upper', combinedUpper, 'transparent', {
        borderWidth: 0, pointRadius: 0, stepped: 'before', spanGaps: false,
        fill: { target: 'end', above: 'transparent', below: 'rgba(229,115,115,0.12)' },
      })
    );
  }
  if (combinedLower.length > 0) {
    datasets.push(
      makeDataset('Constraint Lower', combinedLower, 'transparent', {
        borderWidth: 0, pointRadius: 0, stepped: 'before', spanGaps: false,
        fill: { target: 'start', above: 'rgba(229,115,115,0.12)', below: 'transparent' },
      })
    );
  }

  if (sensorSpan && sensorSpan.min.length > 0 && sensorSpan.max.length > 0) {
    datasets.push(
      makeDataset('Sensor Min', sensorSpan.min, 'transparent', {
        borderWidth: 0, pointRadius: 0, tension: 0, order: 9,
      }),
      makeDataset('Sensor Range', sensorSpan.max, 'transparent', {
        borderWidth: 0,
        pointRadius: 0,
        tension: 0,
        fill: '-1',
        backgroundColor: 'rgba(79, 195, 247, 0.18)',
        order: 9,
      })
    );
  }

  chart.render(datasets, { yMin, yMax });
}

/**
 * Build time-series points for the dynamic capacity corridor.
 *
 * Returns two arrays of {x, y} points:
 *   heating — per-step COP-limited rated heating capacity (no power_scale)
 *   cooling — per-step rated cooling capacity, negated to a negative y value
 *
 * The historical window (from `windowStart` to the first forecast entry) is held
 * flat at the current-snapshot capacity; the forecast window uses per-step values
 * computed from the outdoor-temperature forecast so the corridor visibly moves
 * with outdoor temperature.
 */
function buildCapacityPoints(roomForecast, windowStart, forecastOnly = false) {
  const currentHeat = (
    roomForecast?.current_rated_max_power
    ?? roomForecast?.current_max_power
    ?? roomForecast?.max_power
    ?? null
  );
  const currentCool = (
    roomForecast?.max_cooling_power
    ?? roomForecast?.current_max_cooling_power
    ?? null
  );

  const heating = [];
  const cooling = [];

  if (!forecastOnly) {
    if (currentHeat != null) heating.push({ x: windowStart, y: wattsToKw(currentHeat) });
    if (currentCool != null) cooling.push({ x: windowStart, y: -wattsToKw(currentCool) });
  }

  for (const entry of (roomForecast?.forecast ?? [])) {
    const t = new Date(entry.time).getTime();
    if (entry.heating_capacity != null) heating.push({ x: t, y: wattsToKw(entry.heating_capacity) });
    if (entry.cooling_capacity != null) cooling.push({ x: t, y: -wattsToKw(entry.cooling_capacity) });
  }

  return { heating, cooling };
}

/**
 * Heating-side Y-axis anchor for the power chart [kW].
 *
 * Prefer the COP-limited rated capacity at the present outdoor temperature
 * (``current_rated_max_power``) so full command fills the scale.  Raw
 * ``max_power`` is the configured datasheet maximum at the rated COP point
 * and can be much higher than what a heat pump can deliver in cold weather,
 * which makes traces look capped around half the axis even at u = 1.
 */
function resolveHeatingPlotMaxKw(roomForecast) {
  return wattsToKw(
    roomForecast?.current_rated_max_power
    ?? roomForecast?.max_power
    ?? null,
  );
}

/** Refresh the power-chart Y limits and dynamic capacity corridor after a forecast update. */
export function updatePowerChartBounds(chart, roomForecast, windowStart) {
  if (!chart._chart || !roomForecast) return;

  const maxPower = resolveHeatingPlotMaxKw(roomForecast);
  const maxCoolingPower = wattsToKw(roomForecast?.max_cooling_power ?? null);
  const minPower = maxCoolingPower !== null ? -maxCoolingPower : 0;

  const ds = chart._chart.data.datasets;
  const measuredIdx = ds.findIndex((d) => d.label === 'Measured Power');
  const plannedIdx = ds.findIndex((d) => d.label === 'Planned Power');
  const powerSeries = [
    measuredIdx >= 0 ? ds[measuredIdx].data : [],
    plannedIdx >= 0 ? ds[plannedIdx].data : [],
  ];
  const boundsArr = [maxPower, minPower, 0];
  const { yMin, yMax } = computeYLimits(powerSeries, boundsArr);

  if (chart._chart.options?.scales?.y) {
    chart._chart.options.scales.y.min = yMin;
    chart._chart.options.scales.y.max = yMax;
  }

  const historyStart = windowStart ?? chart._historyWindowStart ?? (Date.now() - 12 * 3600 * 1000);
  const { heating: heatingCapPts, cooling: coolingCapPts } = buildCapacityPoints(roomForecast, historyStart);

  const heatCapIdx = ds.findIndex((d) => d.label === 'Heating Capacity');
  if (heatCapIdx >= 0) ds[heatCapIdx].data = heatingCapPts;

  const coolCapIdx = ds.findIndex((d) => d.label === 'Cooling Capacity');
  if (coolCapIdx >= 0) ds[coolCapIdx].data = coolingCapPts;

  chart._chart.update('none');
}

export function buildPowerChart(
  chart, powerHistory, powerForecast, priceHistory, priceForecast, roomForecast, windowStart,
  options = {},
) {
  const forecastOnly = options.forecastOnly === true;
  const maxPower = resolveHeatingPlotMaxKw(roomForecast);
  const maxCoolingPower = wattsToKw(roomForecast?.max_cooling_power ?? null);
  const minPower = maxCoolingPower !== null ? -maxCoolingPower : 0;

  const powerHistoryKw = wattsToKwPoints(powerHistory);
  const powerForecastKw = wattsToKwPoints(powerForecast);
  const allPower = forecastOnly ? [powerForecastKw] : [powerHistoryKw, powerForecastKw];
  const boundsArr = [maxPower, minPower, 0];
  const { yMin, yMax } = computeYLimits(allPower, boundsArr);

  const allPrice = forecastOnly ? priceForecast : [...priceHistory, ...priceForecast];
  const { yMin: priceMin, yMax: priceMax } = computeYLimits([allPrice], [0]);

  const datasets = [];
  if (!forecastOnly) {
    datasets.push(
      makeDataset('Measured Power', powerHistoryKw, '#ffb74d', {
        borderWidth: 2, stepped: 'before',
        fill: true, backgroundColor: 'rgba(255,183,77,0.08)',
      }),
    );
  }
  datasets.push(
    makeDataset('Planned Power', powerForecastKw, '#ffb74d', {
      dashed: !forecastOnly, borderWidth: 2, stepped: 'before',
      ...(forecastOnly ? { fill: true, backgroundColor: 'rgba(255,183,77,0.08)' } : {}),
    }),
  );
  if (!forecastOnly) {
    datasets.push(
      makeDataset('Price', priceHistory, '#81c784', {
        borderWidth: 2, yAxisID: 'y2', stepped: 'before',
      }),
    );
  }
  datasets.push(
    makeDataset(forecastOnly ? 'Price' : 'Price Forecast', priceForecast, '#81c784', {
      dashed: !forecastOnly, borderWidth: forecastOnly ? 2 : 1.5, yAxisID: 'y2', stepped: 'before',
    }),
  );

  // Dynamic capacity corridor: invisible boundary datasets whose fill extends to
  // the axis edge so infeasible regions are shaded without dashed reference lines.
  const historyStart = windowStart ?? (Date.now() - 12 * 3600 * 1000);
  chart._historyWindowStart = historyStart;
  const { heating: heatingCapPts, cooling: coolingCapPts } = buildCapacityPoints(
    roomForecast, historyStart, forecastOnly,
  );

  if (heatingCapPts.length > 0) {
    datasets.push(makeDataset('Heating Capacity', heatingCapPts, 'transparent', {
      fill: 'end',
      backgroundColor: 'rgba(229,115,115,0.12)',
      borderWidth: 0,
      pointRadius: 0,
      tension: 0,
      stepped: 'before',
      order: 10,
    }));
  }
  // Only shade the cooling bound when the room has cooling capacity.
  if (coolingCapPts.length > 0) {
    datasets.push(makeDataset('Cooling Capacity', coolingCapPts, 'transparent', {
      fill: 'start',
      backgroundColor: 'rgba(229,115,115,0.12)',
      borderWidth: 0,
      pointRadius: 0,
      tension: 0,
      stepped: 'before',
      order: 10,
    }));
  }

  chart.render(datasets, { yMin, yMax, y2Min: priceMin, y2Max: priceMax });
}

export function buildDisturbanceChart(
  chart, outdoorHistory, outdoorForecast, solarHistory, solarForecast,
  options = {},
) {
  const forecastOnly = options.forecastOnly === true;
  const allOutdoor = forecastOnly ? outdoorForecast : [...outdoorHistory, ...outdoorForecast];
  const solarHistoryKw = wattsToKwPoints(solarHistory);
  const solarForecastKw = wattsToKwPoints(solarForecast);
  const allSolar = forecastOnly ? solarForecastKw : [...solarHistoryKw, ...solarForecastKw];

  const { yMin: outdoorMin, yMax: outdoorMax } = computeYLimits([allOutdoor], []);
  const { yMin: solarMin, yMax: solarMax } = computeYLimits([allSolar], [0]);

  const datasets = [];
  if (!forecastOnly) {
    datasets.push(
      makeDataset('Outdoor Temperature', outdoorHistory, '#90a4ae', { borderWidth: 2 }),
    );
  }
  datasets.push(
    makeDataset(forecastOnly ? 'Outdoor Temperature' : 'Outdoor Forecast', outdoorForecast, '#90a4ae', {
      dashed: !forecastOnly, borderWidth: 2,
    }),
  );
  if (!forecastOnly) {
    datasets.push(
      makeDataset('Solar Gain', solarHistoryKw, '#ffd54f', {
        borderWidth: 2, yAxisID: 'y2',
        fill: true, backgroundColor: 'rgba(255,213,79,0.08)',
      }),
    );
  }
  datasets.push(
    makeDataset(forecastOnly ? 'Solar Gain' : 'Solar Gain Forecast', solarForecastKw, '#ffd54f', {
      dashed: !forecastOnly, borderWidth: 2, yAxisID: 'y2',
      ...(forecastOnly ? { fill: true, backgroundColor: 'rgba(255,213,79,0.08)' } : {}),
    }),
  );

  chart.render(datasets, { yMin: outdoorMin, yMax: outdoorMax, y2Min: solarMin, y2Max: solarMax });
}
