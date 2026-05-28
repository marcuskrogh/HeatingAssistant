const ENTITY_PREFIX = 'sensor.heating_assistant_';
const SYSTEM_METRICS = [
  'mpc_performance',
  'system_summary',
  'outdoor_temperature_measured',
  'outdoor_temperature_forecast',
  'estimated_parameters_status',
  'weather_forecast_status',
  'electricity_price',
  'electricity_price_forecast',
];

export function discoverRooms(states) {
  const roomMap = new Map();

  for (const entityId of Object.keys(states)) {
    if (!entityId.startsWith(ENTITY_PREFIX)) continue;

    const suffix = entityId.slice(ENTITY_PREFIX.length);
    if (SYSTEM_METRICS.some((m) => suffix === m)) continue;

    const metricSeparators = [
      '_temperature_measured',
      '_temperature_filtered',
      '_temperature_forecast',
      '_temperature_offset',
      '_heating_power_measured',
      '_heating_power_forecast',
      '_solar_gain_measured',
      '_solar_gain_forecast',
      '_setpoint',
      '_constraint_upper',
      '_constraint_lower',
      '_model_fit_quality',
      '_prediction_error',
      '_parameter_confidence',
      '_heat_loss',
      '_energy_balance',
      '_open_loop_rmse',
      '_kalman_innovation',
      '_residual_acf',
      '_window_state',
      '_control_action',
      '_heater_scale',
      '_energy_total',
    ];

    let roomSlug = null;
    for (const sep of metricSeparators) {
      if (suffix.endsWith(sep)) {
        roomSlug = suffix.slice(0, -sep.length);
        break;
      }
    }
    if (!roomSlug) continue;

    if (!roomMap.has(roomSlug)) {
      roomMap.set(roomSlug, {
        slug: roomSlug,
        name: slugToName(roomSlug),
        entities: {},
      });
    }

    const metric = suffix.slice(roomSlug.length + 1);
    roomMap.get(roomSlug).entities[metric] = entityId;
  }

  return Array.from(roomMap.values()).sort((a, b) =>
    a.name.localeCompare(b.name)
  );
}

function slugToName(slug) {
  return slug
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}
