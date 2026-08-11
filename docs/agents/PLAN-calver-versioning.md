# Implementation plan: Calendar versioning (YYYY.MM.PATCH)

## Summary
- Replace the `2.0.x` lock with Home Assistant–style calendar versions: **`YYYY.MM.PATCH`**.
- Cut over live surfaces to **`2026.08.0`**, enforce the format in the version lock/sync path, and document the bump rules for future ships.

## Scope
**In**
- Live version strings: `heating_assistant/config.yaml`, Dockerfile `BUILD_VERSION`, root + App `pyproject.toml`, thin integration `manifest.json` / `const.py` (source + synced), `heatingassistant` package `__version__` / server banner strings.
- Version lock / `scripts/sync-ha-app-package.sh`: validate `YYYY.MM.PATCH` (zero-padded month `01`–`12`, unpadded patch — no leading zeros except `0`).
- Docs for the scheme + how to bump (README packaging note).
- Tests that assert `2.0.32` → `2026.08.0`; add coverage for format rejection / lock behaviour as needed.

**Out**
- Rewriting historical PLAN / ISSUES log `2.0.x` mentions.
- Pre-release / beta suffixes.
- Changing update/restart semantics beyond using the new string (still equality-based drift detection).

## Decisions
- Month zero-padded; patch not padded (`2026.08.0`, `2026.08.12`).
- First ship: `2026.08.0`.
- PATCH resets on calendar-month change; increments within the month.
- Keep existing App ≡ integration ≡ Dockerfile ≡ `pyproject` lock.

## Constraints
- Single shared version across the existing lock surfaces.
- No silent dual-scheme support in tooling — new format only after cutover.

## Classification
- Class: tweak
- Confidence: high
- Why: Intentional, bounded change to the packaging/version scheme and lock tooling — not a defect and not a new product slice.

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
- Rationale: Contained packaging delta; tests + focused review cover risk without feature-heavy cost.

## Inputs
- Research: none
- Model: none

## Acceptance criteria
- All live lock surfaces report **`2026.08.0`** and `scripts/sync-ha-app-package.sh` succeeds.
- Invalid version strings fail the lock/validation path.
- Docs state the scheme and bump rules (month reset / same-month +1).
- Version-related tests updated and passing; historical agent docs untouched.

## Work packages
1. **Cut over live versions** to `2026.08.0` across lock + package surfaces.
2. **Encode scheme in tooling** — validate format in sync/lock; document bump rules.
3. **Tests** — update hardcoded `2.0.32` asserts; cover validation as needed.

## Open items
- None.

## Tracker
- Provider: jira
- Story: —
- Task: SWD-307
- Sub-tasks: SWD-308 (cutover), SWD-310 (tooling+docs), SWD-309 (tests)
- Branch: `cursor/swd-307-calver-versioning-d25e` (WORKSPACE map: `swd-307-calver-versioning`)
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/597
- Classification: tweak
- Workflow: delta-fast

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/597
