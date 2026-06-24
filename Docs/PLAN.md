# Heating Assistant → Home Assistant App: Feasibility & Migration Plan

> **Project goal:** Evaluate whether Heating Assistant should remain a custom integration, become a Home Assistant App (formerly add-on), or adopt a hybrid architecture — and produce a concrete migration plan if warranted.

**Status:** Complete (Phase A — feasibility)  
**Last updated:** 2026-06-24  
**Owner:** Software development manager agent  
**Executive summary:** [STATUS.md](STATUS.md)

---

## Background

Heating Assistant currently runs as a **custom integration** inside Home Assistant Core. The long-term distribution plan has been HACS → eventual Core integration submission. However:

- The `mbc` control library is not on PyPI (fetched from GitHub in `manifest.json`), which blocks Core integration acceptance.
- HACS publication still requires users to add a custom repository.
- A real-time MPC control loop (~5 s per cycle, 15-min cadence) competes with HA Core's event loop.
- Home Assistant has renamed **add-ons → Apps** (late 2025): standalone containerized applications managed by the Supervisor, running *alongside* HA Core on HA OS.

The hypothesis under investigation: an **App** may offer easier adoption (bundled dependencies, App Store distribution) and better runtime isolation for a real-time control system.

---

## Clarifying Questions (for project stakeholders)

These must be answered before committing to a migration path:

### Product & audience

1. **Target install base:** What percentage of your users run Home Assistant OS / Supervised vs Container / Core-only? Is it acceptable to exclude non-OS installs?
2. **Primary distribution goal:** Is the main objective App Store visibility, Core integration acceptance, or lowest friction for existing custom-repo users?
3. **Coexistence:** Should the integration path remain supported indefinitely, or is a hard cut-over acceptable once the App is stable?

### Technical scope

4. **Entity surface:** Do users depend on Heating Assistant entities (`climate.*`, `sensor.*`) appearing in HA's entity registry for automations, energy dashboards, and third-party cards — or is the built-in sidebar panel sufficient?
5. **Control latency:** Is sub-second heater dispatch latency required, or is a 1–5 s API round-trip acceptable?
6. **Recorder integration:** Must historical sensor data live in HA's recorder, or can history be stored in the App's own database with optional export?
7. **Multi-instance:** Should one App instance control one HA instance only, or could it orchestrate multiple homes?

### `mbc` dependency

8. **PyPI timeline:** Is publishing `mbc` to PyPI on the roadmap regardless? If yes, does that change the App-vs-integration calculus?
9. **Vendoring:** Would vendoring `mbc` inside the integration (instead of PyPI) be acceptable as an alternative to an App?

### Migration appetite

10. **Effort budget:** Is this a research spike only, or is a working PoC App expected in this project phase?
11. **Maintenance model:** Are you willing to maintain two artifacts (App + thin bridge integration) if a hybrid architecture wins?

---

## Work Packages

| ID | Work Package | Status | Depends on | Deliverable |
|----|-------------|--------|------------|-------------|
| WP1 | Terminology & distribution landscape | ✅ complete | — | `PROGRESS.md` §WP1 |
| WP2 | Current integration architecture audit | ✅ complete | — | `PROGRESS.md` §WP2 |
| WP3 | App architecture options (full / hybrid / stay) | ✅ complete | WP1, WP2 | `PROGRESS.md` §WP3 |
| WP4 | Feasibility assessment: pros, cons, blockers | ✅ complete | WP1–WP3 | `PROGRESS.md` §WP4 |
| WP5 | Adoption & distribution path comparison | ✅ complete | WP4 | `PROGRESS.md` §WP5 |
| WP6 | Recommended strategy & phased roadmap | ✅ complete | WP4, WP5 | `PROGRESS.md` §WP6 |
| WP7 | PoC specification (conditional) | ✅ complete | WP6 | `PROGRESS.md` §WP7 |

---

## Recommended outcome (Phase A)

See [STATUS.md](STATUS.md) for the full executive summary. In brief:

- **Primary (HA OS/Supervised):** Hybrid App + thin bridge integration
- **Parallel (all platforms):** Stay integration + vendored `mbc`
- **Rejected:** Full App without integration (destroys entity/automation surface)

Phases B–F defined in [PROGRESS.md §WP6](PROGRESS.md#wp6-recommended-strategy--phased-roadmap).

---

## Execution Log

| Date | Action | Result |
|------|--------|--------|
| 2026-06-24 | Plan compiled | This document created |
| 2026-06-24 | WP1+WP2 assigned to subagent | Complete — see PROGRESS.md |
| 2026-06-24 | WP3+WP4+WP5 assigned to subagent | Complete — see PROGRESS.md |
| 2026-06-24 | WP6+WP7 assigned to subagent | Complete — see PROGRESS.md, STATUS.md |
| 2026-06-24 | Phase A complete | All work packages done; awaiting stakeholder review |

---

## Revision History

| Version | Date | Change |
|---------|------|--------|
| 0.1 | 2026-06-24 | Initial plan with 7 work packages and clarifying questions |
| 1.0 | 2026-06-24 | All work packages complete; Phase A feasibility closed |
