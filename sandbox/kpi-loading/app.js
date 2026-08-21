import { createGauge, setGaugeComputing } from '/ha-industrial-panel/js/components/gauge.js';
import { isComputeInProgress } from '/ha-industrial-panel/js/utils.js';

const COMPUTING_STATE = {
  'sensor.heating_assistant_mpc_performance': {
    attributes: { nmpc_computing: true, control_computing: false },
  },
};

const IDLE_STATE = {
  'sensor.heating_assistant_mpc_performance': {
    attributes: { nmpc_computing: false, control_computing: false },
  },
};

const ROOM_KPIS = [
  { label: 'POWER', value: 1.2, min: 0, max: 4, format: (v) => `${v.toFixed(1)} kW` },
  { label: 'ENERGY PRICE', value: 1.84, min: 0, max: 4, format: (v) => v.toFixed(2) },
  { label: 'SOLAR GAIN', value: 420, min: 0, max: 1200, format: (v) => `${v.toFixed(0)} W` },
  { label: 'HEAT LOSS', value: 310, min: 0, max: 1200, format: (v) => `${v.toFixed(0)} W` },
  { label: 'MODEL FIT', value: 0.86, min: 0, max: 1, format: () => 'GOOD' },
];

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
    if (this.dataset.candidate === '1') {
      const extra = document.createElement('link');
      extra.rel = 'stylesheet';
      extra.href = './overlay.css';
      root.appendChild(extra);
    }
    const wrap = document.createElement('div');
    wrap.style.background = 'var(--bg-primary)';
    wrap.style.padding = '16px';
    wrap.style.borderRadius = '8px';
    const grid = document.createElement('div');
    grid.className = 'grid-kpi';
    wrap.appendChild(grid);
    root.appendChild(wrap);

    const gauges = ROOM_KPIS.map((spec) => {
      const el = createGauge(spec);
      grid.appendChild(el);
      return el;
    });

    const apply = (state) => {
      const computing = isComputeInProgress(state);
      gauges.forEach((el) => {
        setGaugeComputing(el, computing);
        if (computing && this.hasAttribute('data-capture-mid')) {
          el.classList.add('capture-mid');
        }
      });
    };

    this._apply = apply;
    const params = new URLSearchParams(location.search);
    if (params.get('capture') === '1') {
      this.setAttribute('data-capture-mid', '');
    }
    const mode = params.get('mode') || 'computing';
    apply(mode === 'idle' ? IDLE_STATE : COMPUTING_STATE);
    const lead = document.getElementById('lead');
    if (lead && !lead.dataset.filled) {
      lead.dataset.filled = '1';
      lead.append(` This capture is ${mode === 'idle' ? 'idle' : 'nmpc_computing: true'}.`);
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
