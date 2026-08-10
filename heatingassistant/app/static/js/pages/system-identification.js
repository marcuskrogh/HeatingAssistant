import { renderIdentificationIndex } from '../identification/sysid-index.js?v=119';
import { renderIdentificationDetail } from '../identification/sysid-detail.js?v=119';

export function renderSystemIdentification(container, rooms, state, connection, hass, slug) {
  if (slug) {
    return renderIdentificationDetail(container, slug, rooms, state, connection, hass);
  }
  return renderIdentificationIndex(container, rooms, state);
}
