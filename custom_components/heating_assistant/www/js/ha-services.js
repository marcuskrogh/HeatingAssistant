/** Centralized hass.callService wrappers for heating_assistant and climate domains. */

function ha(hass, service, data = {}) {
  return hass.callService('heating_assistant', service, data);
}

export async function setSystemEnabled(hass, enabled) {
  return ha(hass, 'set_system_enabled', { enabled });
}

export async function updateControllerTuning(hass, data) {
  return ha(hass, 'update_controller_tuning', data);
}

export async function updateEstimationParams(hass, data) {
  return ha(hass, 'update_estimation_params', data);
}

export async function setRoomComfortOffset(hass, roomName, comfortOffset) {
  return ha(hass, 'set_room_comfort_offset', {
    room_name: roomName,
    comfort_offset: comfortOffset,
  });
}

export async function updateUiSettings(hass, data) {
  return ha(hass, 'update_ui_settings', data);
}

export async function updateRooms(hass, data) {
  return ha(hass, 'update_rooms', data);
}

export async function updateHeatSources(hass, data) {
  return ha(hass, 'update_heat_sources', data);
}

export async function updateSystemParams(hass, data) {
  return ha(hass, 'update_system_params', data);
}

export async function updateSystemConfig(hass, data) {
  return ha(hass, 'update_system_config', data);
}

export async function setScheduleEnabled(hass, roomName, enabled) {
  return ha(hass, 'set_schedule_enabled', { room_name: roomName, enabled });
}

export async function updateRoomSchedule(hass, roomName, periods) {
  return ha(hass, 'update_room_schedule', {
    room_name: roomName,
    periods,
  });
}

export async function scheduleExperiment(hass, data) {
  return ha(hass, 'schedule_experiment', data);
}

export async function cancelExperiment(hass, experimentId) {
  return ha(hass, 'cancel_experiment', { experiment_id: experimentId });
}

export async function deleteExperiment(hass, experimentId) {
  return ha(hass, 'delete_experiment', { experiment_id: experimentId });
}

export async function deleteParameterHistory(hass, historyIndex) {
  return ha(hass, 'delete_parameter_history', { history_index: historyIndex });
}

export async function estimateParametersMl(hass, data = {}) {
  return ha(hass, 'estimate_parameters_ml', data);
}

export async function storeIdentifiedParameters(hass, data) {
  return ha(hass, 'store_identified_parameters', data);
}

export async function runSysidSimulation(hass, data) {
  return ha(hass, 'run_sysid_simulation', data);
}

export async function runOpenLoopSimulation(hass, data) {
  return ha(hass, 'run_open_loop_simulation', data);
}

export async function createDataset(hass, data) {
  return hass.callService('heating_assistant', 'create_dataset', data, undefined, true);
}

export async function deleteDataset(hass, datasetId) {
  return ha(hass, 'delete_dataset', { dataset_id: datasetId });
}

export async function setClimateTemperature(hass, entityId, temperature) {
  return hass.callService('climate', 'set_temperature', {
    entity_id: entityId,
    temperature,
  });
}

export async function turnClimateOn(hass, entityId) {
  return hass.callService('climate', 'turn_on', { entity_id: entityId });
}

export async function turnClimateOff(hass, entityId) {
  return hass.callService('climate', 'turn_off', { entity_id: entityId });
}
