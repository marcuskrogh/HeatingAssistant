const BASE_PATH = '/ha-industrial-panel';
const PANEL_VERSION = '5';

class HaIndustrialPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._hass = null;
    this._connection = null;
    this._router = null;
    this._rooms = [];
    this._state = {};
    this._initialized = false;
    this._unsubscribe = null;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized && hass) {
      this._initialized = true;
      this._boot();
    }
  }

  set panel(panel) {
    this._panel = panel;
  }

  async _boot() {
    this._renderShell();

    const [
      { HaConnection },
      { Router },
      { discoverRooms },
      { renderOverview },
      { renderRoomDetail },
      { renderTuning },
    ] = await Promise.all([
      import(`${BASE_PATH}/js/ha-connection.js?v=${PANEL_VERSION}`),
      import(`${BASE_PATH}/js/router.js?v=${PANEL_VERSION}`),
      import(`${BASE_PATH}/js/discovery.js?v=${PANEL_VERSION}`),
      import(`${BASE_PATH}/js/pages/overview.js?v=${PANEL_VERSION}`),
      import(`${BASE_PATH}/js/pages/room-detail.js?v=${PANEL_VERSION}`),
      import(`${BASE_PATH}/js/pages/tuning.js?v=${PANEL_VERSION}`),
    ]);

    this._connection = new HaConnection(this._hass);
    const states = await this._connection.getStates();
    this._state = states;
    this._rooms = discoverRooms(states);

    const contentEl = this.shadowRoot.getElementById('content');
    this._router = new Router(contentEl, {
      overview: () => renderOverview(contentEl, this._rooms, this._state, this._connection),
      room: (slug) => renderRoomDetail(contentEl, slug, this._rooms, this._state, this._connection),
      tuning: () => renderTuning(contentEl, this._rooms, this._state, this._connection, this._hass),
    });

    this._unsubscribe = await this._connection.subscribe((event) => {
      this._onStateChanged(event);
    });

    this._router.start();
    this._updateActiveNav();
  }

  _onStateChanged(event) {
    const entityId = event.data?.entity_id;
    if (!entityId || !entityId.startsWith('sensor.heating_assistant_')) return;

    const newState = event.data.new_state;
    if (newState) {
      this._state[entityId] = newState;
      if (this._router) this._router.update(this._state);
    }
  }

  _renderShell() {
    this.shadowRoot.innerHTML = `
      <link rel="stylesheet" href="${BASE_PATH}/css/industrial.css?v=${PANEL_VERSION}">
      <div class="shell">
        <header class="header">
          <div class="header__left">
            <button class="menu-button" id="menu-toggle" aria-label="Toggle sidebar">
              <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
                <path fill="currentColor" d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z"/>
              </svg>
            </button>
            <h1 class="header__title">HEATING ASSISTANT</h1>
            <nav class="header__nav">
              <a class="header__nav-link" href="#overview">OVERVIEW</a>
              <a class="header__nav-link" href="#tuning">TUNING</a>
            </nav>
          </div>
          <div class="header__status">
            <span class="status-dot status-dot--live"></span>
            <span class="status-label">LIVE</span>
          </div>
        </header>
        <main id="content" class="content">
          <div class="loading">INITIALIZING...</div>
        </main>
      </div>
    `;

    this.shadowRoot.getElementById('menu-toggle').addEventListener('click', () => {
      if (window.history.length > 1) {
        window.history.back();
      } else {
        window.location.replace('/lovelace');
      }
    });

    this.shadowRoot.querySelectorAll('.header__nav-link').forEach((link) => {
      link.addEventListener('click', (e) => {
        e.preventDefault();
        const target = link.getAttribute('href');
        window.location.hash = target;
      });
    });

    window.addEventListener('hashchange', () => this._updateActiveNav());
  }

  _updateActiveNav() {
    const hash = window.location.hash.slice(1).split('/')[0] || 'overview';
    const links = this.shadowRoot.querySelectorAll('.header__nav-link');
    links.forEach((link) => {
      const linkRoute = link.getAttribute('href').slice(1);
      link.classList.toggle('header__nav-link--active', linkRoute === hash);
    });
  }

  disconnectedCallback() {
    if (this._unsubscribe) {
      this._unsubscribe();
      this._unsubscribe = null;
    }
    if (this._router) {
      this._router.destroy();
    }
  }
}

customElements.define('ha-industrial-panel', HaIndustrialPanel);
