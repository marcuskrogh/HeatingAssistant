# Research brief: Estimation history hole — JSONL write vs horizon load

## Question

For the multi-hour parameter-estimation gap (room plots continuous; controller
healthy): are rows **missing** from durable `id_history/*.jsonl`, or does JSONL
have continuous timestamps while `resolve_history(horizon_hours)` / the
in-memory `_history_buffer` show the hole?

## Axes covered

| Axis | Status | Notes |
|------|--------|-------|
| Preprints (arXiv) | skipped | Forensic product-path question; not a literature survey |
| Formal written | skipped | No standards/RFCs govern this App-local dual-store path |
| Web discovery | covered | Streaming systems: ring/live buffer vs durable historical store must share one load path |
| Informal / practitioner | covered | This repo (primary evidence); legacy HA tests; RisingWave/Timeplus informal parallels |

## Search strategy

- **Repo (practitioner):** `resolve_history`, `IdentificationHistoryStore`,
  `_take_identification_sample`, ticker/`update_tag` plot writers,
  `tests/test_history_access_services.py` (legacy horizon backfill).
- **Web:** dual memory / historical backfill vs live buffer consistency
  (Timeplus HistoricalStore + NativeLog; RisingWave CDC buffer vs checkpoint).

## Executive summary

**Both failure modes are code-proven; today’s incident cannot be fully
discriminated without on-device checks.** Independently of the incident:

1. **Load-path defect (definite).** App `resolve_history(horizon_hours=…)`
   returns `select_recent_window(_history_buffer)` only and **never** calls
   `id_history_store.async_query_range`. Explicit `window_start`/`window_end`
   **does** merge JSONL. Legacy HA `get_history_for_horizon` backfilled from
   JSONL when the buffer did not cover the horizon
   (`tests/test_history_access_services.py`). Default EKF / ML / open-loop
   estimation uses `horizon_hours` — so estimation can show holes that durable
   JSONL (or a custom window) would not.

2. **Write-path asymmetry (definite possibility).** Plot history is written from
   the wall-clock ticker, MQTT `update_tag`, and control. Identification
   samples are written **only** inside a completed `run_control_cycle` via
   `_take_identification_sample` → `async_append`. Buffer is updated **before**
   durable append; `OSError` on append is logged and swallowed. Continuous room
   plots + healthy MPC do **not** imply continuous ID JSONL.

**Discriminator (operator, still needed for *today*):**

| Check | If true → |
|-------|-----------|
| `id_history/<instance>/<day>.jsonl` missing timestamps in the gap | Write-path hole (H-write) |
| JSONL continuous for the gap, but horizon EKF shows hole; custom window continuous | Load-path only (H-load) |
| Logs contain `ID history store: append failed` | Dual-write disk failure (H-disk) |
| Horizon hole + custom window also hole + JSONL sparse | Real missing ID records |

Without HAOS `/data` access in this agent environment, H-write vs H-load for the
specific day remains **insufficient evidence**. Code evidence alone is enough to
define the load-path fix (SWD-320) and to keep the write-alignment Task
(SWD-318) justified.

## Key sources

1. **App `resolve_history` horizon branch** — Informal / practitioner (repo) —
   `heatingassistant/app/sysid_services.py` L161–162 — horizon returns buffer
   only; L150–158 window merges JSONL.
2. **Legacy horizon backfill test** — Informal / practitioner (repo) —
   `tests/test_history_access_services.py` L139–153 —
   `get_history_for_horizon` awaits `async_query_range` when buffer is short.
3. **ID append + silent failure** — Informal / practitioner (repo) —
   `heatingassistant/engine/history/store.py` L183–196;
   `runtime.py` L647–650, L2997–2998 (buffer before disk).
4. **Plot vs ID writers** — Informal / practitioner (repo) —
   `runtime.py` `_background_ticker_loop` L419–435 (plot without ID);
   `update_tag` L606; `run_control_cycle` L647–650 (sole ID path).
5. **Timeplus historical vs live store** — Web discovery —
   https://www.timeplus.com/post/unified-architecture-historical-real-time —
   historical backfill must not be limited to the live/log buffer retention.
6. **RisingWave CDC buffer vs checkpoint** — Informal / practitioner —
   https://github.com/risingwavelabs/risingwave/issues/25848 — volatile buffer
   ahead of durable checkpoint creates permanent gaps on recovery.

## Themes and trends

- **Agreement:** Live/ring buffers and durable stores diverge unless load and
  write paths treat them as one logical series (Timeplus; legacy HA horizon
  helper; this App’s window mode).
- **Agreement:** Silent durable-write failure with successful in-memory update
  produces session-OK / restart-broken behaviour (RisingWave CDC; App
  buffer-before-JSONL order).
- **Disagreement / product-local:** This App’s default estimation path uses the
  incomplete horizon loader, while the explicit-window path is already correct —
  a regression relative to legacy `get_history_for_horizon`.

## Gaps and limitations

- No on-device `id_history` / `plot_history` JSONL or App logs for the incident
  day — **cannot** name H-write vs H-load for that window.
- Preprints / formal axes intentionally skipped.
- Web sources are analogous systems, not HAOS Heating Assistant evidence.

## Recommended reading order

1. `heatingassistant/app/sysid_services.py` — `resolve_history`
2. `tests/test_history_access_services.py` — horizon backfill expectations
3. `heatingassistant/app/runtime.py` — ticker / `update_tag` / `run_control_cycle`
4. `heatingassistant/engine/history/store.py` — append / query_range
5. Timeplus post (web) — unified historical + live load framing

## Role in pipeline

Supportive context for `/define SWD-320` (horizon JSONL merge) and
`/define SWD-318` (ID write alignment). Does not settle UX/scope; define still
probes acceptance.

## Sources

- [repo] `heatingassistant/app/sysid_services.py` — `resolve_history` (horizon vs window). Axis: Informal / practitioner.
- [repo] `heatingassistant/app/runtime.py` — ticker, `update_tag`, `run_control_cycle`, `_take_identification_sample`. Axis: Informal / practitioner.
- [repo] `heatingassistant/engine/history/store.py` — JSONL append / `async_query_range`. Axis: Informal / practitioner.
- [repo] `tests/test_history_access_services.py` — legacy `get_history_for_horizon` JSONL backfill. Axis: Informal / practitioner.
- [web] Timeplus — “A Unified Architecture: How Timeplus Bridges Historical Backfill and Real-Time Processing” — https://www.timeplus.com/post/unified-architecture-historical-real-time. Axis: Web discovery.
- [informal] RisingWave #25848 — CDC backfill loses buffered events on crash recovery — https://github.com/risingwavelabs/risingwave/issues/25848. Axis: Informal / practitioner.

## Tracker

- Task: SWD-319
- Story: SWD-316
- Artifact: docs/agents/RESEARCH-estimation-history-hole.md
- Branch: cursor/swd-319-estimation-history-research-2dd4
- PR: (pending)

## Next

`/define SWD-320` — load-path defect is code-proven (horizon never merges JSONL); define the fix next. Operator JSONL check still useful for incident forensics and for shaping SWD-318.
