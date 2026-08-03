# Research brief: Dual-mode MPC solvers (Ipopt NMPC; HPIPM / Riccati for linear)

## Question
For Heating Assistant’s **current** OCP (no Phase 7 / CasADi / acados rewrite), what do sources and the existing stack say about:

1. Using **Ipopt** as the internal solver for a **non-linear** MPC mode that embeds nonlinear dynamics in the OCP?
2. Whether **HPIPM** (esp. its Riccati / OCP-structured path) is a good internal solver for the **linear** (linearized QP) mode vs staying on **HiGHS / OSQP**?
3. What evidence-backed factors should later **define** use when writing user guidance for **linear** vs **non-linear**?

This brief is **supportive evidence only**. It does not choose defaults, UX copy, or acceptance criteria.

## Axes covered
| Axis | Status | Notes |
|------|--------|-------|
| Preprints (arXiv) | covered | `scripts/arxiv_research.py` missing in this checkout; used arXiv API via `curl` / stdlib (fallback per research skill). Queries: HPIPM, building NMPC, Ipopt+MPC, LTV/building MPC, OSQP. |
| Formal written | covered | Wächter & Biegler (Ipopt); Stellato et al. (OSQP); Frison & Diehl (HPIPM); Huangfu & Hall (HiGHS dual simplex citation); peer venue pages / DOIs. |
| Web discovery | covered | HPIPM GitHub/ar5iv, HiGHS site, cyipopt install docs, acados docs (packaging context), mpc_qpbenchmark HPIPM install notes. |
| Informal / practitioner | covered | Repo docs (HA config / controller facade / HiGHS robustness tests), cyipopt docs, qpsolvers HPIPM-from-source notes, greenhouse NMPC warm-start practice. |

## Search strategy
- **Preprints:** `ti:HPIPM OR abs:HPIPM`; building/HVAC + nonlinear MPC (`cat:eess.SY`); Ipopt + MPC; OSQP title; building + linearized/LTV MPC.
- **Formal:** DOI lookups for Ipopt (10.1007/s10107-004-0559-y), OSQP (10.1007/s12532-020-00179-2), HPIPM (arXiv:2003.02547 / Frison & Diehl), HiGHS (10.1007/s12532-017-0130-5).
- **Web:** HPIPM dense vs OCP QP; packaging BLASFEO/HPIPM; Ipopt/cyipopt warm-start for thermal NMPC.
- **Informal / codebase:** `HeatingLinearisedMPC` / condensed QP path; `mpc_solver` legacy; Ipopt in estimation; HiGHS failure-mode tests; product Phase 7 notes (out of scope here, context only).

## Executive summary
Sources consistently treat **Ipopt** as a standard, capable NLP backend for building / thermal **NMPC**, especially with warm-started shifted solutions and update intervals on the order of minutes. That matches Heating Assistant’s long control cycle and existing **cyipopt / `IpoptNLPBackend`** path already used for system ID.

For the **linear** mode, **HPIPM’s headline efficiency comes from structure-exploiting Riccati factorization of OCP-structured QPs** (stage-wise dynamics equalities). Heating Assistant’s live linear path today is a **condensed convex QP** (mbc `StandardLinearisedContinuousMPC` / absolute-input OCP → OSQP/HiGHS), **not** an OCP-structured stage-wise QP. On that formulation, Riccati does **not** apply as a drop-in; HPIPM’s **dense QP** type could theoretically replace HiGHS/OSQP after condensing, but literature and packaging practice show **native BLASFEO+HPIPM builds**, not a simple PyPI dependency — costly for a Home Assistant custom component. Given small horizons (default ~6 steps), residential room counts, and **minutes** between solves, sources do **not** show a compelling need to chase HPIPM for wall-clock reasons on the current condensed stack; **OSQP / HiGHS remain the evidence-aligned linear backends**, with known HiGHS robustness quirks already handled in-tree.

User-facing **linear vs non-linear** guidance in the literature turns on **model fidelity under nonlinearity** (COP / heat-pump maps, large excursions, emitter/UFH dynamics) versus **convexity, predictability, and lighter dependencies** — not primarily on millisecond solver races for this duty cycle. Product choices (default mode, fallbacks, exact copy) remain open for `/define`.

