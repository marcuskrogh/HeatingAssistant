# Heating Assistant → Home Assistant App: Project Status

> **Last updated:** 2026-06-24  
> **Phase:** A — Feasibility complete  
> **Full analysis:** [PROGRESS.md](PROGRESS.md) · **Plan:** [PLAN.md](PLAN.md)

---

## Executive summary

This project evaluated whether Heating Assistant should remain a custom integration, become a Home Assistant **App** (formerly add-on), or adopt a **hybrid** architecture. Seven work packages (WP1–WP7) are complete.

**Bottom line:** Pursue a **dual-track strategy**:

| Track | Path | Audience |
|-------|------|----------|
| **Primary (OS/Supervised)** | **Hybrid App + thin bridge integration** | Users on HA OS/Supervised who want MPC runtime isolation and bundled `mbc` |
| **Parallel (all platforms)** | **Stay integration + vendored `mbc`** | Container/Core users; fallback during hybrid maturation; HACS/Core submission |

**Full App (no integration) is rejected** — it eliminates 157 HA entities, 30 services, and recorder integration that users depend on for automations and energy dashboards.

The hybrid approach scores highest in the weighted decision matrix (4.05 vs 3.95 Stay, 3.05 Full App) by isolating the ~5 s MPC solve in a Docker container while preserving entity/automation UX through a bridge (~3–5k LOC estimated).

---

## Final recommendation

### Default (pending stakeholder confirmation)

Assuming typical constraints — users want entities/automations, mixed install base, dual maintenance acceptable for PoC only:

