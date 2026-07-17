import { renderIdentificationIndex } from '../identification/sysid-index.js?v=108';
import { renderIdentificationDetail } from '../identification/sysid-detail.js?v=108';

export function renderSystemIdentification(container, rooms, state, connection, hass, slug) {
  if (slug) {
    return renderIdentificationDetail(container, slug, rooms, state, connection, hass);
  }
  return renderIdentificationIndex(container, rooms, state);
}
