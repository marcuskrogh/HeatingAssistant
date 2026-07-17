import { setPanelHash } from '../panel-hash.js?v=107';
import { ICONS } from './config-icons.js?v=107';
import { el } from './config-ui.js?v=107';

// Landing page — cards linking to each configuration area
// ---------------------------------------------------------------------------

const LANDING_CARDS = [
  {
    hash: '#config/display',
    icon: ICONS.display,
    title: 'Display & Plots',
    desc: 'How much history and forecast the room charts show. Decoupled from the controller horizon.',
  },
  {
    hash: '#config/rooms',
    icon: ICONS.rooms,
    title: 'Rooms',
    desc: 'Thermal model, comfort setpoints, sensors, windows and inter-room connections for each room.',
  },
  {
    hash: '#config/sources',
    icon: ICONS.sources,
    title: 'Heat Sources',
    desc: 'Electric heaters and heat pumps: capacity, efficiency, COP and the entity each one drives.',
  },
  {
    hash: '#config/system',
    icon: ICONS.system,
    title: 'Environment',
    desc: 'Outdoor temperature, weather, solar irradiance and electricity-price sensors.',
  },
  {
    hash: '#config/params',
    icon: ICONS.params,
    title: 'System Parameters',
    desc: 'Data retention, history depth and other system-level settings that control how the integration stores and manages data.',
  },
];

function renderLanding(container) {
  container.innerHTML = '';
  container.appendChild(el('div', 'section-header', 'CONFIGURATION'));
  container.appendChild(el(
    'p', 'config-section__desc',
    'Configure every part of the Heating Assistant here. Changes to rooms or heat sources '
    + 'restart the model so new parameters take effect; display and environment changes apply live.',
  ));

  const grid = el('div', 'config-landing-grid');
  for (const c of LANDING_CARDS) {
    const card = el('div', 'card card--clickable config-landing-card');
    card.innerHTML = `
      <div class="config-landing-card__icon">${c.icon}</div>
      <div class="config-landing-card__body">
        <div class="config-landing-card__title">${c.title}</div>
        <div class="config-landing-card__desc">${c.desc}</div>
      </div>
      <div class="config-landing-card__chevron">›</div>
    `;
    card.addEventListener('click', () => { setPanelHash(c.hash); });
    grid.appendChild(card);
  }
  container.appendChild(grid);
  return { update() {}, destroy() {} };
}

// ---------------------------------------------------------------------------

export { LANDING_CARDS, renderLanding };
