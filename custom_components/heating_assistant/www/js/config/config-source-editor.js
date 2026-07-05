import { setPanelHash } from '../panel-hash.js?v=94';
import {
  updateHeatSources,
  updateRooms,
  updateSystemConfig,
  updateSystemParams,
  updateUiSettings,
} from '../ha-services.js?v=94';
import { ICONS } from './config-icons.js?v=94';
import { LANDING_CARDS } from './config-landing.js?v=94';
import {
  ROOM_SIZE_PRESETS,
  HOUSE_AGE_PRESETS,
  nearestPreset,
} from './config-presets.js?v=94';
import {
  el,
  schedulePanelNav,
  sectionCard,
  advancedSubsection,
  configListHeader,
  configPageShell,
  actionsBar,
  editorActionsBar,
  setStatus,
  numberField,
  textField,
  selectField,
  paramGrid,
  prettify,
  loadingNode,
  fmt,
  entitySelectorField,
  listEditor,
} from './config-ui.js?v=94';

// Heat sources — editor (sections driven by HVAC mode)
// ---------------------------------------------------------------------------

function renderSourceEditor(container, connection, hass, idxParam) {
  const navTimers = [];
  const page = {
    update() {},
    destroy() { navTimers.forEach((id) => clearTimeout(id)); },
  };

  const isNewInitial = idxParam === 'new';
  const { body } = configPageShell(container, {
    backLabel: 'HEAT SOURCES',
    backHash: '#config/sources',
    title: isNewInitial ? 'NEW HEAT SOURCE' : 'EDIT SOURCE',
    description: LANDING_CARDS.find((c) => c.hash === '#config/sources').desc,
  });
  body.appendChild(loadingNode());

  connection.getModelConfig().then((cfg) => {
    const allSources = (cfg && cfg.heat_sources) ? cfg.heat_sources.map((s) => ({ ...s })) : [];
    const roomNames = ((cfg && cfg.rooms) || []).map((r) => r.name).filter(Boolean);
    const enums = (cfg && cfg.enums) || {};
    const isNew = idxParam === 'new';
    const idx = isNew ? allSources.length : Number(idxParam);

    if (!isNew && (Number.isNaN(idx) || idx < 0 || idx >= allSources.length)) {
      body.innerHTML = '<div class="loading">Heat source not found.</div>';
      return;
    }

    const src = isNew
      ? { name: '', type: 'electric_heater', room: roomNames[0] || '', max_power: 2000, hvac_mode: 'heat_cool' }
      : { ...allSources[idx] };

    const titleEl = container.querySelector('.section-header');
    if (titleEl) titleEl.textContent = isNew ? 'NEW HEAT SOURCE' : `EDIT SOURCE: ${src.name || ''}`;

    body.innerHTML = '';

    // Dynamic body re-renders when type or HVAC mode changes (so the heating /
    // cooling sections appear and disappear with the selected mode).
    const dynamic = el('div');
    body.appendChild(dynamic);

    const HP_TYPES = ['heat_pump', 'ground_source_heat_pump'];
    function modeIncludes(part) {
      if (!HP_TYPES.includes(src.type)) return part === 'heat';
      const m = src.hvac_mode || 'heat_cool';
      return m === 'heat_cool' || m === part;
    }

    function renderFields() {
      dynamic.innerHTML = '';

      // ── Shared settings ──────────────────────────────────────────────────
      const sharedCard = sectionCard('Shared settings',
        'Identity, placement, and the entity the controller commands.');
      const typeField = selectField(src, 'type', 'Type',
        enums.source_types || ['electric_heater', 'heat_pump', 'ground_source_heat_pump',
          'gas_heater', 'oil_boiler', 'hydronic_radiator', 'hydronic_floor_heating',
          'electric_floor_heating', 'oil_radiator', 'pellet_stove',
          'electric_storage_heater', 'generic_thermostat'],
        { hint: 'Type of heat source.', onChange: () => renderFields() });
      const sharedFields = [
        textField(src, 'name', 'Name', { placeholder: 'living_room_heater', hint: 'Unique identifier.' }),
        typeField,
        selectField(src, 'room', 'Room', roomNames.length ? roomNames : [''], { hint: 'Room this source heats.' }),
        entitySelectorField(container, hass, src, 'heater_entity', 'Driven entity',
          HP_TYPES.includes(src.type) ? ['climate'] : ['switch', 'input_boolean', 'climate', 'number'],
          { hint: 'Entity the controller commands.' }),
      ];
      if (HP_TYPES.includes(src.type)) {
        sharedFields.push(selectField(src, 'hvac_mode', 'HVAC mode',
          enums.hvac_modes || ['heat', 'cool', 'heat_cool'],
          { def: 'heat_cool', hint: 'Determines which settings below apply.', onChange: () => renderFields() }));
      }
      sharedCard.appendChild(paramGrid(...sharedFields));
      const sharedAdv = advancedSubsection(sharedCard, 'Advanced');
      sharedAdv.appendChild(paramGrid(
        numberField(src, 'emitter_time_constant', 'Emitter time constant', { step: 30, unit: 's', min: 0, hint: '0 for electric; ~600 for hydronic radiators.' }),
      ));
      dynamic.appendChild(sharedCard);

      // ── Heating settings ─────────────────────────────────────────────────
      if (modeIncludes('heat')) {
        if (src.type === 'heat_pump') {
          const heatCard = sectionCard('Heating settings',
            'Enter the unit as it appears on the datasheet: the electrical power it draws '
            + 'and its COP. The heat output is derived (power input × COP).');
          // Virtual datasheet fields → stored as thermal max_power + cop_rated.
          const hp = {
            power_input: (src.max_power != null && src.cop_rated != null)
              ? Math.round(src.max_power / src.cop_rated) : 1300,
            cop: src.cop_rated != null ? src.cop_rated : 3.5,
          };
          const derived = el('span', 'config-derived');
          const setDerived = () => {
            derived.textContent = `Rated heat output ≈ ${src.max_power != null ? src.max_power : '—'} W (power input × COP).`;
          };
          const syncHeat = () => {
            const pin = Number(hp.power_input) || 0;
            const cop = Number(hp.cop) || 0;
            src.cop_rated = cop;
            src.max_power = Math.round(pin * cop);
            setDerived();
          };
          heatCard.appendChild(paramGrid(
            numberField(hp, 'power_input', 'Rated power input', { step: 50, unit: 'W', min: 0, onChange: syncHeat, hint: 'Electrical power the unit draws at full load (datasheet "power input").' }),
            numberField(hp, 'cop', 'Rated COP (heating)', { step: 0.1, min: 1, onChange: syncHeat, hint: 'Heating COP at the reference outdoor temperature.' }),
            numberField(src, 'min_power', 'Minimum heat output', { step: 100, unit: 'W', min: 0, hint: 'Lowest thermal output the unit modulates to (0 = none).' }),
          ));
          heatCard.appendChild(derived);
          // Seed stored values when missing/inconsistent; otherwise keep them exact.
          if (src.cop_rated == null || src.max_power == null) syncHeat(); else setDerived();
          const heatAdv = advancedSubsection(heatCard, 'Advanced');
          heatAdv.appendChild(paramGrid(
            numberField(src, 'cop_temp_ref', 'COP reference temp', { step: 1, unit: '°C', hint: 'Outdoor temp the rated COP is measured at (datasheet point, e.g. 7 °C).' }),
            numberField(src, 'max_temp_offset', 'Max temp offset', { step: 0.5, unit: '°C', min: 0, hint: 'Setpoint offset at full power.' }),
          ));
          dynamic.appendChild(heatCard);
        } else if (src.type === 'ground_source_heat_pump') {
          const heatCard = sectionCard('Heating settings',
            'Ground-source heat pumps have a near-constant COP year-round because '
            + 'the ground loop temperature barely changes with seasons.');
          const gshp = {
            power_input: (src.max_power != null && src.cop_rated != null)
              ? Math.round(src.max_power / src.cop_rated) : 1300,
            cop: src.cop_rated != null ? src.cop_rated : 4.5,
          };
          const derived = el('span', 'config-derived');
          const setDerived = () => {
            derived.textContent = `Rated heat output ≈ ${src.max_power != null ? src.max_power : '—'} W (power input × COP).`;
          };
          const syncHeat = () => {
            const pin = Number(gshp.power_input) || 0;
            const cop = Number(gshp.cop) || 0;
            src.cop_rated = cop;
            src.max_power = Math.round(pin * cop);
            setDerived();
          };
          heatCard.appendChild(paramGrid(
            numberField(gshp, 'power_input', 'Rated power input', { step: 50, unit: 'W', min: 0, onChange: syncHeat, hint: 'Electrical power the unit draws at full load.' }),
            numberField(gshp, 'cop', 'Rated COP (flat)', { step: 0.1, min: 1, onChange: syncHeat, hint: 'Heating COP — constant, not outdoor-temperature-dependent.' }),
            numberField(src, 'min_power', 'Minimum heat output', { step: 100, unit: 'W', min: 0, hint: 'Lowest thermal output the unit modulates to (0 = none).' }),
          ));
          heatCard.appendChild(derived);
          if (src.cop_rated == null || src.max_power == null) syncHeat(); else setDerived();
          const heatAdv = advancedSubsection(heatCard, 'Advanced');
          heatAdv.appendChild(paramGrid(
            numberField(src, 'max_temp_offset', 'Max temp offset', { step: 0.5, unit: '°C', min: 0, hint: 'Setpoint offset at full power.' }),
          ));
          dynamic.appendChild(heatCard);
        } else if (src.type === 'pellet_stove') {
          const heatCard = sectionCard('Heating settings',
            'Pellet stoves burn biomass and have a minimum firing level below which '
            + 'they shut off entirely. Heat output = rated power × efficiency × fraction.');
          heatCard.appendChild(paramGrid(
            numberField(src, 'max_power', 'Rated fuel power', { step: 100, unit: 'W', min: 0, hint: 'Nominal thermal input power at full load.' }),
            numberField(src, 'efficiency', 'Combustion efficiency', { step: 0.01, min: 0.5, max: 1, hint: 'Fraction of fuel energy delivered as heat (0.85–0.93 typical).' }),
            numberField(src, 'min_power_fraction', 'Min firing fraction', { step: 0.05, min: 0.1, max: 0.9, hint: 'Fraction of rated power below which the stove shuts off (default 0.30).' }),
          ));
          dynamic.appendChild(heatCard);
        } else if (src.type === 'electric_storage_heater') {
          const heatCard = sectionCard('Heating settings',
            'Storage heaters charge overnight (charge_power) and release heat passively '
            + 'during the day. The boost coil (max_power) provides real-time supplemental heat.');
          heatCard.appendChild(paramGrid(
            numberField(src, 'max_power', 'Boost power', { step: 100, unit: 'W', min: 0, hint: 'Real-time boost coil power (separate from stored heat).' }),
            numberField(src, 'charge_power', 'Charge power', { step: 100, unit: 'W', min: 0, hint: 'Electrical power drawn during overnight charging.' }),
            numberField(src, 'storage_capacity_kwh', 'Storage capacity', { step: 0.5, unit: 'kWh', min: 0, hint: 'Maximum storable heat energy (default 8 kWh).' }),
          ));
          dynamic.appendChild(heatCard);
        } else {
          const heatCard = sectionCard('Heating settings',
            'Thermal output = rated power × efficiency.');
          heatCard.appendChild(paramGrid(
            numberField(src, 'max_power', 'Rated power', { step: 100, unit: 'W', min: 0, hint: 'Thermal (or fuel) power at full output.' }),
            numberField(src, 'efficiency', 'Efficiency', { step: 0.05, min: 0, max: 1, hint: 'Fraction of input energy delivered as heat (1.0 for resistive heaters).' }),
          ));
          dynamic.appendChild(heatCard);
        }
      }

      // ── Cooling settings ─────────────────────────────────────────────────
      if (modeIncludes('cool')) {
        const coolCard = sectionCard('Cooling settings',
          'Cooling COP (EER) is max cooling power ÷ electrical power input. If your datasheet '
          + 'lists a max cooling power and a power input, divide them (e.g. 5000 W ÷ 1600 W ≈ 3.1).');
        const eff = () => (src.cooling_efficiency != null ? Number(src.cooling_efficiency) : 1);
        if (!modeIncludes('heat')) {
          // Cool-only: there is no heating section, so capture the electrical
          // input here. Stored as max_power with cop_rated = 1 so the model's
          // electrical input equals the entered value.
          const co = {
            // Recover electrical input: divide by cop_rated when it carries a
            // heating COP (switched from heat/cool); cool-only stores cop_rated=1.
            power_input: (src.max_power != null && src.cop_rated)
              ? Math.round(src.max_power / src.cop_rated)
              : (src.max_power != null ? Math.round(src.max_power) : 1300),
            cop: src.cooling_cop != null ? src.cooling_cop : 2.5,
          };
          const derived = el('span', 'config-derived');
          const setDerived = () => {
            const pin = Number(co.power_input) || 0;
            derived.textContent = `Max cooling power ≈ ${Math.round(pin * (Number(co.cop) || 0) * eff())} W (power input × EER).`;
          };
          const syncCool = () => {
            src.cop_rated = 1;
            src.max_power = Math.round(Number(co.power_input) || 0);
            src.cooling_cop = Number(co.cop) || 0;
            setDerived();
          };
          coolCard.appendChild(paramGrid(
            numberField(co, 'power_input', 'Rated power input', { step: 50, unit: 'W', min: 0, onChange: syncCool, hint: 'Electrical power drawn while cooling.' }),
            numberField(co, 'cop', 'Cooling COP (EER)', { step: 0.1, min: 0, onChange: syncCool, hint: 'Max cooling power ÷ power input.' }),
          ));
          coolCard.appendChild(derived);
          syncCool();
          const coolAdv = advancedSubsection(coolCard, 'Advanced');
          coolAdv.appendChild(paramGrid(
            numberField(src, 'cooling_efficiency', 'Cooling efficiency', { step: 0.05, min: 0, max: 1, onChange: setDerived, hint: 'Fraction of cooling capacity used (1.0 = full).' }),
          ));
          dynamic.appendChild(coolCard);
        } else {
          // Reversible unit: cooling capacity is derived from the same electrical
          // input as heating, so only the cooling COP (EER) is needed here.
          if (src.cooling_cop == null) src.cooling_cop = 2.5;
          const pinHeat = (src.max_power && src.cop_rated) ? src.max_power / src.cop_rated : 0;
          const derived = el('span', 'config-derived');
          const setDerived = () => {
            derived.textContent = pinHeat > 0
              ? `Max cooling power ≈ ${Math.round(pinHeat * (Number(src.cooling_cop) || 0) * eff())} W (heating power input × EER).`
              : '';
          };
          coolCard.appendChild(paramGrid(
            numberField(src, 'cooling_cop', 'Cooling COP (EER)', { step: 0.1, min: 0, onChange: setDerived, hint: 'Uses the heating power input above as the electrical input.' }),
          ));
          coolCard.appendChild(derived);
          setDerived();
          const coolAdv = advancedSubsection(coolCard, 'Advanced');
          coolAdv.appendChild(paramGrid(
            numberField(src, 'cooling_efficiency', 'Cooling efficiency', { step: 0.05, min: 0, max: 1, onChange: setDerived, hint: 'Fraction of cooling capacity used (1.0 = full).' }),
          ));
          dynamic.appendChild(coolCard);
        }
      }
    }

    renderFields();

    const actions = editorActionsBar({
      primaryLabel: isNew ? 'Create Source' : 'Save Changes',
      showDelete: !isNew,
      deleteLabel: 'Delete Source',
    });
    body.appendChild(actions);
    const statusEl = actions.querySelector('[data-role="status"]');

    actions.querySelector('[data-role="save"]').addEventListener('click', async (e) => {
      const btn = e.currentTarget;
      if (!src.name || !String(src.name).trim()) { setStatus(statusEl, 'A name is required.', 'error'); return; }
      if (!src.room) { setStatus(statusEl, 'A room must be selected.', 'error'); return; }
      btn.disabled = true;
      setStatus(statusEl, 'Saving… (the model will restart)', 'running');
      try {
        const cleaned = cleanSource(src);
        const next = allSources.map((s) => ({ ...s }));
        if (isNew) next.push(cleaned); else next[idx] = cleaned;
        await updateHeatSources(hass, { heat_sources: next });
        setStatus(statusEl, 'Saved. Restarting model…', 'success');
        navTimers.push(schedulePanelNav('#config/sources', 800));
      } catch (err) {
        setStatus(statusEl, 'Error: ' + (err.message || err), 'error');
        btn.disabled = false;
      }
    });

    const delBtn = actions.querySelector('[data-role="delete"]');
    if (delBtn) {
      delBtn.addEventListener('click', async () => {
        if (!window.confirm(`Delete heat source "${src.name}"?`)) return;
        delBtn.disabled = true;
        setStatus(statusEl, 'Deleting…', 'running');
        try {
          const next = allSources.filter((_, i) => i !== idx).map((s) => ({ ...s }));
          await updateHeatSources(hass, { heat_sources: next });
          setStatus(statusEl, 'Deleted. Restarting model…', 'success');
          navTimers.push(schedulePanelNav('#config/sources', 800));
        } catch (err) {
          setStatus(statusEl, 'Error: ' + (err.message || err), 'error');
          delBtn.disabled = false;
        }
      });
    }
  });

  return page;
}

