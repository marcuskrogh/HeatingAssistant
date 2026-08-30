/** Static HTML builders for the parameter-estimation detail page. */

export function actionsCardHtml() {
  return `
    <div class="tuning-section__title">Actions</div>
    <p class="tuning-section__desc">
      Edit the fields below, then click Apply Parameters to activate them. Use the Stored Datasets section to run automatic parameter estimation.
    </p>
    <div class="tuning-actions">
      <button class="btn btn--primary tuning-actions__btn" id="btn-apply-params">Apply Parameters</button>
      <button class="btn btn--secondary tuning-actions__btn" id="btn-reset-defaults">Reset to Defaults</button>
      <span class="tuning-actions__status" id="action-status"></span>
    </div>
  `;
}

export function paramsCardHtml(defaults) {
  return `
    <div class="tuning-section__title">Parameter Estimation Parameters</div>

    <div class="params-subsection">
      <div class="params-subsection__title">Model Parameters</div>
      <div class="tuning-params-grid">
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-thermal-mass">Thermal Mass (C)</label>
            <button class="param-lock-btn" data-param="thermal_mass" title="Lock: hold fixed during automatic parameter estimation">Fix</button>
          </div>
          <input class="form-input" type="number" id="param-thermal-mass"
            step="100000" min="10000" value="${defaults.thermal_mass}">
          <span class="form-hint">J/K &mdash; thermal storage capacity of the room</span>
        </div>
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-r-external">Thermal Resistance (R<sub>ext</sub>)</label>
            <button class="param-lock-btn" data-param="r_external" title="Lock: hold fixed during automatic parameter estimation">Fix</button>
          </div>
          <input class="form-input" type="number" id="param-r-external"
            step="0.001" min="0.0001" value="${defaults.r_external}">
          <span class="form-hint">K/W &mdash; envelope resistance to outdoor</span>
        </div>
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-internal-gain">Internal Gain (Q<sub>int</sub>)</label>
            <button class="param-lock-btn" data-param="internal_gain" title="Lock: hold fixed during automatic parameter estimation">Fix</button>
          </div>
          <input class="form-input" type="number" id="param-internal-gain"
            step="10" value="${defaults.internal_gain}">
          <span class="form-hint">W &mdash; constant internal heat (people, appliances)</span>
        </div>
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-solar-scale">Solar Scale</label>
            <button class="param-lock-btn" data-param="solar_scale" title="Lock: hold fixed during automatic parameter estimation">Fix</button>
          </div>
          <input class="form-input" type="number" id="param-solar-scale"
            step="0.01" min="0" value="${defaults.solar_scale}">
          <span class="form-hint">&times; &mdash; multiplier on modelled solar gain (1.0 = model)</span>
        </div>
      </div>
    </div>

    <div class="params-subsection">
      <div class="params-subsection__title">Envelope Split (2R2C)</div>
      <div class="tuning-params-grid">
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-c-air-fraction">Air-node Mass Fraction</label>
            <button class="param-lock-btn" data-param="c_air_fraction" title="Lock: hold fixed during automatic parameter estimation">Fix</button>
          </div>
          <input class="form-input" type="number" id="param-c-air-fraction"
            step="0.001" min="0" max="1" value="${defaults.c_air_fraction}">
          <span class="form-hint">0&ndash;1 &mdash; share of mass on the fast air node</span>
        </div>
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-r-aw-fraction">Air&ndash;Wall Resistance Fraction</label>
            <button class="param-lock-btn" data-param="r_aw_fraction" title="Lock: hold fixed during parameter estimation">Fix</button>
          </div>
          <input class="form-input" type="number" id="param-r-aw-fraction"
            step="0.001" min="0" max="1" value="${defaults.r_aw_fraction}">
          <span class="form-hint">0&ndash;1 &mdash; fraction of conductive-path resistance on the air&harr;wall film (infiltration excluded)</span>
        </div>
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-t-wall-initial">Wall Initial Temp (T<sub>wall,0</sub>)</label>
            <button class="param-lock-btn" data-param="t_wall_initial" title="Lock: hold fixed during parameter estimation">Fix</button>
          </div>
          <input class="form-input form-input--readonly" type="text" id="param-t-wall-initial"
            readonly value="&mdash;" tabindex="-1">
          <span class="form-hint">&deg;C &mdash; identified envelope temperature at window start (populated after parameter estimation; lock to hold fixed)</span>
        </div>
        <div class="form-group">
          <div class="form-group__header">
            <label class="form-label" for="param-ua-open">Open-contact UA</label>
          </div>
          <input class="form-input form-input--readonly" type="text" id="param-ua-open"
            readonly value="&mdash;" tabindex="-1">
          <span class="form-hint">W/K &mdash; extra outdoor exchange while a window or door contact is open (populated after parameter estimation)</span>
        </div>
      </div>
    </div>

    <div class="params-subsection" id="inter-room-r-subsection" hidden>
      <div class="params-subsection__title">Inter-Room Connections</div>
      <p class="params-subsection__desc">
        Thermal resistances between this room and neighbours, estimated during parameter estimation when enough cross-room excitation is present.
      </p>
      <div class="tuning-params-grid" id="inter-room-r-list"></div>
    </div>

    <div class="params-subsection" id="heater-scales-subsection">
      <div class="params-subsection__title">Heater Power Scales</div>
      <div class="tuning-params-grid" id="heater-scales-list"></div>
    </div>

    <div class="params-subsection">
      <div class="params-subsection__title">Stochastic Parameters</div>
      <div class="tuning-params-grid">
        <div class="form-group">
          <label class="form-label" for="param-sigma-w">Process Noise (&sigma;<sub>w</sub>)</label>
          <input class="form-input" type="number" id="param-sigma-w"
            step="0.001" min="0.000001" value="${defaults.sigma_w}">
          <span class="form-hint">K/&radic;s &mdash; model adaptation rate</span>
        </div>
        <div class="form-group">
          <label class="form-label" for="param-sigma-v">Sensor Noise (&sigma;<sub>v</sub>)</label>
          <input class="form-input" type="number" id="param-sigma-v"
            step="0.001" min="0.000001" value="${defaults.sigma_v}">
          <span class="form-hint">K &mdash; expected temperature sensor noise</span>
        </div>
      </div>
    </div>

    <div class="params-subsection">
      <div class="params-subsection__title">Parameter Estimation Window</div>
      <div class="window-mode-toggle">
        <button class="window-mode-btn window-mode-btn--active" id="window-mode-recent" type="button">Recent Horizon</button>
        <button class="window-mode-btn" id="window-mode-custom" type="button">Custom Date Range</button>
      </div>
      <div id="window-panel-recent" class="tuning-params-grid">
        <div class="form-group">
          <label class="form-label" for="param-horizon">Horizon</label>
          <input class="form-input" type="number" id="param-horizon"
            step="0.5" min="0.5" value="${defaults.horizon_hours}">
          <span class="form-hint">hours &mdash; history window ending at the most recent record (up to parameter-estimation history retention)</span>
        </div>
      </div>
      <div id="window-panel-custom" class="window-datetime-panel" style="display:none">
        <div class="window-datetime-inputs">
          <div class="form-group">
            <label class="form-label" for="param-window-start">Window Start</label>
            <div class="form-input form-input--datetime-wrap">
              <input class="form-input--datetime" type="datetime-local" id="param-window-start">
            </div>
            <span class="form-hint">Start of the parameter-estimation window (local time)</span>
          </div>
          <div class="form-group">
            <label class="form-label" for="param-window-end">Window End</label>
            <div class="form-input form-input--datetime-wrap">
              <input class="form-input--datetime" type="datetime-local" id="param-window-end">
            </div>
            <span class="form-hint">End of the parameter-estimation window (local time)</span>
          </div>
        </div>
        <div class="form-group window-preset-row">
          <span class="form-hint">Quick presets:</span>
          <div class="window-presets">
            <button class="btn btn--ghost btn--sm" data-preset="1h" type="button">Last 1 h</button>
            <button class="btn btn--ghost btn--sm" data-preset="6h" type="button">Last 6 h</button>
            <button class="btn btn--ghost btn--sm" data-preset="12h" type="button">Last 12 h</button>
            <button class="btn btn--ghost btn--sm" data-preset="24h" type="button">Last 24 h</button>
          </div>
        </div>
      </div>
    </div>

    <div class="params-subsection params-subsection--last" id="ds-save-mount"></div>
  `;
}

