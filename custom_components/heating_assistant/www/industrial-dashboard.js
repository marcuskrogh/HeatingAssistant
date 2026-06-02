const BASE_PATH = '/ha-industrial-panel';
const PANEL_VERSION = '14';

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
      { renderSystemIdentification },
      { renderControllerTuning },
      { renderSchedules },
    ] = await Promise.all([
      import(`${BASE_PATH}/js/ha-connection.js?v=${PANEL_VERSION}`),
      import(`${BASE_PATH}/js/router.js?v=${PANEL_VERSION}`),
      import(`${BASE_PATH}/js/discovery.js?v=${PANEL_VERSION}`),
      import(`${BASE_PATH}/js/pages/overview.js?v=${PANEL_VERSION}`),
      import(`${BASE_PATH}/js/pages/room-detail.js?v=${PANEL_VERSION}`),
      import(`${BASE_PATH}/js/pages/system-identification.js?v=${PANEL_VERSION}`),
      import(`${BASE_PATH}/js/pages/tuning-controller.js?v=${PANEL_VERSION}`),
      import(`${BASE_PATH}/js/pages/schedules.js?v=${PANEL_VERSION}`),
    ]);

    this._connection = new HaConnection(this._hass);
    const states = await this._connection.getStates();
    this._state = states;
    this._rooms = discoverRooms(states);

    const contentEl = this.shadowRoot.getElementById('content');
    this._router = new Router(contentEl, {
      overview: () => renderOverview(contentEl, this._rooms, this._state, this._connection, this._hass),
      room: (slug) => renderRoomDetail(contentEl, slug, this._rooms, this._state, this._connection, this._hass),
      identification: (slug) => renderSystemIdentification(contentEl, this._rooms, this._state, this._connection, this._hass, slug),
      tuning: (slug) => renderControllerTuning(contentEl, this._rooms, this._state, this._connection, this._hass, slug),
      schedules: (slug) => renderSchedules(contentEl, this._rooms, this._state, this._connection, this._hass, slug),
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
          <div class="panel-nav__brand">
            <svg class="panel-nav__logo" viewBox="0 0 28 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
              <path d="M7 1.5C7 1.5 5.5 3.5 7 5C8.5 6.5 7 8.5 7 8.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <path d="M14 1C14 1 12.5 3 14 4.5C15.5 6 14 8 14 8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <path d="M21 1.5C21 1.5 19.5 3.5 21 5C22.5 6.5 21 8.5 21 8.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
              <rect x="2" y="10" width="24" height="12" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/>
              <line x1="9" y1="10" x2="9" y2="22" stroke="currentColor" stroke-width="1.5"/>
              <line x1="16" y1="10" x2="16" y2="22" stroke="currentColor" stroke-width="1.5"/>
            </svg>
            <span class="panel-nav__name">HEATING ASSISTANT</span>
          </div>
          <button class="panel-nav__toggle" id="nav-toggle" aria-label="Toggle navigation" aria-expanded="false">
            <span class="panel-nav__toggle-icon">&#9776;</span>
            <span class="panel-nav__toggle-label">PAGES</span>
          </button>
          <div class="panel-nav__links" id="nav-links">
            <a class="panel-nav__link" href="#overview">OVERVIEW</a>
            <a class="panel-nav__link" href="#identification">IDENTIFICATION</a>
            <a class="panel-nav__link" href="#schedules">SCHEDULES</a>
            <a class="panel-nav__link" href="#tuning">TUNING</a>
          </div>
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

    const navToggle = this.shadowRoot.getElementById('nav-toggle');
    const navLinks = this.shadowRoot.getElementById('nav-links');

    if (navToggle && navLinks) {
      navToggle.addEventListener('click', () => {
        const isOpen = navLinks.classList.toggle('panel-nav__links--open');
        navToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
      });
    }

    this.shadowRoot.querySelectorAll('.panel-nav__link').forEach((link) => {
      link.addEventListener('click', (e) => {
        e.preventDefault();
        const target = link.getAttribute('href');
        window.location.hash = target;
        // Close mobile dropdown after navigation
        if (navLinks) {
          navLinks.classList.remove('panel-nav__links--open');
          if (navToggle) navToggle.setAttribute('aria-expanded', 'false');
        }
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

    toolbar.appendChild(menuBtn);
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
