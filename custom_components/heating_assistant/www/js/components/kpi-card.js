export function createKpiCard({ value, label, unit }) {
  const container = document.createElement('div');
  container.className = 'card kpi';
  const displayValue = value ?? '—';

  container.innerHTML = `
    <span class="kpi__value">${displayValue}<span class="kpi__unit">${unit || ''}</span></span>
    <span class="kpi__label">${label}</span>
  `;

  return container;
}

export function updateKpiCard(container, { value, unit }) {
  const valueEl = container.querySelector('.kpi__value');
  if (valueEl) {
    const displayValue = value ?? '—';
    valueEl.innerHTML = `${displayValue}<span class="kpi__unit">${unit || ''}</span>`;
  }
}
