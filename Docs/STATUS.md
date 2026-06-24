# Heating Assistant → Home Assistant App: Project Status

> **Last updated:** 2026-06-24  
> **Phase:** A — Feasibility complete · **Decision locked**  
> **Full analysis:** [PROGRESS.md](PROGRESS.md) · **Plan:** [PLAN.md](PLAN.md)

---

## Executive summary

Phase A evaluated whether Heating Assistant should remain an integration, become a Home Assistant App, or adopt a hybrid architecture. Seven work packages (WP1–WP7) are complete.

**Stakeholder decision (2026-06-24):** **Stay integration only.** No App or hybrid path. When preparing for HACS default listing, **vendor the parts of `mbc` we need** inside the integration rather than depending on a GitHub zip URL in `manifest.json`.

| Decision | Choice |
|----------|--------|
| Architecture | **Integration only** (current monolithic design) |
| HA App / hybrid | **Declined** |
| `mbc` packaging | **Vendoring** (subset), deferred until HACS prep |
| PyPI publish for `mbc` | Not required for this path |

This aligns with the feasibility study's **Stay + vendored `mbc`** track (weighted score 3.95): single codebase, all install types, no dual maintenance, entity/automation surface unchanged.

---

## Locked roadmap

| Phase | Name | Status | Trigger |
|-------|------|--------|---------|
| **A** | Feasibility complete | ✅ **Done** | — |
| **B** | `mbc` vendoring for HACS | ⬜ **Deferred** | When HACS default listing prep begins |
| **C** | HACS default listing | ⬜ Future | After Phase B |
| **D** | Core integration submission | ⬜ Future | Optional; after HACS stable |

**Cancelled:** Hybrid App PoC (former Phase C), production hybrid release, App store submission, deprecation decision.

---

## Phase B — `mbc` vendoring (when HACS prep starts)

Not started. Scope documented here for when the work begins.

### Why vendoring

- Current `manifest.json` fetches `mbc` from a GitHub zip — blocks Core submission and is fragile for HACS review.
- Vendoring the **subset actually imported** avoids maintaining a separate PyPI package while keeping installs self-contained.

### `mbc` surface used by Heating Assistant

| Module | Symbols | Used in |
|--------|---------|---------|
| `mbc.models` | `ContinuousDiscreteSDE` | `controller.py`, tests |
| `mbc.estimation` | `ContinuousDiscreteEKF`, `ContinuousDiscreteEKFParams`, `IntegrationScheme` | `controller.py`, `sysid.py`, tests |
| `mbc.control` | `StandardLinearisedContinuousMPC` | `controller.py` |
| `mbc.control` | `IpoptNLPBackend`, `NLPProblem`, `ScipyNLPBackend` | `parameter_estimator.py` |
| `mbc.control.qp_solver` | `HighsQPBackend`, `QPProblem` | tests (transitive from MPC stack) |
| `mbc.identification` | `cd_ped_neg_log_likelihood`, `nelder_mead` | `parameter_estimator.py` |

Vendoring should include transitive dependencies within `mbc` required by these entry points (QP solver, NLP backends, integration helpers). Prune unused subpackages after import analysis rather than copying the full repo blindly.

### Suggested approach

1. Pin the current commit (`5b0a7098…` in `manifest.json`) as the vendor baseline.
2. Copy into `custom_components/heating_assistant/vendor/mbc/` (or `_vendor/mbc/`).
3. Update imports to use the vendored path (e.g. relative import or `sys.path` shim in `__init__.py`).
4. Remove `mbc@https://…` from `manifest.json`; keep `numpy`, `scipy`, `highspy` as external requirements.
5. Add `vendor/mbc/LICENSE` and upstream attribution in README.
6. Document sync procedure for pulling upstream `mbc` updates.
7. Run full `pytest` suite; verify fresh HA install with no outbound GitHub fetch.

### Success criteria (Phase B)

- Fresh HA install succeeds without GitHub zip fetch for `mbc`
- All tests pass (`pytest tests/ -v -m "not slow"`)
- HACS default-listing dependency requirements met

---

## Stakeholder answers (recorded)

| # | Question | Answer |
|---|----------|--------|
| Architecture | Integration vs App vs hybrid? | **Integration only** |
| Q9 | Vendoring acceptable? | **Yes** — preferred over App/PyPI for HACS |
| Q10 | PoC App expected? | **No** — research/feasibility only |
| Q11 | Dual maintenance? | **No** — App path declined |
| Q3 | Coexistence | N/A — single integration path |

Remaining questions (install-base split, Core submission priority) can be decided at HACS/Core prep time.

---

## Work package completion

| WP | Title | Status | Outcome |
|----|-------|--------|---------|
| WP1–WP5 | Feasibility analysis | ✅ | Documented in PROGRESS.md |
| WP6 | Strategy & roadmap | ✅ | Superseded by stakeholder decision above |
| WP7 | Hybrid PoC spec | ✅ | **Not proceeding** — reference only |

---

## Next actions

| # | Action | When |
|---|--------|------|
| 1 | Continue feature work on monolithic integration | Now |
| 2 | Phase B: vendor `mbc` subset | When HACS default listing prep starts |
| 3 | HACS default listing application | After Phase B |
| 4 | Core integration PR (optional) | After HACS stable |

---

## Document index

| Document | Purpose |
|----------|---------|
| [PLAN.md](PLAN.md) | Original plan + stakeholder decision |
| [PROGRESS.md](PROGRESS.md) | Full WP1–WP7 analysis (historical) |
| [STATUS.md](STATUS.md) | This executive summary |
