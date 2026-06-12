/**
 * Reusable collapsible / expandable section.
 *
 * A single, consistent expandable-menu primitive for the whole dashboard: a
 * clickable header showing a title on the left and a chevron on the right that
 * rotates when open, plus a body that shows/hides. Designed to be dropped into a
 * `.card` (or used standalone) so every expandable menu in the app looks and
 * behaves identically.
 *
 * Usage:
 *   const sec = createCollapsible({ title: 'Experiment Scheduler', open: false });
 *   sec.body.innerHTML = '…';            // or append nodes to sec.body
 *   sec.setBadge('2 active');            // optional right-aligned status pill
 *   card.appendChild(sec.element);
 *
 * Returns { element, header, body, setOpen, isOpen, setBadge }.
 */
export function createCollapsible({ title = '', open = false, badge = null } = {}) {
  const element = document.createElement('div');
  element.className = 'ha-collapsible' + (open ? ' ha-collapsible--open' : '');

  const header = document.createElement('button');
  header.type = 'button';
  header.className = 'ha-collapsible__header';
  header.setAttribute('aria-expanded', open ? 'true' : 'false');
  header.innerHTML = `
    <span class="ha-collapsible__title">${title}</span>
    <span class="ha-collapsible__meta">
      <span class="ha-collapsible__badge" hidden></span>
      <svg class="ha-collapsible__chevron" width="18" height="18" viewBox="0 0 24 24"
           fill="none" stroke="currentColor" stroke-width="2.2"
           stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <polyline points="6 9 12 15 18 9"></polyline>
      </svg>
    </span>
  `;

  const body = document.createElement('div');
  body.className = 'ha-collapsible__body';
  if (!open) body.hidden = true;

  const badgeEl = header.querySelector('.ha-collapsible__badge');

  function setOpen(next) {
    body.hidden = !next;
    header.setAttribute('aria-expanded', next ? 'true' : 'false');
    element.classList.toggle('ha-collapsible--open', next);
  }

  function setBadge(text) {
    if (text) {
      badgeEl.textContent = text;
      badgeEl.hidden = false;
    } else {
      badgeEl.hidden = true;
    }
  }

  header.addEventListener('click', () => setOpen(body.hidden));
  if (badge) setBadge(badge);

  element.appendChild(header);
  element.appendChild(body);

  return {
    element,
    header,
    body,
    setOpen,
    isOpen: () => !body.hidden,
    setBadge,
  };
}
