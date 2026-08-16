# Implementation plan: App update changelog and restart-required Settings card

## Summary
- The Supervisor App update dialog has no changelog because the App folder has
  no `CHANGELOG.md`.
- After a thin-bridge sync, `run.sh` auto-restarts Core or creates a
  persistent notification. Other Apps/integrations put **Restart required** on
  the Settings updates list.
- Download/install percent is Supervisor docker-pull progress. This App has no
  `image:` key, so Supervisor builds locally and the bar stays at 0%. Prebuilt
  registry images are out of this slice.

## Scope / Decisions / Constraints
**In**
- `heating_assistant/CHANGELOG.md` next to `config.yaml`. Headings
  `# YYYY.MM.PATCH` (exact version, then newline) so Core can slice notes for
  the update dialog.
- WORKSPACE Changelog path points at that file so later ship appends.
- After a thin-bridge sync, do **not** auto-restart Core and do **not** create
  a persistent notification.
- App publishes an MQTT Update entity while a restart stamp is present so
  Settings shows **Restart required** (same list as App updates). Install on
  that entity requests Core restart via Supervisor.
- Stamp file records from/to versions for the entity.
- Tests, CalVer, App package sync.

**Out**
- Prebuilt container images / `image:` in `config.yaml` (needed for dynamic
  download percent; adding `image:` before packages exist would break updates).
- Changing MPC, PE, Ingress UI, or MQTT tag/bindings contract.
- HACS or Core/OS update flows.

**Decisions**
- Changelog format is Supervisor/Core’s heading regex, not Keep a Changelog
  brackets (`## [x.y.z] - date` does not match).
- Restart card is MQTT discovery from the App so it appears on the **first**
  update to this version (new integration code is not loaded until Core
  restarts).
- Native `update.py` platform stays unregistered (would duplicate the MQTT
  card on later updates).
- `HEATINGASSISTANT_AUTO_CORE_RESTART` is removed; restart is user-driven from
  Settings.

**Constraints**
- Dual tree: edit `heatingassistant/` and `custom_components/`, then
  `scripts/sync-ha-app-package.sh`.
- Dev-surface keys only in tracker / PLAN / PR — not in product UI copy or
  changelog bullets.

## Classification
- Class: tweak
- Confidence: high
- Why: small intentional UX deltas on the existing App update path; not a
  defect with a single known-correct behaviour, and not a new product slice

## Workflow
- Template: delta-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - review.mode: single
  - review.depth: focused
  - side_paths: none
- Chain: implement → review-fix → ship
- Rationale: localized packaging + MQTT discovery; efficiency-first, no new
  layers beyond a small restart helper.

## Inputs
- Research: —
- Model: —

## Acceptance criteria
1. Supervisor changelog API would find `heating_assistant/CHANGELOG.md` with a
   `# {config.yaml version}` heading.
2. After a sync that needs Core restart, the App publishes an MQTT Update
   entity whose installed/latest versions differ and whose summary is restart
   required; no `persistent_notification` call remains in `run.sh`.
3. Install payload on the command topic requests Supervisor Core restart
   (token-gated; no-op without token).
4. When the stamp is absent, discovery is cleared (empty retained payload).
5. CalVer bumped; App package synced.

## Work packages
1. App CHANGELOG.md for Supervisor update dialog (SWD-353)
2. Restart-required Settings update entity (SWD-354)
3. Tests, CalVer, App sync (SWD-355)

## Open items
- Dynamic download percent remains a Supervisor local-build limit until a
  later slice publishes registry images.

## Tracker
- Provider: jira
- Story: —
- Task: SWD-352
- Sub-tasks: SWD-353, SWD-354, SWD-355
- Branch: `cursor/swd-352-update-path-51f5`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/618
- Classification: tweak
- Workflow: delta-fast

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/618
