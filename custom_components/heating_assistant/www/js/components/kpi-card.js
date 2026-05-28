export function createKpiCard({ value, label, unit, html }) {
  const container = document.createElement('div');
  container.className = 'card kpi';
  renderKpiContent(container, value, label, unit, html);
  return container;
}

export function updateKpiCard(container, { value, unit, html }) {
  const valueEl = container.querySelector('.kpi__value');
  if (!valueEl) return;
  if (html) {
    valueEl.innerHTML = value;
  } else {
    const displayValue = value ?? '—';
    valueEl.innerHTML = `${displayValue}${unit ? `<span class="kpi__unit">${unit}</span>` : ''}`;
  }
}

function renderKpiContent(container, value, label, unit, html) {
  const displayValue = value ?? '—';
  if (html) {
    container.innerHTML = `
      <span class="kpi__value">${displayValue}</span>
      <span class="kpi__label">${label}</span>
    `;
  } else {
    container.innerHTML = `
      <span class="kpi__value">${displayValue}${unit ? `<span class="kpi__unit">${unit}</span>` : ''}</span>
      <span class="kpi__label">${label}</span>
    `;
  }
}