1. **Proceed to Phase C (Hybrid PoC)** per [WP7 specification](PROGRESS.md#wp7-poc-specification): 1 room, App runs MPC tick, bridge exposes climate + key sensors, actuator via bridge.
2. **Run Phase B in parallel:** vendored `mbc` (or PyPI if roadmap confirms) to unblock HACS default listing and Core submission for the integration path.
3. **Maintain monolithic integration** for non-OS users and as regression baseline until Phase D production hybrid is validated.
4. **Reassess dual maintenance** at Phase F after one stable production release cycle.

### What we are not recommending

- **Full App** as primary path (entity surface loss, ~30k LOC port)
- **Hard deprecation** of the monolithic integration before hybrid is production-proven
- **Core submission** before `mbc` packaging blocker is resolved (Phase B)

---

## Work package completion

| WP | Title | Status | Key outcome |
|----|-------|--------|-------------|
| WP1 | Terminology & distribution landscape | ✅ | Apps = Supervisor containers; OS-only; REST/WS/Ingress communication |
| WP2 | Integration architecture audit | ✅ | 157 entities, 30 services, 9 WS commands; ~36% portable control / 64% HA glue |
| WP3 | App architecture options | ✅ | Full / Hybrid / Stay compared; hybrid protocol sketched |
| WP4 | Feasibility assessment | ✅ | Hybrid preferred (4.05 weighted); 13 risks documented |
| WP5 | Adoption & distribution comparison | ✅ | OS users → Hybrid #1; Container/Core → Stay #1 |
| WP6 | Recommended strategy & phased roadmap | ✅ | Dual-track default; decision tree; phases A–F |
| WP7 | PoC specification | ✅ | 1-room hybrid scope; API contract; Dockerfile; acceptance tests |

---

## Phased roadmap (summary)

| Phase | Name | Status | Next milestone |
|-------|------|--------|----------------|
| **A** | Feasibility complete | ✅ **Done** | Stakeholder review |
| **B** | `mbc` packaging (PyPI or vendoring) | ⬜ Pending | Vendor `mbc` or publish to PyPI |
| **C** | Hybrid PoC | ⬜ Pending | Build per WP7; acceptance tests T1–T10 |
| **D** | Production hybrid release | ⬜ Future | Multi-room, full entity surface, multi-arch CI |
| **E** | HACS / Core submission | ⬜ Future | HACS listing + Core PR for bridge |
| **F** | Deprecation decision | ⬜ Future | Coexist vs deprecate monolith |

See [WP6 phased roadmap](PROGRESS.md#3-phased-roadmap-technical-milestones) for full milestone detail and success criteria.

---

## Open questions for stakeholders

Answers to [PLAN.md clarifying questions](PLAN.md#clarifying-questions-for-project-stakeholders) will confirm or override the default recommendation. Priority items:

| Priority | # | Question | Default assumed | Impact if different |
|----------|---|----------|-----------------|---------------------|
| **High** | 4 | Do users depend on HA entities for automations/recorder? | Yes | "Panel only" → Full App niche viable |
| **High** | 11 | Is dual maintenance (App + bridge) acceptable long-term? | PoC only | Rejected → Stay + vendor; defer App |
| **High** | 10 | Is a working PoC expected this phase? | Yes (WP7 ready) | Research only → stop after Phase A |
| **High** | 1 | OS/Supervised vs Container/Core install split? | Majority OS | >30% Container/Core → elevate Stay to co-primary |
| **Medium** | 2 | Primary distribution goal? | Balanced | App Store vs Core vs HACS shifts Phase B–E priority |
| **Medium** | 3 | Hard cut-over vs indefinite coexistence? | Coexist | Hard cut-over accelerates Phase F |
| **Medium** | 8 | PyPI `mbc` on roadmap? | TBD | Yes → Phase B PyPI; strengthens Core path |
| **Medium** | 9 | Vendoring acceptable? | Yes | Rejected → App bundling primary for `mbc` |
| **Low** | 5 | Sub-second actuator latency? | Not required | Required → bridge actuation mode B mandatory |
| **Low** | 6 | All diagnostic history in recorder? | Yes | App-owned DB → reduced bridge sensor set OK |
| **Low** | 7 | Multi-instance App? | One HA per App | Multi-home → out of PoC scope |

---

## Top risks

| ID | Risk | Mitigation |
|----|------|------------|
| R1 | App/bridge version mismatch | Shared semver; health checks; hold last action |
| R5 | Dual maintenance stalls features | PoC-only commitment; reassess at Phase F |
| R4 | Container/Core users excluded from App | Stay path remains first-class |
| R10 | Missed tick during App restart | Stale-tick detection; safe idle mode |
| R9 | Core rejection (non-PyPI `mbc`) | Phase B vendoring or PyPI |

Full register: [WP6 §4](PROGRESS.md#4-risk-register-consolidated-from-wp4).

---

## Next actions

| # | Action | Owner | Depends on |
|---|--------|-------|------------|
| 1 | **Stakeholder review** — answer PLAN.md Q1, Q4, Q10, Q11 | Product | — |
| 2 | **Phase B kickoff** — decide PyPI vs vendoring for `mbc` | Dev | Stakeholder Q8, Q9 |
| 3 | **Phase C kickoff** — implement WP7 PoC (if Q10 confirms) | Dev | Stakeholder Q10, Q11 |
| 4 | **HACS prep** — default listing application after Phase B | Dev | Phase B complete |
| 5 | **Do not modify monolith** for bridge until PoC validated | Dev | Phase C T9 (regression gate) |

---

## Key metrics (reference install: 5 rooms, 5 sources)

| Metric | Current integration | Hybrid target |
|--------|--------------------|--------------|
| HA entities | 157 | 157 (via bridge) |
| Domain services | 30 | 30 (split App/bridge in production) |
| MPC solve location | HA event loop (~5 s block) | App container (isolated) |
| `mbc` install | GitHub zip at setup | Docker bundle (App) + vendor (Stay) |
| Platform coverage | All install types | OS: App+bridge; others: Stay |
| Maintenance artifacts | 1 | 2 (PoC → production reassess) |

---

## Document index

| Document | Purpose |
|----------|---------|
| [PLAN.md](PLAN.md) | Project plan, clarifying questions, work package definitions |
| [PROGRESS.md](PROGRESS.md) | Full WP1–WP7 analysis and deliverables |
| [STATUS.md](STATUS.md) | This executive summary (updated at phase gates) |
