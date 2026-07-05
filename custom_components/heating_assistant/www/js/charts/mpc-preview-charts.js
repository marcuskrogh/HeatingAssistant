/**
 * MPC preview chart builders shared by room detail and tuning pages.
 *
 * Implementation lives in room-detail.js today; this module provides a stable
 * import path so tuning does not depend on the full room page.
 */
export {
  buildTemperatureChart,
  buildPowerChart,
  buildDisturbanceChart,
} from '../pages/room-detail.js?v=89';
