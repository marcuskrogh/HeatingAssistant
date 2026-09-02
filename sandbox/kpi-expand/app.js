import { createGauge } from '/ha-industrial-panel/js/components/gauge.js';
import { createCountdown, COUNTDOWN_NMPC, setCountdownComputing } from '/ha-industrial-panel/js/components/countdown.js';
import { bindKpiExpandSection } from '/ha-industrial-panel/js/components/kpi-expand.js';
import { mpcLoadDetail, nextControlDetail, nextNmpcDetail, overallHealthDetail } from '/ha-industrial-panel/js/kpi-detail-catalog.js';

const NOW_S = Date.now() / 1000;

function fixtureState({ nmpc = false } = {}) {
  return {
    'sensor.heating_assistant_system_summary': {
      state: '4200',
      attributes: {
        system_enabled: true,
        system_quality: 'healthy',
        mqtt_connected: true,
        issue_summary: '',
        has_heat_pump: true,
        effective_system_cop: 3.1,
      },
    },
    'sensor.heating_assistant_mpc_performance': {
      state: '0.18',
      attributes: {
        dt_s: 900,
        nmpc_period_s: 7200,
        last_nmpc_ts: NOW_S - 450,
        nmpc_computing: nmpc,
        control_computing: false,
        last_nmpc_duration_s: 24.7,
        nmpc_result_ts: NOW_S - 400,
        last_control_ran_ts: NOW_S - 80,
        mean_tracking_error: 0.12,
      },
    },
  };
}

class HaKpiHost extends HTMLElement {
  connectedCallback() {
    const root = this.attachShadow({ mode: 'open' });
    const css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = '/ha-industrial-panel/css/industrial.css';
    root.appendChild(css);

    const wrap = document.createElement('div');
    wrap.style.background = 'var(--bg-primary)';
    wrap.style.padding = '16px';
    wrap.style.borderRadius = '8px';

    const systemHeader = document.createElement('div');
    systemHeader.className = 'section-header';
    systemHeader.textContent = 'SYSTEM STATUS';
    wrap.appendChild(systemHeader);
    const systemGrid = document.createElement('div');
    systemGrid.className = 'grid-kpi';
    wrap.appendChild(systemGrid);

    const controllerHeader = document.createElement('div');
    controllerHeader.className = 'section-header';
    controllerHeader.textContent = 'CONTROLLER KPIS';
    wrap.appendChild(controllerHeader);
    const controllerGrid = document.createElement('div');
    controllerGrid.className = 'grid-kpi';
    wrap.appendChild(controllerGrid);
    root.appendChild(wrap);

    const systemExpand = bindKpiExpandSection(systemGrid);
    const health = createGauge({
      value: 100, min: 0, max: 100, label: 'OVERALL HEALTH', format: () => 'HEALTHY',
    });
    systemExpand.register(health, { key: 'overall-health', detail: overallHealthDetail });
    const mpc = createGauge({
      value: 9, min: 0, max: 100, label: 'MPC LOAD', format: (v) => `${v.toFixed(0)}%`,
    });
    systemExpand.register(mpc, { key: 'mpc-load', detail: mpcLoadDetail });

    const controllerExpand = bindKpiExpandSection(controllerGrid);
    const power = createGauge({
      value: 1.2, min: 0, max: 4, label: 'HEATING POWER', format: (v) => `${v.toFixed(1)} kW`,
    });
    controllerExpand.register(power, {
      key: 'heating-power',
      detail: () => ({
        description: 'Sum of measured heater power across the house.',
        rows: [
          { label: 'Live', value: '4.2 kW' },
          { label: 'Fill', value: '42%' },
        ],
      }),
    });
    const control = createCountdown(fixtureState(), false);
    controllerExpand.register(control.element, { key: 'next-control', detail: nextControlDetail });
    const nmpc = createCountdown(fixtureState(), { ...COUNTDOWN_NMPC, small: false });
    controllerExpand.register(nmpc.element, { key: 'next-nmpc', detail: nextNmpcDetail });

    const params = new URLSearchParams(location.search);
    const mode = params.get('mode') || 'collapsed';
    const state = fixtureState({ nmpc: mode === 'nmpc' });
    systemExpand.paint(state);
    controllerExpand.paint(state);
    setCountdownComputing(nmpc.element, Boolean(state['sensor.heating_assistant_mpc_performance'].attributes.nmpc_computing));
    if (mode === 'mpc') systemExpand.open('mpc-load');
    if (mode === 'nmpc') controllerExpand.open('next-nmpc');

    const lead = document.getElementById('lead');
    if (lead && !lead.dataset.filled) {
      lead.dataset.filled = '1';
      lead.append(` Capture mode: ${mode}.`);
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
