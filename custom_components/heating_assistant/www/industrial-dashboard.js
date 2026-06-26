const BASE_PATH = '/ha-industrial-panel';

// Cache-bust token for all dynamically-imported submodules (js/pages/*, css).
//
// Derived from THIS file's own URL query string — the `?v=` that HA's panel
// registration appends via `js_url` in __init__.py.  Reusing the same token
// for every submodule import guarantees the entry point and its submodules
// are always loaded at the same version, so the two can never drift.
//
// Uses document.currentScript.src (available in classic scripts throughout
// synchronous top-level execution).  import.meta.url is NOT used here because
// HA loads panel JS as a classic <script>, and import.meta is module-only
// syntax that causes a parse-time SyntaxError in classic scripts regardless of
// any try/catch.
//
// Single source of truth: the `?v=` on `js_url` in __init__.py.  Bump that
// one number on every frontend change — no need to touch this file.
const PANEL_VERSION = (() => {
  try {
    const src = document.currentScript?.src ?? '';
    const v = new URLSearchParams(src.split('?')[1] ?? '').get('v');
    if (v) return v;
  } catch (e) {
    /* unexpected — fall through to hardcoded fallback */
  }
  return '78';
})();

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
    // The backend starts STOPPED after every (re)start; the user must press
    // START to engage the controller.  Reflect that default until the real
    // system_enabled attribute syncs in from the coordinator.
    this._systemRunning = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (this._connection) {
      this._connection.updateHass(hass);
    }
    if (this._menuButton) {
      this._menuButton.hass = hass;
    }
    if (!this._initialized && hass) {
      this._initialized = true;
      this._boot();
    } else if (this._initialized && this._router) {
      let changed = false;
      for (const [id, state] of Object.entries(hass.states)) {
        if (id.startsWith('sensor.heating_assistant_') && this._state[id] !== state) {
          this._state[id] = state;
          changed = true;
        }
      }
      if (changed) {
        this._router.update(this._state);
        this._syncSystemRunning();
      }
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

    try {
      const [
        { HaConnection },
        { Router },
        { discoverRooms },
        { renderOverview },
        { renderRoomDetail },
        { renderSystemIdentification },
        { renderControllerTuning },
        { renderSchedules },
        { renderConfiguration },
      ] = await Promise.all([
        import(`${BASE_PATH}/js/ha-connection.js?v=${PANEL_VERSION}`),
        import(`${BASE_PATH}/js/router.js?v=${PANEL_VERSION}`),
        import(`${BASE_PATH}/js/discovery.js?v=${PANEL_VERSION}`),
        import(`${BASE_PATH}/js/pages/overview.js?v=${PANEL_VERSION}`),
        import(`${BASE_PATH}/js/pages/room-detail.js?v=${PANEL_VERSION}`),
        import(`${BASE_PATH}/js/pages/system-identification.js?v=${PANEL_VERSION}`),
        import(`${BASE_PATH}/js/pages/tuning-controller.js?v=${PANEL_VERSION}`),
        import(`${BASE_PATH}/js/pages/schedules.js?v=${PANEL_VERSION}`),
        import(`${BASE_PATH}/js/pages/configuration.js?v=${PANEL_VERSION}`),
      ]);

      this._connection = new HaConnection(this._hass);

      // Read state from the LATEST hass (may have been updated via set hass()
      // while modules were loading) rather than the stored connection snapshot.
      const latestStates = this._hass.states;
      this._state = { ...latestStates };
      this._rooms = discoverRooms(this._state);

      const contentEl = this.shadowRoot.getElementById('content');
      this._router = new Router(contentEl, {
        overview: () => renderOverview(contentEl, this._rooms, this._state, this._connection, this._hass),
        room: (slug) => renderRoomDetail(contentEl, slug, this._rooms, this._state, this._connection, this._hass),
        identification: (slug) => renderSystemIdentification(contentEl, this._rooms, this._state, this._connection, this._hass, slug),
        tuning: (slug) => renderControllerTuning(contentEl, this._rooms, this._state, this._connection, this._hass, slug),
        schedules: (slug) => renderSchedules(contentEl, this._rooms, this._state, this._connection, this._hass, slug),
        config: (slug) => renderConfiguration(contentEl, this._rooms, this._state, this._connection, this._hass, slug),
      });

      this._router.start();
      this._updateActiveNav();
      this._syncSystemRunning();

      // After the router renders the initial page, sync with the very latest
      // hass.states to pick up any changes that arrived during boot.  This
      // closes the window between the state snapshot above and subscription
      // activation where state_changed events could be missed.
      this._syncLatestState();
    } catch (err) {
      // Surface the error in the panel instead of leaving the user on the
      // frozen "INITIALIZING..." placeholder forever.
      const contentEl = this.shadowRoot.getElementById('content');
      if (contentEl) {
        contentEl.innerHTML = `
          <div class="loading" style="color:#e57373;">
            LOAD ERROR — ${err?.message || err}<br>
            <small style="opacity:0.6;">Check the browser console and HA logs for details.</small>
          </div>`;
      }
      console.error('[heating-assistant] Boot failed:', err);
    }
  }

  _syncLatestState() {
    if (!this._hass || !this._router) return;
    let changed = false;
    for (const [id, state] of Object.entries(this._hass.states)) {
      if (id.startsWith('sensor.heating_assistant_') && this._state[id] !== state) {
        this._state[id] = state;
        changed = true;
      }
    }
    if (changed) this._router.update(this._state);
  }

  _syncSystemRunning() {
    const summary = this._state['sensor.heating_assistant_system_summary'];
    if (!summary) return;
    const enabled = summary.attributes?.system_enabled;
    if (typeof enabled === 'boolean') {
      this._systemRunning = enabled;
      this._updateRunButton();
    }
  }

  _renderShell() {
    this.shadowRoot.innerHTML = `
      <link rel="stylesheet" href="${BASE_PATH}/css/industrial.css?v=${PANEL_VERSION}">
      <div class="shell">
        <div id="top-bar"></div>
        <nav class="panel-nav" id="panel-nav">
          <div class="panel-nav__links" id="nav-links">
            <a class="panel-nav__link" href="#overview">OVERVIEW</a>
            <a class="panel-nav__link" href="#schedules">SCHEDULES</a>
            <a class="panel-nav__link" href="#tuning">TUNING</a>
            <a class="panel-nav__link" href="#identification">SYSTEM IDENTIFICATION</a>
            <a class="panel-nav__link" href="#config">CONFIGURATION</a>
          </div>
          <div class="panel-nav__brand">
            <svg class="panel-nav__logo" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
              <line x1="31" y1="52" x2="71" y2="52" stroke="#00d4aa" stroke-width="2.5" stroke-linecap="round" opacity="0.32"/>
              <path d="M 31 72 C 42 72, 45 53.5, 57 52.5 C 63 52, 67 52, 71 52" fill="none" stroke="#00d4aa" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
              <circle cx="71" cy="52" r="3.6" fill="#00d4aa"/>
              <path d="M 20 84 L 20 47 L 50 19 L 80 47 L 80 84 Z" fill="none" stroke="#00d4aa" stroke-width="5.5" stroke-linejoin="round" stroke-linecap="round"/>
            </svg>
            <span class="panel-nav__name">HEATING ASSISTANT</span>
          </div>
          <span class="panel-nav__fill"></span>
          <div class="panel-nav__controls">
            <div class="panel-nav__live-indicator" id="live-indicator">
              <span class="live-dot" id="live-dot"></span>
              <span class="live-label" id="live-label">STOPPED</span>
            </div>
            <button class="panel-nav__run-btn" id="run-btn" aria-label="Start or stop the heating assistant">
              ⏻
            </button>
          </div>
          <button class="panel-nav__toggle" id="nav-toggle" aria-label="Toggle navigation" aria-expanded="false">
            <span class="panel-nav__toggle-icon">&#9776;</span>
            <span class="panel-nav__toggle-label">PAGES</span>
          </button>
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

    const runBtn = this.shadowRoot.getElementById('run-btn');
    if (runBtn) {
      runBtn.addEventListener('click', () => this._toggleSystem());
    }

    this._updateRunButton();

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

  _updateRunButton() {
    const nav = this.shadowRoot.getElementById('panel-nav');
    const btn = this.shadowRoot.getElementById('run-btn');
    if (!btn) return;

    // The whole second bar signals system state: --live tints and animates the
    // bar when the controller is running, --stopped marks it dormant.
    if (nav) {
      nav.classList.toggle('panel-nav--live', this._systemRunning);
      nav.classList.toggle('panel-nav--stopped', !this._systemRunning);
    }

    btn.classList.toggle('panel-nav__run-btn--running', this._systemRunning);

    const dot = this.shadowRoot.getElementById('live-dot');
    const label = this.shadowRoot.getElementById('live-label');
    if (dot) {
      dot.classList.toggle('live-dot--live', this._systemRunning);
      dot.classList.toggle('live-dot--stopped', !this._systemRunning);
    }
    if (label) {
      label.textContent = this._systemRunning ? 'LIVE' : 'STOPPED';
      label.classList.toggle('live-label--live', this._systemRunning);
      label.classList.toggle('live-label--stopped', !this._systemRunning);
    }
  }

  async _toggleSystem() {
    if (!this._hass) return;
    const newEnabled = !this._systemRunning;
    this._systemRunning = newEnabled;
    this._updateRunButton();
    try {
      await this._hass.callService('heating_assistant', 'set_system_enabled', { enabled: newEnabled });
    } catch (e) {
      // Revert on failure
      this._systemRunning = !newEnabled;
      this._updateRunButton();
    }
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
    if (this._router) {
      this._router.destroy();
    }
  }
}

customElements.define('ha-industrial-panel', HaIndustrialPanel);
