import { createGauge, setGaugeComputing } from '/ha-industrial-panel/js/components/gauge.js';
import { createCountdown, COUNTDOWN_NMPC } from '/ha-industrial-panel/js/components/countdown.js';
import { entityAttr, isComputeInProgress, systemEntity } from '/ha-industrial-panel/js/utils.js';

const ELAPSED_S = 450;
const NOW_S = Date.now() / 1000;

function mpcState({ nmpc = false, control = false } = {}) {
  return {
    'sensor.heating_assistant_system_summary': {
      attributes: { system_enabled: true },
    },
    'sensor.heating_assistant_mpc_performance': {
      attributes: {
        dt_s: 900,
        nmpc_period_s: 7200,
        last_nmpc_ts: NOW_S - ELAPSED_S,
        nmpc_computing: nmpc,
        control_computing: control,
      },
    },
  };
}

const STATES = {
  idle: mpcState(),
  nmpc: mpcState({ nmpc: true }),
  control: mpcState({ control: true }),
  computing: mpcState({ nmpc: true }),
};

const LIVE_KPIS = [
  { label: 'HEATING POWER', value: 1.2, min: 0, max: 4, format: (v) => `${v.toFixed(1)} kW` },
  { label: 'ENERGY PRICE', value: 1.84, min: 0, max: 4, format: (v) => v.toFixed(2) },
];

function setCountdownComputing(container, computing) {
  if (!container) return;
  container.classList.toggle('countdown--computing', !!computing);
}

function flag(state, name) {
  return Boolean(entityAttr(state, systemEntity('mpc_performance'), name));
}

class HaKpiHost extends HTMLElement {
  connectedCallback() {
    const root = this.attachShadow({ mode: 'open' });
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = '/ha-industrial-panel/css/industrial.css';
    root.appendChild(css);
    const extraCapture = document.createElement('link');
    extraCapture.rel = 'stylesheet';
    extraCapture.href = './capture.css';
    root.appendChild(extraCapture);
    const candidate = this.dataset.candidate === '1';
    if (candidate) {
      const extra = document.createElement('link');
      extra.rel = 'stylesheet';
      extra.href = './overlay.css';
      root.appendChild(extra);
    }

    const wrap = document.createElement('div');
    wrap.style.background = 'var(--bg-primary)';
    wrap.style.padding = '16px';
    wrap.style.borderRadius = '8px';
    const header = document.createElement('div');
    header.className = 'section-header';
    header.textContent = 'CONTROLLER KPIS';
    wrap.appendChild(header);
    const grid = document.createElement('div');
    grid.className = 'grid-kpi';
    wrap.appendChild(grid);
    root.appendChild(wrap);

    const gauges = LIVE_KPIS.map((spec) => {
      const el = createGauge(spec);
      grid.appendChild(el);
      return el;
    });

    const control = createCountdown(STATES.idle, false);
    grid.appendChild(control.element);
    const nmpc = createCountdown(STATES.idle, { ...COUNTDOWN_NMPC, small: false });
    grid.appendChild(nmpc.element);

    const apply = (state) => {
      control.tick(state);
      nmpc.tick(state);
      if (candidate) {
        gauges.forEach((el) => setGaugeComputing(el, false));
        setCountdownComputing(control.element, flag(state, 'control_computing'));
        setCountdownComputing(nmpc.element, flag(state, 'nmpc_computing'));
      } else {
        const computing = isComputeInProgress(state);
        gauges.forEach((el) => setGaugeComputing(el, computing));
        setCountdownComputing(control.element, false);
        setCountdownComputing(nmpc.element, false);
      }
      if (this.hasAttribute('data-capture-mid')) {
        [control.element, nmpc.element, ...gauges].forEach((el) => {
          el.classList.add('capture-mid');
        });
      }
    };

    const params = new URLSearchParams(location.search);
    if (params.get('capture') === '1') {
      this.setAttribute('data-capture-mid', '');
    }
    const mode = params.get('mode') || 'nmpc';
    const state = STATES[mode] || STATES.nmpc;
    apply(state);

    const lead = document.getElementById('lead');
    if (lead && !lead.dataset.filled) {
      lead.dataset.filled = '1';
      const nmpcOn = flag(state, 'nmpc_computing');
      const controlOn = flag(state, 'control_computing');
      lead.append(
        ` This capture is ${
          nmpcOn ? 'nmpc_computing: true' : controlOn ? 'control_computing: true' : 'idle'
        }.`,
      );
    }

    Promise.all(
      [...root.querySelectorAll('link')].map(
        (link) =>
          new Promise((resolve) => {
            link.addEventListener('load', resolve, { once: true });
            link.addEventListener('error', resolve, { once: true });
          }),
      ),
    ).then(() => {
      document.documentElement.dataset.ready = '1';
    });
  }
}

customElements.define('ha-kpi-host', HaKpiHost);
