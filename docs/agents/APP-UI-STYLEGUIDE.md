# App UI styleguide

Use this file when adding or changing Heating Assistant Ingress UI
(pages, popups, plots, menus). Tokens live on `:host` in
`heatingassistant/app/static/css/industrial.css`. Do not invent a new
pixel size for a role that already has a token.

The panel runs in shadow DOM. Tokens are CSS custom properties on `:host`,
so page stylesheets loaded into the same shadow root inherit them.

## Colour (existing)

| Token | Role |
| --- | --- |
| `--bg-primary` | Page background, plot fill |
| `--bg-card` | Cards, modal surfaces |
| `--bg-elevated` | Raised controls |
| `--border` | Hairline borders, plot grid fallback |
| `--text-primary` | Titles, primary values |
| `--text-secondary` | Body, legends, supporting copy |
| `--text-dim` | Kickers, axis ticks, muted labels |
| `--accent` | Interactive emphasis, live state |
| `--warning` / `--alarm` / `--good` | Status |
| `--chart-*` | Series colours (temperature, power, solar, …) |

Do not add a second accent for a new page. Identification-in-progress uses
`--experiment`.

## Type scale

| Token | Size | Role |
| --- | --- | --- |
| `--type-kicker` | 11px | ALL-CAPS labels: section headers, chart titles, KPI/gauge labels, buttons, badges, plot legends, metric labels |
| `--type-caption` | 12px | Supporting copy, pills, table body, hints |
| `--type-ui` | 13px | Panel nav (including hamburger), body text, form inputs, back links, loading |
| `--type-subtitle` | 16px | Card titles, modal titles, secondary metric figures |
| `--type-title` | 20px | Page and room titles (`h2.room-header__title`) |
| `--type-metric` | 24px | KPI / gauge / countdown / PE remaining-time values |
| `--type-hero` | 32px | Climate target and compact climate numbers |
| `--type-display` | 46px | Climate-card **current** temperature only |
| `--type-icon` | 18px | Icon glyphs in round buttons |
| `--type-icon-lg` | 24px | Close ×, landing chevron |

Rules:

- Never use 8px or 9px. Kickers are 11px.
- Similar elements share one token across Overview, Schedules, Tuning,
  Parameter Estimation, System Status, Configuration, and popups.
- ALL-CAPS chrome uses `--type-kicker` plus `letter-spacing: 0.08em–0.1em`
  and `font-weight: 600` or `700`.
- Sentence-case titles use `--type-title` or `--type-subtitle`, never kicker.
- Numeric readouts use `--font-mono` and `font-variant-numeric: tabular-nums`
  when they tick.
- Nav must stay `--type-ui` on every breakpoint. Do not shrink hamburger
  links below 13px.

## Shared chrome

| Element | Class | Type |
| --- | --- | --- |
| Panel nav links | `.panel-nav__link` | `--type-ui` |
| Brand name | `.panel-nav__name` | `--type-ui` |
| Page section header | `.section-header` | `--type-kicker` |
| Card / tuning section | `.tuning-section__title` | `--type-kicker` |
| Collapsible title | `.ha-collapsible__title` | `--type-kicker` |
| Room / page title | `.room-header__title` | `--type-title` |
| Back control | `.nav-back` | `--type-ui` |
| Primary actions | `.btn` | `--type-kicker` (uppercase) |
| Cards | `.card` | 20px padding, `--radius`, `--border` |
| Modal | `.ha-modal` / `.pe-progress` | `--bg-card`, `--radius`, kicker + title + metrics |

Popups (PE progress, HA modal) reuse the same kicker → title → metric
stack as pages. A popup must not introduce a unique display size except
`--type-display` on the climate card.

## Plots

Room-view plots are the guide. They live in `time-series-chart.js` +
`room-charts.js` and are not restyled to match other screens. Other plots
(Identification Chart.js, PE canvas) copy those CSS-pixel values.

| Token / constant | Value | Use |
| --- | --- | --- |
| `--chart-height-primary` / `CHART_HEIGHT_PRIMARY` | 240px | Temperature / reconstruction / forecast |
| `--chart-height-secondary` / `CHART_HEIGHT_SECONDARY` | 200px | Power, disturbances, PE progress, default |
| Tick / legend | 10px | Room Chart.js `font.size: 10`; PE canvas ticks |
| Tooltip | 11px | Room Chart.js tooltips only |
| Primary series | 2 | Room temp/power/disturbance `borderWidth` |
| Dashed overlay | 1.5 / `[5, 5]` | `makeDataset({ dashed: true })` |
| NOW marker | 9px system-ui | Room plugin only |
| Chart card title | `.chart-container__title` | `--type-kicker` |

Canvas plots must call `sizePlotCanvas(canvas)` so `lineWidth: 2` is 2 CSS
pixels (a 680×200 bitmap stretched with `width: 100%` looks thinner). Use
`CHART_LINE_WIDTH` / `CHART_DASH_*` / `CHART_TICK_SIZE` from
`chart-theme.js`. Do not set Chart.js `elements.line.borderWidth` globally —
room series set width per dataset.

Do not set plot height to 180 or 260. Do not change room dataset
`borderWidth`, dash, or tension.

Legend copy is sentence case (Chart.js label text). Plot card titles stay
ALL-CAPS kickers.

## Narrow screens

- Nav stays `--type-ui` (13px). Hamburger links use `min-height: 44px` and
  `12px 14px` padding; do not shrink type.
- PE overlay scrolls (`align-items: flex-start`) at `max-width: 768px`.
- Room plot heights stay 240 / 200. Chart.js already disables sticky
  tooltips on coarse / ≤768px viewports.

## Adding a page

1. Load the existing page CSS bundle from `industrial-dashboard.js` (or add
   one file under `css/pages/` and register it there).
2. Use `.section-header`, `.card`, `.btn`, `.room-header__title`,
   `.tuning-section__title` instead of new title classes when the role
   matches.
3. New type roles need a new token **and** a styleguide row — not a one-off
   pixel value.
4. New plots: `new TimeSeriesChart(el, { title, yLabel, height: CHART_HEIGHT_* })`.
5. After CSS/JS edits, run `scripts/sync-ha-app-package.sh`.