## Key sources
- **HPIPM: a high-performance quadratic programming framework for model predictive control** — Preprints / Formal — [arXiv:2003.02547](https://arxiv.org/abs/2003.02547) — Defines dense vs OCP vs tree QP; Riccati for OCP type; dense path for condensed QPs; Python needs BLASFEO.
- **giaf/hpipm** — Informal — https://github.com/giaf/hpipm — Install: build BLASFEO + HPIPM shared libs, then `pip install` Python interface; `LD_LIBRARY_PATH`.
- **OSQP: an operator splitting solver for quadratic programs** — Formal — DOI [10.1007/s12532-020-00179-2](https://doi.org/10.1007/s12532-020-00179-2) / [arXiv:1711.08013](https://arxiv.org/abs/1711.08013) — Robust general QP; warm-start / factorization caching; already in the linear stack’s ecosystem.
- **HiGHS** — Web / Formal citation — https://highs.dev/ ; Huangfu & Hall DOI [10.1007/s12532-017-0130-5](https://doi.org/10.1007/s12532-017-0130-5) — MIT-licensed LP/MIP/QP; used by current linear MPC; in-repo tests document spurious active-set failures + retries.
- **Wächter & Biegler, Ipopt algorithm** — Formal — DOI [10.1007/s10107-004-0559-y](https://doi.org/10.1007/s10107-004-0559-y) — Canonical interior-point NLP method behind Ipopt.
- **cyipopt install / warm-start API** — Informal / Web — https://cyipopt.readthedocs.io/stable/install.html , reference `solve(..., lagrange=, zl=, zu=)` — Conda-forge easiest; source needs system Ipopt; warm-start hooks exist.
- **Scenario-based NMPC for building heating** — Preprints — [arXiv:2012.02011](https://arxiv.org/abs/2012.02011) — Argues nonlinear building models can outperform linearized ones; positions linearized+stochastic vs nonlinear+deterministic as a recurring tradeoff.
- **AI-enhanced NMPC for greenhouses (Ipopt + warm-start)** — Formal / Informal — https://www.mdpi.com/2076-3417/15/14/7988 — CasADi + Ipopt, 15 min sample, warm-start cuts solve time without changing control quality.
- **White-box building NMPC (TACO / JModelica)** — Formal — DOI [10.3384/ecp21181315](https://doi.org/10.3384/ecp21181315) — Large nonlinear OCPs still runnable on ~15 min MPC updates with warm-start.
- **Heating Assistant controller / config (codebase)** — Informal — `controller/facade.py` (`HeatingLinearisedMPC`, condensed QP); `docs/CONFIGURATION.md` (`mpc_solver` → QP; legacy `ipopt`/`slsqp` ignored); `estimation/kalman_ml.py` (live Ipopt for ID); `tests/test_qp_solver_robustness.py` (HiGHS failure modes).

## Themes and trends
### 1. Ipopt is a fit for residential-scale thermal NMPC
Across formal Ipopt docs and building/greenhouse NMPC practice, **Ipopt** is the default open NLP engine. Warm-started shifted horizons are repeatedly cited as the practical enabler under **~15 min** updates. HA already depends on this stack for identification and historically exposed Ipopt as an MPC solver option (now ignored in favour of QP). Algorithmic fitness is not the open question; **packaging / optional dependency** and **local optima / nonconvex cost** behaviour are.

### 2. HPIPM Riccati ≠ drop-in for today’s linear path
HPIPM’s O(\(N(n_x+n_u)^3\)) Riccati path assumes an **OCP-structured QP** (stage dynamics equalities). HA’s linear mode assembles a **condensed** QP in deviation/absolute-input coordinates via mbc. Mapping to Riccati would mean **rebuilding** the linear OCP as stage-wise LTV \((A_k,B_k,\ldots)\) (possibly soft constraints / price terms adapted) — a structural change beyond swapping backends. HPIPM **dense QP** can solve condensed problems, but that is a different value proposition (IPM on dense KKT) and still carries native-build cost.

### 3. Packaging dominates the HPIPM decision for HA
Practitioner sources (HPIPM README, qpbenchmark notes, acados docs) agree: **no trivial wheels** — compile BLASFEO + HPIPM, set library paths. That is a poor match for HACS / Home Assistant custom-component distribution compared with **OSQP/HiGHS** (and optional **cyipopt** already in the ID path). acados wraps HPIPM but is explicitly **out of scope** for this initiative (Phase 7).

### 4. Linear vs non-linear is a fidelity / risk tradeoff, not a speed race here
Building-heating literature contrasts linearized models (convex, fast, miss nonlinearity) with nonlinear models (richer COP / dynamics, heavier NLP). With HA’s reported ~5 s solves on small houses and multi-minute cycles, sources support framing guidance around:

| Factor | Favours **linear** (sources) | Favours **non-linear** (sources) |
|--------|------------------------------|----------------------------------|
| Prediction under strong nonlinearity (HP maps, large ΔT, emitter lag) | Weaker | Stronger |
| Convexity / reliability / easier debugging | Stronger | Weaker (local minima) |
| Dependency / install weight | Stronger (OSQP/HiGHS) | Heavier (Ipopt/cyipopt) |
| Rooms × horizon growth | Stays cheap | NLP cost grows |
| Cycle budget (minutes) | Both usually OK | Both usually OK if warm-started |

Exact UI wording and defaults are **not** settled here.

### 5. Linear-mode fallback evidence leans “keep HiGHS/OSQP”
If HPIPM is deferred: continue **HiGHS/OSQP** condensed QP, retaining existing HiGHS retry/robustness work. That is the lowest-friction evidence-aligned path for linear mode on the current OCP.

## Gaps and limitations
- No published benchmark of **this** HA condensed QP on HPIPM dense vs HiGHS/OSQP (size, soft slacks, price terms).
- arXiv script absent; API sampling is not exhaustive (`total_results` for broad LTV query was huge — treated as discovery only).
- Did not re-run timed solver benchmarks in this session; cycle-time claims cite repo docs / prior notes and external NMPC papers.
- Whether mbc already exposes a ready **nonlinear** continuous MPC class to wire beside `StandardLinearisedContinuousMPC` needs an implement spike — not answered as product scope.
- A `/model` Task for Riccati-shaped reformulation is only motivated if define later **chooses** HPIPM OCP structure; evidence does not require it for dual-mode destination.

## Recommended reading order
1. Frison & Diehl HPIPM ([arXiv:2003.02547](https://arxiv.org/abs/2003.02547)) — dense vs OCP QP / Riccati.
2. HA `HeatingLinearisedMPC` docstring + `CONFIGURATION.md` `mpc_solver` — what “linear” means today.
3. Wächter & Biegler Ipopt DOI + cyipopt warm-start docs.
4. Pippia et al. [arXiv:2012.02011](https://arxiv.org/abs/2012.02011) — building heating linearized vs nonlinear framing.
5. OSQP DOI + HiGHS site + in-repo `test_qp_solver_robustness.py`.

## Role in pipeline
Supportive context for `/define SWD-240` (and optional `/model` only if define picks Riccati reformulation). Does **not** settle user alignment. Particulars for define remain open: default mode, exact guidance copy, failure when cyipopt missing, whether to attempt dense HPIPM at all.

## Sources
1. Frison, G. & Diehl, M. (2020). *HPIPM: a high-performance quadratic programming framework for model predictive control*. arXiv:2003.02547. Axis: Preprints / Formal. https://arxiv.org/abs/2003.02547
2. giaf/hpipm README. Axis: Informal. https://github.com/giaf/hpipm
3. Stellato, B. et al. (2020). *OSQP: an operator splitting solver for quadratic programs*. Math. Prog. Comp. DOI:10.1007/s12532-020-00179-2; arXiv:1711.08013. Axis: Formal / Preprints.
4. Huangfu, Q. & Hall, J. A. J. (2018). *Parallelizing the dual revised simplex method*. Math. Prog. Comp. DOI:10.1007/s12532-017-0130-5. Axis: Formal. Site: https://highs.dev/
5. Wächter, A. & Biegler, L. T. (2006). *On the implementation of an interior-point filter line-search algorithm for large-scale nonlinear programming*. Math. Program. DOI:10.1007/s10107-004-0559-y. Axis: Formal.
6. COIN-OR Ipopt documentation. Axis: Web. https://coin-or.github.io/Ipopt/
7. cyipopt documentation (install + Problem.solve warm-start). Axis: Informal / Web. https://cyipopt.readthedocs.io/
8. Pippia, T. et al. (2020). *Scenario-based Nonlinear Model Predictive Control for Building Heating Systems*. arXiv:2012.02011. Axis: Preprints.
9. MDPI Appl. Sci. 15(14):7988 (2025). Greenhouse NMPC with Ipopt + warm-start. Axis: Formal. https://www.mdpi.com/2076-3417/15/14/7988
10. Jorissen et al. *Detailed White-Box Non-Linear Model Predictive Control for Scalable Building HVAC Control*. DOI:10.3384/ecp21181315. Axis: Formal.
11. qpsolvers/mpc_qpbenchmark HPIPM install notes. Axis: Informal. https://github.com/qpsolvers/mpc_qpbenchmark
12. acados Python interface / installation. Axis: Web / Informal. https://docs.acados.org/python_interface/
13. Heating Assistant codebase: `custom_components/heating_assistant/controller/facade.py`, `docs/CONFIGURATION.md`, `estimation/kalman_ml.py`, `tests/test_qp_solver_robustness.py`. Axis: Informal.

## Tracker
- Task: [SWD-239](https://marcusknielsen.atlassian.net/browse/SWD-239)
- Parent map: [SWD-238](https://marcusknielsen.atlassian.net/browse/SWD-238)
- Artifact: `docs/agents/RESEARCH-swd-239-dual-mode-mpc.md`
- Branch: `cursor/swd-239-mpc-solver-research-d0ba`
- PR: *(set on open)*

## Next
`/define SWD-240` — Align dual-mode product particulars with the user; this brief is supportive context only (Ipopt for non-linear; HPIPM Riccati not a drop-in for condensed linear QP; guidance factors above remain open for UX decisions)
