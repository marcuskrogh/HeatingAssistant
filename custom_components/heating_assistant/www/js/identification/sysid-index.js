import { setPanelHash } from '../panel-hash.js?v=100';
import { loadDismissedWarnings, saveDismissedWarning } from './sysid-shared.js?v=100';

export function renderIdentificationIndex(container, rooms, state) {
  container.innerHTML = '';

  const header = document.createElement('div');
  header.className = 'section-header';
  header.textContent = 'SYSTEM IDENTIFICATION';
  container.appendChild(header);

  const desc = document.createElement('p');
  desc.className = 'tuning-section__desc';
  desc.textContent = 'Select a room to view and configure its system identification parameters, run parameter estimation, and validate model fit.';
  container.appendChild(desc);

  const grid = document.createElement('div');
  grid.className = 'grid-rooms';
  container.appendChild(grid);

  let latestState = state;

  function buildTiles(st) {
    latestState = st;
    grid.innerHTML = '';
    for (const room of rooms) {
      const card = document.createElement('div');
      card.className = 'card card--clickable sysid-index-card';
      card.dataset.room = room.slug;
      card.innerHTML = buildIdentificationCardHtml(room, st);
      card.addEventListener('click', (event) => {
        if (event.target.closest('[data-dismiss-warning]')) return;
        setPanelHash(`#identification/${room.slug}`);
      });
      grid.appendChild(card);
    }
  }

  function handleDismissWarning(event) {
    const dismissBtn = event.target.closest('[data-dismiss-warning]');
    if (!dismissBtn) return;
    event.preventDefault();
    event.stopPropagation();
    const card = dismissBtn.closest('[data-room]');
    const slug = card?.dataset.room;
    const code = dismissBtn.dataset.dismissWarning;
    if (!slug || !code) return;
    saveDismissedWarning(slug, code);
    const room = rooms.find((r) => r.slug === slug);
    if (room && card) {
      card.innerHTML = buildIdentificationCardHtml(room, latestState);
    }
  }

  grid.addEventListener('click', handleDismissWarning);
  grid.addEventListener('pointerdown', handleDismissWarning);

  buildTiles(state);

  return {
    update(newState) {
      for (const room of rooms) {
        const card = grid.querySelector(`[data-room="${room.slug}"]`);
        if (card) card.innerHTML = buildIdentificationCardHtml(room, newState);
      }
      latestState = newState;
    },
    destroy() {},
  };
}

function identificationEntityIds(slug) {
  return {
    fit: `sensor.heating_assistant_${slug}_model_fit_quality`,
    confidence: `sensor.heating_assistant_${slug}_parameter_confidence`,
    openLoop: `sensor.heating_assistant_${slug}_open_loop_rmse`,
  };
}

function fitBadgeClass(fitInfo) {
  if (fitInfo.class === 'fit--good') return 'sysid-index-card__badge--good';
  if (fitInfo.class === 'fit--acceptable') return 'sysid-index-card__badge--acceptable';
  if (fitInfo.class === 'fit--poor') return 'sysid-index-card__badge--poor';
  return 'sysid-index-card__badge--unknown';
}

function identificationStat(label, value) {
  return `<span class="store-stat"><span class="store-stat__k">${label}</span><span class="store-stat__v">${value}</span></span>`;
}

function identificationWarningsHtml(slug, confAttrs) {
  const warnings = Array.isArray(confAttrs.card_warnings) ? confAttrs.card_warnings : [];
  const dismissed = loadDismissedWarnings(slug);
  const visible = warnings.filter((w) => w?.code && !dismissed.has(w.code));
  if (visible.length === 0) return '';

  return `<div class="sysid-index-card__warnings">${visible.map((w) => {
    const severity = w.severity || 'warn';
    return `
      <div class="sysid-index-card__warning sysid-index-card__warning--${severity}">
        <span class="sysid-index-card__warning-text">${w.message}</span>
        <button type="button" class="sysid-index-card__warning-dismiss" data-dismiss-warning="${w.code}" aria-label="Dismiss warning">×</button>
      </div>`;
  }).join('')}</div>`;
}

function buildIdentificationCardHtml(room, st) {
  const ids = identificationEntityIds(room.slug);
  const fitEntity = st[ids.fit];
  const confEntity = st[ids.confidence];

  const fitVal = fitEntity ? parseFloat(fitEntity.state) : null;
  const fitInfo = modelFitLabel(fitVal);
  const fitAttrs = fitEntity?.attributes || {};
  const confAttrs = confEntity?.attributes || {};

  const r2 = fitVal != null && !isNaN(fitVal) ? formatNumber(fitVal, 2) : '—';
  const rmse = fitAttrs.rmse != null ? `${formatNumber(fitAttrs.rmse, 3)} °C` : '—';
  const estimated = confAttrs.is_estimated === true
    ? 'Yes'
    : (confAttrs.is_estimated === false ? 'No' : '—');

  return `
    <div class="sysid-index-card__header">
      <span class="sysid-index-card__name">${room.name}</span>
      <span class="sysid-index-card__badge ${fitBadgeClass(fitInfo)}">${fitInfo.label === '—' ? 'NO DATA' : fitInfo.label}</span>
    </div>
    <div class="sysid-index-card__meta">
      ${identificationStat('R²', r2)}
      ${identificationStat('RMSE', rmse)}
      ${identificationStat('Estimated', estimated)}
    </div>
    ${identificationWarningsHtml(room.slug, confAttrs)}
  `;
}