export function validationIntroHtml() {
  return `
    <div class="tuning-section__title">Model Validation</div>
    <p class="tuning-section__desc">
      Two complementary fit tests over the parameter-estimation window configured above.
      <strong>One-step EKF reconstruction</strong> measures short-horizon tracking with Kalman
      correction at each timestep; <strong>multi-step open-loop simulation</strong> is a free-run
      drift test with no measurement feedback. A good model should score well on both.
    </p>
    <div class="grid-kpi" id="fit-comparison-kpis"></div>
    <div class="fit-comparison__hints">
      <span class="form-hint">EKF RMSE: one-step ahead with Kalman correction each timestep</span>
      <span class="form-hint">Open-loop RMSE: multi-step free-run drift without measurement feedback</span>
    </div>
  `;
}

export function historyBodyHtml() {
  return `
    <p class="tuning-section__desc" style="margin:0 0 12px">
      Previously applied parameter sets. Load one back into the fields above to
      review and re-apply it, or delete entries you no longer need.
    </p>
    <div id="param-history-list"></div>
  `;
}

export function buildValidationSection(container, spec) {
  const { title, desc, btnId, btnClass, btnLabel, statusId, kpiId } = spec;
  const section = document.createElement('div');
  section.className = 'card tuning-section';
  section.innerHTML = `
      <div class="tuning-section__title">${title}</div>
      <p class="tuning-section__desc">${desc}</p>
      <div class="tuning-actions">
        <button class="btn ${btnClass}" id="${btnId}">${btnLabel}</button>
        <span class="tuning-actions__status" id="${statusId}"></span>
      </div>
      <div class="grid-kpi" id="${kpiId}"></div>
      <div class="tuning-chart" data-chart="temp"></div>
      <div class="tuning-chart" data-chart="inputs"></div>
      <div class="tuning-chart" data-chart="disturb"></div>
    `;
  container.appendChild(section);
  return section;
}
