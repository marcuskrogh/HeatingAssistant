/** HTML builders for the schedule detail period editor. */

import {
  activeOverrideFields,
  hasOverride,
  OVERRIDE_META,
  overrideBaseline,
  SCHEDULE_TYPE_CONTINUOUS,
  SCHEDULE_TYPE_DATE_RANGE,
  SCHEDULE_TYPE_WEEKLY,
} from '../schedule-utils.js?v=124';

const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function escapeAttr(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function typeLabel(type) {
  if (type === SCHEDULE_TYPE_DATE_RANGE) return 'Date range';
  if (type === SCHEDULE_TYPE_CONTINUOUS) return 'Continuous span';
  return 'Weekly recurring';
}

export function segmentedHtml(field, value, options, extraClass = '') {
  return `<div class="schedule-form__segmented ${extraClass}" data-segmented-field="${field}">
      ${options.map((opt) => `
        <button type="button"
          class="schedule-form__segment${value === opt.value ? ' schedule-form__segment--active' : ''}"
          data-value="${opt.value}">${opt.label}</button>
      `).join('')}
    </div>`;
}

export function overrideInputHtml(field, period, defaults) {
  const meta = OVERRIDE_META[field];
  const value = hasOverride(period, field) ? period[field] : overrideBaseline(field, defaults);
  const unit = meta.unit ? `<span class="form-hint">${meta.unit}</span>` : '';
  return `
      <div class="schedule-form__override" data-override="${field}">
        <div class="schedule-form__override-main">
          <label class="form-label">${meta.label}</label>
          <div class="schedule-form__override-control">
            <input class="form-input form-input--time" type="number" step="${meta.step}" min="${meta.min}" max="${meta.max}"
              value="${escapeAttr(value)}" data-field="${field}">
            <button type="button" class="schedule-form__override-remove" data-remove-override="${field}" title="Return to inherit">Remove</button>
          </div>
          <span class="form-hint">${meta.hint}${unit ? ` - ${unit}` : ''}</span>
        </div>
      </div>
    `;
}

export function overridesHtml(period, defaults) {
  const modeFields = activeOverrideFields(period.mode || 'comfort');
  const shownFields = modeFields.filter((field) => hasOverride(period, field));
  const addable = modeFields.filter((field) => !hasOverride(period, field));
  const rows = shownFields.map((field) => overrideInputHtml(field, period, defaults)).join('');
  const empty = rows ? '' : '<p class="schedule-form__section-empty">No overrides. This period inherits room/default values.</p>';
  const picker = addable.length > 0 ? `
      <div class="schedule-form__override-picker">
        <select class="schedule-form__mode-select" data-action="override-picker" aria-label="Override to add">
          ${addable.map((field) => `<option value="${field}">${OVERRIDE_META[field].label}</option>`).join('')}
        </select>
        <button type="button" class="btn btn--sm" data-action="add-override">Add override</button>
      </div>
    ` : '<p class="schedule-form__section-empty">All overrides for this mode are shown.</p>';
  return `${empty}${rows}${picker}`;
}

export function periodHeaderHtml({ isActive, isNext, periodEnabled, preview }) {
  return `
      <div class="schedule-form__period-header-main">
        ${isActive ? '<span class="sched-detail__now-badge">NOW</span>' : ''}
        ${isNext ? '<span class="sched-detail__next-badge">NEXT</span>' : ''}
        <button type="button" class="sched-period-toggle ${periodEnabled ? 'sched-period-toggle--on' : 'sched-period-toggle--off'}" data-action="toggle-enabled" title="${periodEnabled ? 'Disable period' : 'Enable period'}">${periodEnabled ? 'ON' : 'OFF'}</button>
        <span class="sched-row__type">${escapeAttr(preview.type)}</span>
        <span class="schedule-form__period-name">${escapeAttr(preview.name)}</span>
        <span class="schedule-form__period-time">${escapeAttr(preview.timing)}</span>
        <span class="sched-row__mode ${preview.modeCls}">${escapeAttr(preview.mode)}</span>
      </div>
      <div class="schedule-form__period-header-actions">
        <span class="schedule-form__unsaved-dot" aria-hidden="true" title="Unsaved changes"></span>
        <button class="schedule-form__delete" title="Delete period" data-action="delete">×</button>
        <span class="schedule-form__expand-chevron">${preview.isExpanded ? '▲' : '▼'}</span>
      </div>
    `;
}

export function periodWhenHtml({ scheduleType, whenByType, periodIndex }) {
  const weeklyWhen = whenByType[SCHEDULE_TYPE_WEEKLY];
  const dateWhen = whenByType[SCHEDULE_TYPE_DATE_RANGE];
  const continuousWhen = whenByType[SCHEDULE_TYPE_CONTINUOUS];
  const activeWhen = whenByType[scheduleType];
  const timeMode = activeWhen?.time_mode || 'window';
  let daysHtml = '';
  for (let d = 0; d < 7; d++) {
    const on = (weeklyWhen.days || []).includes(d);
    daysHtml += `<span class="schedule-form__day${on ? ' schedule-form__day--active' : ''}" data-day="${d}">${DAY_NAMES[d]}</span>`;
  }

  if (scheduleType === SCHEDULE_TYPE_WEEKLY) {
    return `
        <div class="schedule-form__days" data-period="${periodIndex}">${daysHtml}</div>
        ${segmentedHtml('time_mode', timeMode, [
          { value: 'all_day', label: 'All day' },
          { value: 'window', label: 'Time window' },
        ], 'schedule-form__segmented--compact')}
        <div class="schedule-form__period-row schedule-form__time-row"${timeMode === 'all_day' ? ' hidden' : ''}>
          <div class="form-group">
            <label class="form-label">Start time</label>
            <input class="form-input form-input--time" type="time" value="${escapeAttr(weeklyWhen.start || '08:00')}" data-when-field="start">
          </div>
          <div class="form-group">
            <label class="form-label">End time</label>
            <input class="form-input form-input--time" type="time" value="${escapeAttr(weeklyWhen.end || '22:00')}" data-when-field="end">
          </div>
        </div>
      `;
  }
  if (scheduleType === SCHEDULE_TYPE_DATE_RANGE) {
    return `
        <div class="schedule-form__date-row">
          <div class="form-group">
            <label class="form-label">Start date</label>
            <div class="form-input form-input--datetime-wrap">
              <input class="form-input--datetime" type="date" value="${escapeAttr(dateWhen.start_date)}" data-when-field="start_date">
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">End date</label>
            <div class="form-input form-input--datetime-wrap">
              <input class="form-input--datetime" type="date" value="${escapeAttr(dateWhen.end_date)}" data-when-field="end_date">
            </div>
          </div>
        </div>
        ${segmentedHtml('time_mode', timeMode, [
          { value: 'all_day', label: 'All day' },
          { value: 'window', label: 'Time window' },
        ], 'schedule-form__segmented--compact')}
        <div class="schedule-form__period-row schedule-form__time-row"${timeMode === 'all_day' ? ' hidden' : ''}>
          <div class="form-group">
            <label class="form-label">Start time</label>
            <input class="form-input form-input--time" type="time" value="${escapeAttr(dateWhen.start || '08:00')}" data-when-field="start">
          </div>
          <div class="form-group">
            <label class="form-label">End time</label>
            <input class="form-input form-input--time" type="time" value="${escapeAttr(dateWhen.end || '22:00')}" data-when-field="end">
          </div>
        </div>
      `;
  }
  return `
        <div class="schedule-form__date-row">
          <div class="form-group">
            <label class="form-label">Start datetime</label>
            <div class="form-input form-input--datetime-wrap">
              <input class="form-input--datetime" type="datetime-local" value="${escapeAttr(continuousWhen.start_at)}" data-when-field="start_at">
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">End datetime</label>
            <div class="form-input form-input--datetime-wrap">
              <input class="form-input--datetime" type="datetime-local" value="${escapeAttr(continuousWhen.end_at)}" data-when-field="end_at">
            </div>
          </div>
        </div>
      `;
}

export function periodBodyHtml({ scheduleType, whenByType, periodIndex, period, defaults }) {
  const whenHtml = periodWhenHtml({ scheduleType, whenByType, periodIndex });
  return `
      <div class="schedule-form__editor-section">
        <div class="schedule-form__section-title">Type</div>
        ${segmentedHtml('schedule_type', scheduleType, [
          { value: SCHEDULE_TYPE_WEEKLY, label: 'Weekly recurring' },
          { value: SCHEDULE_TYPE_DATE_RANGE, label: 'Date range' },
          { value: SCHEDULE_TYPE_CONTINUOUS, label: 'Continuous span' },
        ])}
      </div>
      <div class="schedule-form__editor-section">
        <div class="schedule-form__section-title">Name</div>
        <div class="form-group">
          <input class="form-input form-input--name" type="text" value="${escapeAttr(period.name || '')}" data-field="name">
        </div>
      </div>
      <div class="schedule-form__editor-section">
        <div class="schedule-form__section-title">When <span class="schedule-form__section-subtitle">${typeLabel(scheduleType)}</span></div>
        ${whenHtml}
      </div>
      <div class="schedule-form__editor-section">
        <div class="schedule-form__section-title">Behaviour</div>
        ${segmentedHtml('mode', period.mode === 'off' ? 'off' : 'comfort', [
          { value: 'comfort', label: 'Comfort' },
          { value: 'off', label: 'Off' },
        ], 'schedule-form__segmented--compact')}
      </div>
      <div class="schedule-form__editor-section">
        <div class="schedule-form__section-title">Overrides</div>
        ${overridesHtml(period, defaults)}
      </div>
    `;
}
