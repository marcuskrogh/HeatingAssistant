const BASE_PATH = '/ha-industrial-panel';
const PANEL_VERSION = '6';

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
    if (this._menuButton) {
      this._menuButton.hass = hass;
    }
    if (!this._initialized && hass) {
      this._initialized = true;
      this._boot();
    }
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menuButton) {
      this._menuButton.narrow = narrow;
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
      room: (slug) => renderRoomDetail(contentEl, slug, this._rooms, this._state, this._connection, this._hass),
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
        <div id="top-bar"></div>
        <nav class="panel-nav" id="panel-nav">
          <a class="panel-nav__link" href="#overview">OVERVIEW</a>
          <a class="panel-nav__link" href="#tuning">TUNING</a>
          <span class="panel-nav__fill"></span>
          <span class="panel-nav__status">
            <span class="status-dot status-dot--live"></span>
            <span class="status-label">LIVE</span>
          </span>
        </nav>
        <main id="content" class="content">
          <div class="loading">INITIALIZING...</div>
        </main>
      </div>
    `;

    this._buildTopBar();

    this.shadowRoot.querySelectorAll('.panel-nav__link').forEach((link) => {
      link.addEventListener('click', (e) => {
        e.preventDefault();
        const target = link.getAttribute('href');
        window.location.hash = target;
      });
    });

    window.addEventListener('hashchange', () => this._updateActiveNav());
  }

  _buildTopBar() {
    const container = this.shadowRoot.getElementById('top-bar');

    const toolbar = document.createElement('app-toolbar');

    const menuBtn = document.createElement('ha-menu-button');
    menuBtn.hass = this._hass;
    menuBtn.narrow = this._narrow ?? window.innerWidth < 870;
    this._menuButton = menuBtn;

    const title = document.createElement('div');
    title.setAttribute('main-title', '');
    title.textContent = 'Heating Assistant';

    toolbar.appendChild(menuBtn);
    toolbar.appendChild(title);
    container.appendChild(toolbar);
  }

  _updateActiveNav() {
    const hash = window.location.hash.slice(1).split('/')[0] || 'overview';
    const links = this.shadowRoot.querySelectorAll('.panel-nav__link');
    links.forEach((link) => {
      const linkRoute = link.getAttribute('href').slice(1);
      link.classList.toggle('panel-nav__link--active', linkRoute === hash);
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