function cleanSource(src) {
  const out = { ...src };
  out.name = String(out.name).trim();
  out.max_power = Number(out.max_power || 0);
  const HP_TYPES = ['heat_pump', 'ground_source_heat_pump'];
  // Drop keys irrelevant to the chosen type/mode so stale values don't linger.
  if (!HP_TYPES.includes(out.type)) {
    ['cop_rated', 'cop_temp_ref', 'min_power', 'max_temp_offset', 'hvac_mode',
      'cooling_cop', 'cooling_efficiency', 'heating_efficiency'].forEach((k) => delete out[k]);
  } else {
    delete out.efficiency;
    const m = out.hvac_mode || 'heat_cool';
    if (m === 'cool') {
      ['cop_temp_ref', 'min_power', 'max_temp_offset'].forEach((k) => delete out[k]);
    }
    if (m === 'heat') {
      ['cooling_cop', 'cooling_efficiency'].forEach((k) => delete out[k]);
    }
    // GSHP has no outdoor-temp COP correction, so cop_temp_ref is unused.
    if (out.type === 'ground_source_heat_pump') delete out.cop_temp_ref;
  }
  // Strip type-specific keys that don't belong to other types.
  if (out.type !== 'pellet_stove') delete out.min_power_fraction;
  if (out.type !== 'electric_storage_heater') {
    ['charge_power', 'storage_capacity_kwh', 'passive_discharge_rate'].forEach((k) => delete out[k]);
  }
  return out;
}

// ---------------------------------------------------------------------------

export { renderSourceEditor, cleanSource };
