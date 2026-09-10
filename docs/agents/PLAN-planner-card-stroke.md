# Implementation plan: Even stroke on planner selection cards

## Summary
- Controller Tuning Linear/Nonlinear cards stacked a 2px border, an outer
  2px ring, and a full-height left accent bar, so the frame looked thicker
  on the left and at the corners.
- Use the same 1px `--border` hairline as `.card`. Live/selected/draft
  change colour and fill only. Radio uses a centred dot; empty badges hide.

## Scope / Decisions / Constraints
**In**
- `.tuning-mode-card*` in `heatingassistant/app/static/css/pages/tuning.css`.
- Tests, CalVer, changelog, panel cache-bust, App package sync.

**Out**
- Planner apply/switch behaviour and copy.
- Other Tuning form controls beyond incidental consistency with `.card`.

**Decisions**
- Class is **tweak**: visual polish; click/apply behaviour unchanged.
- One 1px stroke on every side; no `box-shadow` ring; no left `::before` bar.
- In-use fill stays `--accent-dim`; draft stays warning fill.
- Keyboard focus uses `outline` offset outside the frame so it does not
  thicken the border.

**Constraints**
- Dual tree: edit `heatingassistant/`, then `scripts/sync-ha-app-package.sh`.
- Product copy must not include tracker keys.

## Classification
- Class: tweak
- Confidence: high
- Why: small intentional visual delta; planner behaviour is unchanged

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
- Rationale: localized CSS polish; focused review is enough

## Inputs
- Research: none
- Model: none

## Acceptance criteria
- Idle, in-use, selected, and draft cards share the same 1px frame thickness
  on all four sides.
- In-use vs drafted-not-applied remains visually distinct.
- Apply Changes is still required to switch the live planner.
- Focus-visible ring sits outside the card, not on the border.

## Work packages
1. SWD-540 — Even planner-card stroke and selection polish
2. SWD-541 — Tests, CalVer, changelog, App package sync

## Open items
- none

## Tracker
- Provider: jira
- Story: —
- Task: SWD-539
- Sub-tasks: SWD-540, SWD-541
- Branch: swd-539-planner-card-stroke
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/680
- Classification: tweak
- Workflow: delta-fast

## Next
`/review-fix SWD-539` — Focused review then ship on the same PR
