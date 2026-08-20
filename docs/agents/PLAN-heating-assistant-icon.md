# Implementation plan: Proper Heating Assistant icon

## Summary
- The product already has a house + settling-curve + target mark in Ingress, but
  Supervisor and the thin Home Assistant integration still show a generic
  radiator or a missing brand image.
- Ship one SVG source of truth and the PNG sizes Home Assistant actually loads:
  App `icon.png` / `logo.png`, integration `brand/` (HA 2026.3+), and the
  `brands/custom_integrations/` lift copy.

## Scope / Decisions / Constraints
**In**
- Shared SVG mark (house frame, S-curve to a setpoint, target dot) in teal
  `#00d4aa`, matching the Ingress nav.
- Supervisor App assets next to `heating_assistant/config.yaml`: `icon.png`
  (128×128) and `logo.png` (~250×100).
- Thin integration `custom_components/heating_assistant/brand/` with
  `icon.png`, `icon@2x.png`, and dark variants so Settings → Devices & Services
  shows the mark on Home Assistant 2026.3+.
- `custom_components/heating_assistant/icon.svg` as the artwork source.
- `brands/custom_integrations/heating_assistant/` PNGs for a later
  home-assistant/brands lift (HA older than 2026.3).
- Ingress nav + favicon use the same SVG.
- `heating-assistant-icons.js` path data stays aligned (monochrome
  `heating-assistant:logo`).
- App package sync copies `icon.svg` and `brand/`.
- Tests, CalVer, changelog.

**Out**
- Opening a PR against home-assistant/brands (assets are prepared only).
- Changing MQTT entity / climate card icons.
- Changing `panel_icon` away from `mdi:radiator` (Supervisor sidebar only
  accepts Material Design Icons; custom icon sets are not loaded for App
  sidebar entries).
- New control / MQTT behaviour.

**Decisions**
- Keep the existing geometric mark (house + curve to setpoint). It already
  appears in Ingress; this work makes it a real brand asset, not a new identity.
- App / brand PNGs are a teal rounded-square badge with a white mark so the
  Supervisor store and integration list read as an app icon at 24–128 px.
- Ingress / favicon stay the stroke mark on a transparent field (dark UI).
- Dark-mode brand files use the same badge (teal works on HA light and dark).

**Constraints**
- Dual tree: edit `heatingassistant/` and `custom_components/`, then
  `scripts/sync-ha-app-package.sh`.
- Dev-surface keys only in tracker / PLAN / PR — not in product UI copy or
  changelog bullets.

## Classification
- Class: tweak
- Confidence: high
- Why: small intentional visual/packaging delta on an existing mark; not a
  defect and not a new product slice

## Workflow
- Template: delta-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - review.mode: single
  - review.depth: focused
  - side_paths: none
  - sandbox: none
- Chain: implement → review-fix → ship
- Rationale: localized assets + sync/tests; cheapest binding that still
  covers packaging lock risk. Visual inspect happens in implement, not a
  separate sandbox loop.

## Inputs
- Research: none
- Model: none
- Sandbox: none

## Acceptance criteria
1. `heating_assistant/icon.png` is a square PNG (128×128) and
   `heating_assistant/logo.png` is a rectangular PNG (~250×100) so Supervisor
   can show the App in the store.
2. `custom_components/heating_assistant/brand/` contains `icon.png` (256×256)
   and `icon@2x.png` (512×512) plus dark variants; App sync copies that folder.
3. `custom_components/heating_assistant/icon.svg` is the source of truth;
   Ingress nav and favicon use the same mark; `heating-assistant-icons.js`
   still registers `heating-assistant:logo`.
4. `brands/custom_integrations/heating_assistant/` has `icon.png` and
   `icon@2x.png` matching the brands README.
5. `config.yaml` `panel_icon` remains `mdi:radiator`.
6. Fast tests pass; CalVer and changelog record the App release that ships
   the assets.

## Work packages
1. Brand mark SVG/PNGs + App and integration wiring — SWD-409
2. Tests, CalVer, changelog, App sync — SWD-410

## Open items
- None. A later home-assistant/brands PR is explicitly out of this Task.

## Tracker
- Provider: jira
- Task: SWD-408
- Sub-tasks: SWD-409, SWD-410
- Branch: `cursor/swd-408-heating-assistant-icon-73a2`
- PR: (draft, opened after first commit)
- Classification: tweak
- Workflow: delta-fast

## Next
`/implement SWD-408` — Build per PLAN.md workflow binding (same branch/PR)
