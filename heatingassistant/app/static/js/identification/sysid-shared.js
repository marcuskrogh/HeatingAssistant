// Shared constants and helpers for parameter estimation pages.

export const DEFAULTS = {
  sigma_w: 0.1,
  sigma_v: 0.5,
  thermal_mass: 5000000,
  r_external: 0.05,
  internal_gain: 0,
  solar_scale: 1.0,
  c_air_fraction: 0.05,
  r_aw_fraction: 0.05,
  heater_scale: 1.0,
  horizon_hours: 6,
};

export const CONFIG_ENTITY = 'sensor.heating_assistant_controller_config';
const DISMISSED_WARNINGS_KEY = 'heating_assistant_sysid_dismissed_v1';

export function valuesEqual(a, b) {
  const na = Number(a);
  const nb = Number(b);
  if (Number.isFinite(na) && Number.isFinite(nb)) {
    return Math.abs(na - nb) <= 1e-9 * Math.max(1, Math.abs(na), Math.abs(nb));
  }
  return a === b;
}

export function loadDismissedWarnings(slug) {
  try {
    const raw = localStorage.getItem(`${DISMISSED_WARNINGS_KEY}_${slug}`);
    return raw ? new Set(JSON.parse(raw)) : new Set();
  } catch (_) {
    return new Set();
  }
}

export function saveDismissedWarning(slug, code) {
  const dismissed = loadDismissedWarnings(slug);
  dismissed.add(code);
  try {
    localStorage.setItem(`${DISMISSED_WARNINGS_KEY}_${slug}`, JSON.stringify([...dismissed]));
  } catch (_) {}
}
