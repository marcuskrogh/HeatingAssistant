# PLAN — SWD-450 catalog overlay wipes forecast attributes

**Created:** 2026-08-30  
**Issue:** [SWD-450](https://marcusknielsen.atlassian.net/browse/SWD-450)  
**Kind / workflow:** bug / fix-fast  
**Params:** implement.mode=single; verify=tests; test/harden dedicated; review=focused sequential; sandbox=none  
**Sources of truth:** `/workspace/heatingassistant/` + `/workspace/custom_components/` (App copy via `scripts/sync-ha-app-package.sh`)

## Classification

Concrete defect: room-view forecast series (linearised temperature, planned heating, price forecast, outdoor temperature) vanish or flatten after the HA entity catalog overlay. Catalog is **bug / fix-fast**.

## Root cause

`HeatingRuntime._apply_catalog_to_inbound_tags` calls `update_tag` with a GOOD scalar and **no attributes**. `update_tag` then `tag_attributes.pop`s weather_forecast / energy_price attrs. SWD-278 requires that MQTT scalar-only still clears attrs; catalog overlay must not.

Secondary: `_usable_catalog_value` returns weather condition strings (`"cloudy"`), overwriting numeric outdoor °C. `_outdoor_temperature` never falls back to `tag_attributes[weather]["temperature"]`. `_coerce_number` does not parse numeric strings.

## Next (implement)

`/test SWD-450` — Dedicated testing phase, then harden, then review-fix.

## Implement

1. Overlay: skip non-numeric/non-bool catalog values; pass existing `tag_attributes` into `MqttTagPayload`.
2. `_usable_catalog_value`: numbers and bools only.
3. `_coerce_number`: parse numeric strings.
4. `_outdoor_temperature`: fallback to weather `temperature` attr.
5. Tests `tests/test_swd450_catalog_forecast_attrs.py`.
6. CalVer 2026.08.35 + changelog + `scripts/sync-ha-app-package.sh`.
7. Dual-tree: App copy is generated; do not hand-edit `heating_assistant/`.

Do not change MQTT scalar-only attr-clear behaviour.

## Test

`pytest tests/test_swd450_catalog_forecast_attrs.py tests/test_swd278_forecast_disturbances.py tests/test_swd385_tag_quality.py tests/test_swd307_calver_versioning.py tests/test_github_app_repo.py -q`

## Harden / review-fix / ship

Dedicated test + harden. Focused sequential review. `/ship SWD-450`.
