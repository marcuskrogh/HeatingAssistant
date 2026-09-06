# Implementation plan: Schedule periods counted but not shown

## Summary
- Room schedule detail counts persisted comfort periods in the section title but does not render period cards, so they cannot be edited.
- Overview still lists the period; opening the room cannot reconfigure it.
- Restore the dropped `ensureWhenState` helper and hide the empty inactive heading.

## Scope / Decisions / Constraints
- In: Ingress schedule detail card construction; inactive-section `hidden` CSS; spec-lock harness; CalVer / changelog / App sync.
- Out: Schedule engine semantics, overview row layout, experiment editor.
- Root cause: SWD-445 extracted markup and removed `ensureWhenState` while `buildPeriodCard` still calls it (`ReferenceError` after the count is written).
- Secondary: `.sched-detail__section-header { display: flex }` overrides the HTML `hidden` attribute, so INACTIVE PERIODS stays visible when empty.

## Classification
- Class: bug
- Confidence: high
- Why: Correct behaviour is known — persisted periods must appear as editable cards.

## Workflow
- Template: fix-fast
- Parameters:
  - implement.mode: single
  - implement.verify: tests
  - implement.iteration: one-shot
  - test.mode: dedicated
  - harden.mode: dedicated
  - review.mode: single
  - review.depth: focused
  - review.lasers: sequential
  - side_paths: none
  - sandbox: none
- Chain: architect → implement → test → restructure → review → ship
- Rationale: Contained panel defect; no new layers.

## Inputs
- Research: none
- Model: none
- Sandbox: none

## Pass criteria
- Room schedule detail renders one period card per persisted period (active list and inactive list as appropriate).
- Expanding a rendered card shows the period editor so the schedule can be reconfigured.
- Schedules overview still lists the period; opening the room shows that card.
- INACTIVE PERIODS is not visible when there are no inactive periods.

## Work packages
1. Restore `ensureWhenState` in schedule detail; honor `[hidden]` on section headers.
2. Spec-lock harness, CalVer, changelog, App sync.

## Open items
- None.

## Tracker
- Provider: jira
- Story: —
- Task: SWD-491
- Sub-tasks: SWD-492, SWD-493
- Branch: cursor/swd-491-schedule-periods-not-displayed-2822
- PR: https://github.com/marcuskrogh/HeatingAssistant/pull/660
- Classification: bug
- Workflow: fix-fast

## Next
Done — https://github.com/marcuskrogh/HeatingAssistant/pull/660
