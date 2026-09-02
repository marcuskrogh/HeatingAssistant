/**
 * Pure expand-order helper plus a one-open-per-grid host for KPI cards.
 */

export function expandStateAfterClick({ keys, openKey, clickedKey }) {
  const order = keys.slice();
  if (!order.includes(clickedKey)) {
    return { openKey, order };
  }
  if (clickedKey === openKey) {
    return { openKey: null, order };
  }
  return {
    openKey: clickedKey,
    order: [clickedKey, ...order.filter((key) => key !== clickedKey)],
  };
}

export function bindKpiExpandSection(grid) {
  const items = [];
  let openKey = null;
  let latestState = null;

  function originalKeys() {
    return items.map((item) => item.key);
  }

  function applyOrder(order) {
    for (const key of order) {
      const item = items.find((entry) => entry.key === key);
      if (!item) continue;
      grid.appendChild(item.wrap);
      const open = openKey === item.key;
      item.wrap.classList.toggle('kpi-expand--open', open);
      item.wrap.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) paintDetail(item);
    }
  }

  function currentOrder() {
    if (!openKey) return originalKeys();
    return [openKey, ...originalKeys().filter((key) => key !== openKey)];
  }

  function setOpenFromClick(clickedKey) {
    const next = expandStateAfterClick({
      keys: originalKeys(),
      openKey,
      clickedKey,
    });
    openKey = next.openKey;
    applyOrder(next.order);
  }

  function paintDetail(item) {
    const payload = item.detail(latestState) || {};
    const description = payload.description || '';
    const rows = Array.isArray(payload.rows) ? payload.rows : [];
    const lines = rows
      .map((row) => {
        const label = escapeText(row.label || '');
        const value = escapeText(row.value == null || row.value === '' ? '—' : String(row.value));
        return `<div class="kpi-expand__row"><dt>${label}</dt><dd>${value}</dd></div>`;
      })
      .join('');
    item.panel.innerHTML = `
      <p class="kpi-expand__desc">${escapeText(description)}</p>
      <dl class="kpi-expand__rows">${lines}</dl>
    `;
  }

  function syncHidden(item) {
    const hidden = item.element.style.display === 'none';
    item.wrap.style.display = hidden ? 'none' : '';
    if (hidden && openKey === item.key) {
      openKey = null;
      applyOrder(originalKeys());
    }
  }

  function onActivate(event, item) {
    if (item.element.style.display === 'none') return;
    event.preventDefault();
    setOpenFromClick(item.key);
  }

  function register(element, { key, detail }) {
    const wrap = document.createElement('div');
    wrap.className = 'kpi-expand';
    wrap.dataset.expandKey = key;
    wrap.setAttribute('role', 'button');
    wrap.tabIndex = 0;
    wrap.setAttribute('aria-expanded', 'false');
    wrap.appendChild(element);
    const panel = document.createElement('div');
    panel.className = 'kpi-expand__detail';
    wrap.appendChild(panel);
    const item = { key, wrap, element, detail, panel };
    items.push(item);
    wrap.addEventListener('click', (event) => onActivate(event, item));
    wrap.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        onActivate(event, item);
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        openKey = null;
        applyOrder(originalKeys());
      }
    });
    grid.appendChild(wrap);
    return item;
  }

  grid.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (!openKey) return;
    if (!grid.contains(event.target)) return;
    event.preventDefault();
    openKey = null;
    applyOrder(originalKeys());
  });

  return {
    register,
    paint(state) {
      latestState = state;
      items.forEach(syncHidden);
      applyOrder(currentOrder());
    },
    open(key) {
      if (!key) {
        openKey = null;
        applyOrder(originalKeys());
        return;
      }
      const next = expandStateAfterClick({
        keys: originalKeys(),
        openKey: null,
        clickedKey: key,
      });
      openKey = next.openKey;
      applyOrder(next.order);
    },
  };
}

function escapeText(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
