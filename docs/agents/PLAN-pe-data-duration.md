# Implementation plan: Guide PE users on how much data to gather

## Summary
- Parameter Estimation already shows category coverage (envelope, heater,
  solar, open UA). Users still lack a duration hint: one day can cover
  every category, but several days usually make a better model.
- Add that guidance on the Recommended data block and in Tuning.

## Scope / Decisions / Constraints
**In**
- Recommended data description on the Parameter Estimation room page
  (`sysid-datasets.js` `.pe-coverage__desc`).
- Matching note in `docs/TUNING.md` Automatic parameter estimation.
- Source-string tests for the new copy. Cache-bust the PE JS import
  chain. CalVer `2026.08.10`, changelog, App package sync.

**Out**
- Changing category coverage thresholds or the PE algorithm.
- New duration meters, progress bars, or extra checklist tiles.
- Long experiment essays or per-category duration rewrites (envelope
  already says 12 h / a full day is better).
- Gating **Run recommended estimation** on total elapsed days.

**Decisions**
- One short contrast: a single day can cover every category; several
  days usually give a more reliable model. Do not invent a specific
  day count.
- Keep the existing “tap a category” sentence. Append duration after it.
- Product copy stays on the Recommended data block (where users decide
  whether they have enough). Tuning gets the same fact for docs readers.

**Constraints**
- Dual tree: edit `heatingassistant/`, then `scripts/sync-ha-app-package.sh`.
- Dev-surface keys only in tracker / PLAN / PR — not in product UI copy.

## Classification
- Class: tweak
- Confidence: high
- Why: small intentional copy delta on the existing PE guidance surface;
  not a defect and not a new product slice

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
- Rationale: localized UI/docs copy; existing source-string tests cover
  it. Efficiency-first: not feature-heavy.

## Inputs
- Prior: SWD-335 recommended-data tiles; SWD-343 category guides
- Surface: `heatingassistant/app/static/js/identification/sysid-datasets.js`
- Docs: `docs/TUNING.md`

## Acceptance criteria
1. Recommended data text states that one day of recording can cover
   every category, and that several days usually give a more reliable
   model.
2. `docs/TUNING.md` states the same duration contrast.
3. Existing category guides and coverage logic are unchanged.
4. Tests fail if that copy is removed. CalVer bumped; App package synced.

## Work packages
1. PE page + TUNING duration guidance copy (SWD-390)
2. Tests, CalVer, App sync (SWD-391)

## Open items
- None.

## Tracker
- Provider: jira
- Task: [SWD-389](https://marcusknielsen.atlassian.net/browse/SWD-389)
- Relates: [SWD-335](https://marcusknielsen.atlassian.net/browse/SWD-335),
  [SWD-343](https://marcusknielsen.atlassian.net/browse/SWD-343)
- Sub-tasks: SWD-390, SWD-391
- Branch: `cursor/swd-389-pe-data-duration-67d2`
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/621
- Classification: tweak
- Workflow: delta-fast

## Next
`/review-fix SWD-389` — Focused review on the same PR
