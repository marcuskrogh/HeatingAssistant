// System Status page — MQTT / API / module health / MPC operational detail.
import { systemEntity, entityAttr, entityValue, formatNumber } from '../utils.js?v=123';
import { mpcLoadPercent } from '../kpi-engine.js?v=123';

const QUALITY_LABEL = {
  healthy: 'HEALTHY',
  warning: 'WARNING',
  error: 'ERROR',
};

function qualityFromState(state) {
  const q = entityAttr(state, systemEntity('system_summary'), 'system_quality');
  if (q === 'healthy' || q === 'warning' || q === 'error') return q;
  const mqtt = entityAttr(state, systemEntity('system_summary'), 'mqtt_connected');
  if (mqtt === false) return 'error';
  return 'healthy';
}

function formatUptime(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return '—';
  const s = Math.max(0, Math.floor(Number(seconds)));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

function formatDuration(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return '—';
  const s = Math.max(0, Number(seconds));
  if (s < 60) return `${formatNumber(s, 0)} s`;
  if (s < 3600) return `${formatNumber(s / 60, 1)} min`;
  return `${formatNumber(s / 3600, 1)} h`;
}

function row(label, value, quality) {
  const qClass = quality ? ` system-status__value--${quality}` : '';
  return `
    <div class="system-status__row">
      <span class="system-status__key">${label}</span>
      <span class="system-status__value${qClass}">${value}</span>
    </div>`;
}

export function renderSystemStatus(container, rooms, state, connection, hass) {
  container.innerHTML = '';

  const root = document.createElement('div');
  root.className = 'system-status';

  function paint(s) {
    const quality = qualityFromState(s);
    const issue = entityAttr(s, systemEntity('system_summary'), 'issue_summary');
    const mqtt = entityAttr(s, systemEntity('system_summary'), 'mqtt_connected');
    const uptime = entityAttr(s, systemEntity('system_summary'), 'uptime_s');
    const entityCount = entityAttr(s, systemEntity('system_summary'), 'entity_catalog_count');
    const bindings = entityAttr(s, systemEntity('system_summary'), 'bindings_count');
    const controlMode = entityAttr(s, systemEntity('system_summary'), 'control_mode');
    const fallback = entityAttr(s, systemEntity('system_summary'), 'fallback_reason');
    const idHistory = entityAttr(s, systemEntity('system_summary'), 'id_history') || {};
    const mpcLoad = mpcLoadPercent(s);
    const lastDuration = entityValue(s, systemEntity('mpc_performance'));
    const lastRun = entityAttr(s, systemEntity('mpc_performance'), 'last_run_ts');
    const dt = entityAttr(s, systemEntity('mpc_performance'), 'dt_s');
    const meanErr = entityAttr(s, systemEntity('mpc_performance'), 'mean_tracking_error');
    const hassCount = Object.keys(hass?.states || s || {}).length;

    const issueBlock = issue
      ? `<div class="system-status__issue system-status__issue--${quality}">${issue}</div>`
      : `<div class="system-status__issue system-status__issue--healthy">No active issues.</div>`;

    root.innerHTML = `
      <div class="section-header">SYSTEM STATUS</div>
      ${issueBlock}
      <div class="system-status__grid">
        <section class="system-status__card">
          <div class="system-status__card-title">OVERALL</div>
          ${row('Quality', QUALITY_LABEL[quality] || quality, quality)}
          ${row('Uptime', formatUptime(uptime))}
          ${row('API', 'connected', 'healthy')}
        </section>
        <section class="system-status__card">
          <div class="system-status__card-title">MQTT</div>
          ${row('Connection', mqtt === false ? 'disconnected' : 'ok', mqtt === false ? 'error' : 'healthy')}
          ${row('Bindings', bindings == null ? '—' : String(bindings))}
        </section>
        <section class="system-status__card">
          <div class="system-status__card-title">ENTITIES</div>
          ${row('HA catalog', entityCount == null ? '—' : String(entityCount))}
          ${row('Panel states', String(hassCount))}
          ${row('Rooms configured', String((rooms || []).length))}
        </section>
        <section class="system-status__card">
          <div class="system-status__card-title">MPC / CONTROL</div>
          ${row('Mode', controlMode || '—')}
          ${row('Fallback', fallback || 'none', fallback ? 'warning' : 'healthy')}
          ${row('MPC load', mpcLoad == null ? '—' : `${formatNumber(mpcLoad, 0)}%`)}
          ${row('Last solve', lastDuration == null ? '—' : `${formatNumber(Number(lastDuration), 2)} s`)}
          ${row('Interval', dt == null ? '—' : `${formatNumber(Number(dt), 0)} s`)}
          ${row('Mean tracking err', meanErr == null ? '—' : formatNumber(Number(meanErr), 2))}
          ${row('Last run ts', lastRun == null ? '—' : String(lastRun))}
        </section>
        <section class="system-status__card">
          <div class="system-status__card-title">ID HISTORY</div>
          ${row(
            'Last sample age',
            formatDuration(idHistory.last_sample_age_s),
            idHistory.last_sample_quality
          )}
          ${row(
            'Last durable append',
            idHistory.append_detail || '—',
            idHistory.append_quality
          )}
          ${row(
            'Buffer–disk lag',
            formatDuration(idHistory.buffer_disk_lag_s),
            idHistory.lag_quality
          )}
        </section>
      </div>
    `;
  }

  paint(state);
  container.appendChild(root);

  return {
    update(newState) {
      paint(newState);
    },
    destroy() {},
  };
}
