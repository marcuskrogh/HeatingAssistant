# Implementation plan: App UI consistency (typography, plots, styleguide)

## Summary
- Heating Assistant Ingress already has a colour token set, but type sizes,
  plot chrome, and shared labels drifted independently across pages, menus,
  and popups (nav at 10–11px, PE progress clock at 80px, Chart.js ticks at
  10px vs canvas plot 10px monospace with different padding, section titles
  at 8–14px).
- Introduce a closed type and plot scale on `:host`, apply it across App CSS
  and Chart.js / PE canvas, and publish `docs/agents/APP-UI-STYLEGUIDE.md` for later
  additions.

## Scope / Decisions / Constraints
**In**
- `heatingassistant/app/static/css/**` (industrial + page sheets).
- Shared Chart.js defaults (`time-series-chart.js`) and PE canvas
  (`pe-progress.js`).
- Plot heights: primary 240px, secondary 200px (Identification reconstruction
  and heating/disturbance charts included).
- Panel nav (desktop and hamburger) at UI size, not kicker size.
- PE remaining-time display at metric size (not a unique 80px clock).
- `docs/agents/APP-UI-STYLEGUIDE.md`.
- Spec-lock tests, CalVer, changelog, `scripts/sync-ha-app-package.sh`.

**Out**
- Home Assistant native sidebar / `ha-menu-button` chrome (not our CSS).
- Control, MQTT, estimation, or copy rewrites except PE plot legend casing
  to match Chart.js legends.
- Sandbox isolation trees (inspect on production UI).
- New typefaces.

**Decisions**
- Class is **feature**: app-wide visual system plus a durable styleguide.
- Closed scale: kicker 11px, caption 12px, ui 13px, subtitle 16px, title 20px,
  metric 24px, hero 32px, display 46px (climate-card current temperature only).
- Kickers (ALL-CAPS labels, chart titles, buttons, badges) stay 11px; they
  are a role, not “tiny body text”. Ban 8px/9px.
- Nav, body, forms, and back links use `--type-ui` (13px) on all breakpoints.
- Plots share one theme object: tick/legend 11px, y ticks mono, grid and
  line weight from tokens, family from `--font-sans` / `--font-mono`.

**Constraints**
- Dual tree: edit `heatingassistant/`, then sync.
- Product copy must not include tracker keys.
- Do not commit `.agents/skills` sync dirt.

## Classification
- Class: feature
- Confidence: high
- Why: intentional app-wide visual system and styleguide, not a defect and
  not a behaviour-preserving structure-only refine

## Workflow
- Template: feature-standard
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
- Rationale: production CSS/JS is the inspect surface; no isolated sandbox
  tree. Review stays focused/single — wide but one-concern visual tokens.
  Test and harden remain the floor.

## Inputs
- Research: none
- Model: none
- Sandbox: none

## Pass criteria
1. Production CSS under `heatingassistant/app/static/css/` has no
   `font-size` of 8px or 9px (or rem equivalents ≤ 0.65rem).
2. `.panel-nav__link` uses `var(--type-ui)` at the base rule and in the
   ≤1024px hamburger override (not a smaller pixel size).
3. Chart.js defaults and PE canvas `drawPlot` share tick size 11 and the
   same sans/mono families; PE plot CSS height uses
   `var(--chart-height-secondary)`.
4. Identification reconstruction charts use height 240 and heating /
   disturbance charts use 200.
5. `docs/agents/APP-UI-STYLEGUIDE.md` documents the type scale, plot tokens, and
   which class to use for nav, kickers, titles, metrics, cards, and plots.
6. App package sync copies the tokens and styleguide-locked CSS into
   `heating_assistant/heatingassistant/`.

## Work packages
1. SWD-499 — Design tokens + styleguide
2. SWD-500 — Unify fonts and shared chrome across pages
3. SWD-501 — Unify plot sizes and Chart.js / PE canvas styling
4. SWD-502 — Tests, CalVer, changelog, App sync

## Open items
- None — HA sidebar type is out of scope.

## Tracker
- Provider: jira
- Story: —
- Task: SWD-498
- Sub-tasks: SWD-499, SWD-500, SWD-501, SWD-502
- Branch: `cursor/swd-498-app-ui-consistency-dcd6`
- PR: (draft after first delivery commit)
- Classification: feature
- Workflow: feature-standard

## Next
`/architect SWD-498` — Shape stamp for tokens + shared chart theme, then implement
