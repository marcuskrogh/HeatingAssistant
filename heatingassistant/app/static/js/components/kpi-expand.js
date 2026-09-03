/**
 * One-open-per-grid KPI host. Click uses FLIP so the same card moves to the
 * top of the section and grows; the viewport follows the open card.
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

const MOTION_MS = 220;

function prefersReducedMotion() {
  return Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches);
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
      item.panel.setAttribute('aria-hidden', open ? 'false' : 'true');
      paintDetail(item);
    }
  }

  function currentOrder() {
    if (!openKey) return originalKeys();
    return [openKey, ...originalKeys().filter((key) => key !== openKey)];
  }

  function captureRects() {
    const map = new Map();
    items.forEach((item) => {
      map.set(item.key, item.wrap.getBoundingClientRect());
    });
    return map;
  }

  function followOpen() {
    const item = items.find((entry) => entry.key === openKey);
    if (!item) return;
    item.wrap.scrollIntoView({ behavior: 'auto', block: 'start', inline: 'nearest' });
  }

  function playFlip(first, last) {
    items.forEach((item) => {
      const from = first.get(item.key);
      const to = last.get(item.key);
      if (!from || !to) return;
      const dx = from.left - to.left;
      const dy = from.top - to.top;
      const dw = Math.abs(from.width - to.width) > 0.5;
      const dh = Math.abs(from.height - to.height) > 0.5;
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5 && !dw && !dh) return;
      item.wrap.style.zIndex = item.key === openKey ? '4' : '1';
      item.wrap.style.transition = 'none';
      item.wrap.style.transform = `translate(${dx}px, ${dy}px)`;
      if (dw || dh) {
        item.wrap.style.boxSizing = 'border-box';
        item.wrap.style.width = `${from.width}px`;
        item.wrap.style.height = `${from.height}px`;
      }
      requestAnimationFrame(() => {
        item.wrap.style.transition = [
          `transform ${MOTION_MS}ms ease`,
          `width ${MOTION_MS}ms ease`,
          `height ${MOTION_MS}ms ease`,
        ].join(', ');
        item.wrap.style.transform = 'translate(0, 0)';
        if (dw || dh) {
          item.wrap.style.width = `${to.width}px`;
          item.wrap.style.height = `${to.height}px`;
        }
      });
      const clear = () => {
        item.wrap.style.transition = '';
        item.wrap.style.transform = '';
        item.wrap.style.width = '';
        item.wrap.style.height = '';
        item.wrap.style.boxSizing = '';
        item.wrap.style.zIndex = '';
      };
      item.wrap.addEventListener('transitionend', clear, { once: true });
      window.setTimeout(clear, MOTION_MS + 80);
    });
  }

  function commit(next, { motion }) {
    const useMotion = motion && !prefersReducedMotion();
    const apply = () => {
      openKey = next.openKey;
      applyOrder(next.order);
      if (openKey) followOpen();
    };
    if (!useMotion) {
      apply();
      return;
    }
    const first = captureRects();
    apply();
    playFlip(first, captureRects());
  }

  function setOpenFromClick(clickedKey) {
    const next = expandStateAfterClick({
      keys: originalKeys(),
      openKey,
      clickedKey,
    });
    commit(next, { motion: true });
  }

  function paintDetail(item) {
    const payload = item.detail(latestState) || {};
    const description = payload.description || '';
    item.lead.textContent = description;
    item.lead.hidden = !description;
    const sections = Array.isArray(payload.sections) && payload.sections.length
      ? payload.sections
      : [{ title: '', rows: Array.isArray(payload.rows) ? payload.rows : [] }];
    const body = sections
      .map((section) => {
        const heading = section.title
          ? `<h3 class="kpi-expand__section-title">${escapeText(section.title)}</h3>`
          : '';
        const rows = Array.isArray(section.rows) ? section.rows : [];
        const lines = rows
          .map((row) => {
            const label = escapeText(row.label || '');
            const value = escapeText(row.value == null || row.value === '' ? '—' : String(row.value));
            return `<div class="kpi-expand__row"><dt>${label}</dt><dd>${value}</dd></div>`;
          })
          .join('');
        return `${heading}<dl class="kpi-expand__rows">${lines}</dl>`;
      })
      .join('');
    const insetLead = description
      ? `<div class="kpi-expand__description-topic"><h3 class="kpi-expand__section-title kpi-expand__description-title">Description</h3><p class="kpi-expand__inset-lead kpi-expand__description-text">${escapeText(description)}</p></div>`
      : '';
    item.inner.innerHTML = `${insetLead}${body}`;
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
    wrap.className = 'kpi-expand card';
    wrap.dataset.expandKey = key;
    wrap.setAttribute('role', 'button');
    wrap.tabIndex = 0;
    wrap.setAttribute('aria-expanded', 'false');
    wrap.appendChild(element);
    const lead = document.createElement('p');
    lead.className = 'kpi-expand__lead';
    wrap.appendChild(lead);
    const panel = document.createElement('div');
    panel.className = 'kpi-expand__detail';
    panel.setAttribute('aria-hidden', 'true');
    const inner = document.createElement('div');
    inner.className = 'kpi-expand__detail-inner';
    panel.appendChild(inner);
    wrap.appendChild(panel);
    const item = { key, wrap, element, detail, lead, panel, inner };
    items.push(item);
    wrap.addEventListener('click', (event) => onActivate(event, item));
    wrap.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        onActivate(event, item);
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        commit({ openKey: null, order: originalKeys() }, { motion: true });
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
    commit({ openKey: null, order: originalKeys() }, { motion: true });
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
      commit(next, { motion: false });
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
