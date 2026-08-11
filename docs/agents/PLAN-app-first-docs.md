# Implementation plan: App-first README and docs cleanup

## Summary
- Rewrite consumer README for the **HAOS App + thin MQTT bridge** only (short, direct).
- Align remaining relevant docs; **delete** obsolete integration/HACS/Lovelace docs and leftover roadmap markdown.
- Strip issue-tracker keys from consumer-facing surfaces; remove HACS packaging signals.

## Scope / Decisions / Constraints
**In**
- Root `README.md` (+ synced `heating_assistant/README.md`)
- Consumer docs: update `docs/CONFIGURATION.md`, `docs/TUNING.md`; delete obsolete `ENTITIES` / `SERVICES` / `DASHBOARDS`
- Maintainer: rewrite `docs/DEVELOPMENT.md`; replace old `docs/ROADMAP*.md` with a fresh minimal roadmap
- Purge root investigative/plan dumps; clean finished leftovers under `docs/` and `docs/agents/` (keep `WORKSPACE.md`, `ISSUES.md`, useful refs)
- Remove `hacs.json`; strip issue keys from App packaging blurbs / `ha_app/INSTALL.md`

**Out**
- No App/engine/UI behaviour changes
- No restoring HA climate platforms or HACS install path

**Decisions**
- Install: HAOS App only
- UI: Ingress panel only
- Docs: small structured set; delete unnecessary files
- No tracker keys on product surfaces

## Classification
- Class: refine
- Confidence: high
- Why: docs/packaging/structure only; runtime App behaviour unchanged

## Workflow
- Template: structure-safe
- Parameters:
  - implement.mode: single
  - implement.verify: non-regression
  - implement.iteration: one-shot
  - review.mode: single
  - review.depth: focused
  - side_paths: none
- Chain: implement → review-fix → ship
- Rationale: cheapest binding for docs-only refine with App-vs-docs parity checks

## Inputs
- Research: none
- Model: none

## Acceptance criteria
- README is App-first, short/direct; no HACS/manual paths; no `SWD-*`/`SW-*` in consumer surfaces
- Install/setup matches App + Mosquitto + thin bridge + Ingress + START
- Panel pages match current nav
- Requirements mention MQTT; no false HA pip/`mbc` claims
- Linked docs accurate or removed; no dead links from README
- `hacs.json` removed; packaging description has no issue keys
- Root leftover dumps cleaned; DEVELOPMENT rewritten for App architecture
- `heating_assistant/README.md` synced with root README
- Packaging tests updated for new INSTALL anchor

## Work packages
1. Consumer README rewrite + App sync — SWD-312
2. Consumer docs update/delete — SWD-313
3. Maintainer docs + cleanup + remove HACS — SWD-314

## Open items
- Salvage only architecture facts into DEVELOPMENT (no long roadmap archive)
- Site lat/long: document current App defaults, not HA wizard

## Tracker
- Provider: jira
- Task: SWD-311
- Sub-tasks: SWD-312, SWD-313, SWD-314
- Branch: `cursor/swd-311-app-first-docs-75fa`
- PR: (pending)
- Classification: refine
- Workflow: structure-safe

## Next
`/ship SWD-311` — review-fix CLEAN; merge and closeout
