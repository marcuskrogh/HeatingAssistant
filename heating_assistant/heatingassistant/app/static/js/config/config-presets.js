// Categorical thermal-model presets for room configuration.
const ROOM_SIZE_PRESETS = [
  { value: 'small', label: 'Small room', thermal_mass: 2500000, hint: 'bathroom, small bedroom' },
  { value: 'medium', label: 'Medium room', thermal_mass: 5000000, hint: 'bedroom, office' },
  { value: 'large', label: 'Large room', thermal_mass: 9000000, hint: 'living room' },
  { value: 'open', label: 'Open / open-plan', thermal_mass: 14000000, hint: 'open-plan, hall' },
];

const HOUSE_AGE_PRESETS = [
  { value: 'old', label: 'Old / poorly insulated', r_external: 0.03, tightness: 'leaky' },
  { value: 'standard', label: 'Standard insulation', r_external: 0.05, tightness: 'typical' },
  { value: 'modern', label: 'Modern / well insulated', r_external: 0.08, tightness: 'tight' },
  { value: 'passive', label: 'Passive house', r_external: 0.12, tightness: 'passive_house' },
];

function nearestPreset(presets, key, value) {
  if (value == null) return presets[1] || presets[0];
  let best = presets[0];
  let bestDiff = Infinity;
  for (const p of presets) {
    const d = Math.abs(Number(p[key]) - Number(value));
    if (d < bestDiff) { bestDiff = d; best = p; }
  }
  return best;
}

export { ROOM_SIZE_PRESETS, HOUSE_AGE_PRESETS, nearestPreset };
