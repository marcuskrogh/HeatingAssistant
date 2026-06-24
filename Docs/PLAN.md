# Heating Assistant → Home Assistant App: Feasibility & Migration Plan

> **Project goal:** Evaluate whether Heating Assistant should remain a custom integration, become a Home Assistant App (formerly add-on), or adopt a hybrid architecture — and produce a concrete migration plan if warranted.

**Status:** Complete — **decision locked: integration only**  
**Last updated:** 2026-06-24  
**Executive summary:** [STATUS.md](STATUS.md)

---

## Stakeholder decision (2026-06-24)

After reviewing the Phase A feasibility study:

> **Stay an integration only.** Do not pursue HA App or hybrid architecture. When preparing for HACS default listing, **vendor the parts of `mbc` we need** inside the integration instead of the current GitHub zip dependency.

This closes the App/hybrid investigation. The dual-track and hybrid PoC plans in PROGRESS.md §WP6–WP7 are **not proceeding**.

### Resulting path

| Item | Decision |
|------|----------|
| Architecture | Monolithic custom integration (unchanged) |
| HA App / hybrid | Declined |
| `mbc` | Vendor subset when HACS prep begins (Phase B) |
| Distribution | HACS custom repo now → default listing after vendoring → Core optional later |

See [STATUS.md §Phase B](STATUS.md#phase-b--mbc-vendoring-when-hacs-prep-starts) for vendoring scope and procedure.

---

## Background (Phase A context)

Heating Assistant runs as a **custom integration** inside Home Assistant Core. Phase A investigated whether an **App** (Supervisor-managed Docker container) would improve adoption or runtime isolation.

Key findings that informed the decision:

- Apps are **HA OS/Supervised only**; integration works on all install types.
- The integration exposes **157 entities**, **30 services**, and **9 WebSocket commands** — valuable for automations and energy dashboards.
- A hybrid App + bridge scored highest in analysis (4.05) but adds **dual maintenance** the project does not want.
- **Stay + vendored `mbc`** (3.95) achieves HACS/Core unblock with **zero architectural change**.

---

## Clarifying questions — answers

| # | Question | Answer |
|---|----------|--------|
| Architecture | Integration vs App vs hybrid? | **Integration only** |
| 9 | Vendoring acceptable? | **Yes** |
| 10 | PoC App expected? | **No** |
| 11 | Dual maintenance? | **No** |
| 1–8, others | Install base, Core priority, etc. | Deferred to HACS/Core prep |

---

## Work packages (Phase A — complete)

| ID | Work Package | Status |
|----|-------------|--------|
| WP1 | Terminology & distribution landscape | ✅ |
| WP2 | Integration architecture audit | ✅ |
| WP3 | App architecture options | ✅ |
| WP4 | Feasibility assessment | ✅ |
| WP5 | Adoption & distribution comparison | ✅ |
| WP6 | Recommended strategy & phased roadmap | ✅ (superseded by decision above) |
| WP7 | Hybrid PoC specification | ✅ (not proceeding) |

Full deliverables: [PROGRESS.md](PROGRESS.md)

---

## Revised roadmap

| Phase | Name | Status |
|-------|------|--------|
| **A** | Feasibility complete | ✅ Done |
| **B** | `mbc` vendoring for HACS | ⬜ Deferred until HACS prep |
| **C** | HACS default listing | ⬜ Future |
| **D** | Core integration submission | ⬜ Optional future |

~~Hybrid App PoC, production hybrid, App store, deprecation decision~~ — **cancelled**.

---

## Execution log

| Date | Action | Result |
|------|--------|--------|
| 2026-06-24 | Plan compiled | Initial plan created |
| 2026-06-24 | WP1–WP7 complete | See PROGRESS.md |
| 2026-06-24 | Stakeholder decision | Integration only; vendor `mbc` at HACS prep |

---

## Revision history

| Version | Date | Change |
|---------|------|--------|
| 0.1 | 2026-06-24 | Initial plan |
| 1.0 | 2026-06-24 | Phase A complete |
| **2.0** | **2026-06-24** | **Decision locked: integration only; App path closed** |
