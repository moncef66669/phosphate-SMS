# Model-knowledge quality assessment

Assessment date: **2026-09-20**  
Package assessed: **MPH stockyard planning model, version 1.9.0**
Acceptance rule: model-formulation skill quality gate, at least 18/20 with no
critical zero.

## Evidence inspected

- executable solver routing, uniform solve, rolling solve, mixed-resolution
  solve, objective refinement, parcel consolidation, minute execution, washing,
  KPI, model-context, and run-bundle code;
- active scenario/configuration semantics represented in the package;
- the prior combined project report and both newly rendered split reports;
- package and skill validators plus focused analysis-tool tests.

## Iteration 1 — rejected

| Criterion | Score | Finding |
|---|---:|---|
| Scope and evidence hierarchy | 2 | Decision boundary and source status were explicit. |
| Parameters, variables, units and scaling | 2 | Wet/dry and integer/scaled bases were covered. |
| Objective modes and protection | 2 | `service_first` and legacy weighted behavior were current. |
| Constraint classes | 2 | Hard, soft and post-solve acceptance were distinguished. |
| Transformations and state | 2 | Mass, quality, yield and delay behavior were covered. |
| Executable algorithms | 0 | Critical failure: no end-to-end uniform, weekly, mixed, fallback, or state-carry procedure. |
| Solver semantics | 2 | Status and primary optimality scope were documented. |
| Extraction and KPIs | 2 | Displayed jobs, fragmentation and KPI bases were covered. |
| Traceability and drift | 1 | Core mapping existed but orchestration modules were outside the fingerprint. |
| Publication integrity | 2 | The first split rendered cleanly from the canonical formulation. |

First-pass score: **17/20, rejected because a critical item scored zero**.

## Corrections applied for iteration 2

- added explicit entry routing and uniform, rolling, mixed-resolution,
  acceptance/repair, protected-refinement, and consolidation algorithms;
- added stable algorithm records and code anchors to `model_manifest.json`;
- added `planning_time.py` and `parcel_consolidation.py` to the implementation
  fingerprint;
- expanded `CODE_MAP.md` with the procedural solve paths;
- made the two-report build and canonical-source ownership explicit;
- strengthened the reusable model-formulation skill with an algorithm trace,
  scored quality gate, iteration requirement, and PDF visual-QA requirement.

## Iteration 2 — final assessment

| Criterion | Score | Evidence |
|---|---:|---|
| Scope and evidence hierarchy | 2 | Formulation §§ Scope and Evidence. |
| Parameters, variables, units and scaling | 2 | Formulation §§ Parameters, Variables, Bases. |
| Objective modes and protection | 2 | Formulation § Objective and manifest objective terms. |
| Constraint classes | 2 | Formulation § Constraints and manifest constraint records. |
| Transformations and state | 2 | Core balances, washing/G10, and rolling carry-forward. |
| Executable algorithms | 2 | Formulation § Optimization algorithms and manifest algorithm records. |
| Solver semantics | 2 | Status, budgets, determinism, fallback and optimality scope. |
| Extraction and KPIs | 2 | Post-processing, displayed jobs and KPI definitions. |
| Traceability and drift | 2 | Stable code anchors and synchronized source fingerprint. |
| Publication integrity | 2 | Canonical generation, PDF text checks, and all-page visual QA. |

Final score: **20/20, accepted**, subject to the validation results recorded
below.

## Validation record

Release verification results:

- model package validator: **passed**;
- manifest fingerprint check: **passed; all declared hashes current**;
- model-formulation skill validator: **passed**;
- focused analysis/model-context tests: **8 passed**;
- split-report builder syntax check: **passed**;
- scope PDF: **10 pages, 47,694 extracted characters, no blank pages or
  replacement characters; all pages visually reviewed**;
- formulation PDF: **5 pages, 18,961 extracted characters, no blank pages or
  replacement characters; all pages visually reviewed**;
- required algorithm-section text checks: **passed** for optimization
  algorithms, weekly rolling, mixed resolution, `service_first`, and parcel
  consolidation; the obsolete weighted-only objective phrase is absent;
- publication renderer was corrected after visual QA found numbered-list
  restarts; both PDFs were regenerated and the final pages re-reviewed.

## Remaining interpretation boundaries

The acceptance score means the package accurately describes the verified
implementation. It does not validate provisional washing coefficients, approve
the G10 specification, prove commercial cost completeness, or turn a rolling
window solution into a monolithic whole-horizon optimality certificate.

## Version 1.2.0 semantic update — train-export contracts

The package was rechecked after adding BG-22, BG10, BG11 and BG AD as
campaign-selected `BG-TRAIN-PROD` receiving specifications. The review traced
the source transcription through scenario construction, persistence,
validation, candidate eligibility, CP-SAT quality bounds, simulation holds,
KPI attribution and the weekly-demand UI.

- The four labels are specification grades and cannot identify physical stock.
- Campaign boundaries split quality averaging windows.
- Physical material remains eligible by chemistry rather than label equality.
- Required but absent BG11/BG AD assays block certification and remain visible
  as unmet demand.
- BG-22/BG10 P2O5 source ranges are preserved but not duplicated beside BPL.
- Strict source `<` limits retain their exact provenance while the executable
  model uses inclusive maxima; this approximation is explicit.
- The former S0 default remains unchanged, preventing silent activation of an
  infeasible low-moisture export campaign.

Version 1.2.0 release checks passed: the package validator and synchronized
source fingerprint; **16 focused tests** covering contract transcription,
solver selection, KPI attribution, persistence, migration, UI rendering and
run-model context; and a regenerated **5-page, 21,735-character** formulation
PDF with no blank pages or replacement characters. All five pages were
visually reviewed. The full package score remains **20/20**.

## Version 1.3.0 semantic update — G10 washing selectivity

The prior calibration allowed normal raw feed to qualify through Simple
washing, so the service-first hierarchy correctly preferred its 85% recovery
and lower unit cost over Full washing. The physical feasibility seed reinforced
that bias by selecting the highest-yield treatment without first checking the
G10 transformation.

Version 1.3.0 changes the relative assay responses to Simple
`[+12,-15,-15,-45,-50,+20]%` and Full
`[+24,-40,-45,-70,-60,+20]%`, maps the normal Benguerir washing campaigns to
the existing `RAW-TBT` assay, and makes the seed choose among G10-capable modes
before maximizing recovery. The low-Cd campaign remains a legitimate Simple-
washing opportunity; no minimum Full-washing share is imposed.

Verification evidence:

- nominal `RAW-TBT` fails G10 under Simple and passes under Full;
- `RAW-BT-LOW-CD` passes under Simple, retaining an economically rational use
  for the higher-yield treatment;
- a fresh seven-day solve selected **22,101 t Full-washing feed** and **10,670 t
  Simple-washing feed**, rather than one mode exclusively;
- the fresh plan was `FEASIBLE`, not `OPTIMAL`, with an 82.05% reported gap, so
  it verifies selectable treatment behavior but not globally optimal service or
  economic KPIs;
- the coefficients remain provisional pending plant or laboratory calibration.

The focused washing/configuration suite passed **56 tests**. The affected
Benguerir planning/raw-yard regression covered **30 passing tests** (28 passed
in the initial run and the two corrected bounded-solve cases passed on rerun).
The model-package validator and synchronized implementation fingerprint also
passed. The package score remains **20/20 for semantic fidelity**, while
physical calibration maturity is explicitly **conditional**.

## Version 1.4.0 semantic update â€” pre-washing feed blending

Version 1.4.0 adds optimizer-backed pre-washing blending rather than a display-
only matrix. Explicit cross-feed transfers connect the two configured raw
stockpiles at each site's common reclaim header. For an assigned washing line,
the solver enforces daily source-share bounds and one common treatment mode;
dormant alternative transfers are excluded when their blend assignment is off.

Input/output validation covered valid, disabled, malformed, and built-in route
contracts. The controlled numerical case delivered its full 500 t order, used
both sources within 40â€“60% limits, selected exactly one treatment mode, and
preserved the disabled single-source baseline. The expanded pre-washing,
washing, migration, UI, review-scenario and M16 consistency suite passed
**91 tests**.

An all-lines commissioning assignment did not produce an incumbent within the
bounded first-window search and is not shipped as the default. The adjusted
default assigns only `LOU-WASH-L1`; the remaining routeable lines are visible
but unassigned for incremental commissioning. A fresh seven-day S0 acceptance
run then returned `FEASIBLE` with validated minute execution in **165.782 s**.
The active Louta L1 campaign used **5,256 t from 110LG-OL1 and 500 t from
110LG-OL2** (91.313% / 8.687%) under Full washing, satisfying its 5â€“95% share
limits. Across S0, Full-washing feed was **21,920 t** and Simple-washing feed
was **10,670 t**.

This is a behavior and contract acceptance result, not an economic optimum:
the run was FEASIBLE with a **99.60% reported primary gap**, service was only
**33.61%**, and 391,989 t remained unmet, dominated by provisional hard G10
assays/specifications and other capacity/availability competition. It also
reported 173 scheduled starts and eight short-parcel buckets. Therefore the
software I/O verdict is **PASS**, the full S0 optimization/economic verdict is
**CONDITIONAL**, and the 5â€“95% matrix plus Full-only assignment remain
commissioning assumptions pending approved plant recipes and assay data.

## Version 1.5.0 semantic update — permanent PAP G10 demand

The prior implementation synchronized PAP demand to `G10` when washing was
enabled, but the canonical S0 presets still stored TBT/BT/MT PAP campaigns.
Reapplying a preset—or disabling the treatment feature—could therefore restore
a physical stream grade as PAP demand. That contradicted the required
receiving contract even though the enabled default UI usually hid the defect.

Version 1.5.0 makes `G10` invariant for PAP-1 and PAP-2 at the destination and
explicit bucket levels. All demand presets reapply G10 after rebuilding the
week; disabling treatment decisions leaves the G10 contract active; validation
and the weekly editor reject non-G10 PAP campaigns; and hot-session migration
rewrites legacy PAP rows. Physical source, stock and treatment-output grade
identity remains unchanged—G10 is still a receiving specification and final
certification, never a relabelled stock.

Verification evidence:

- both fresh S2/S3 destinations request G10 and expose the six hard limits;
- every explicit S3 PAP demand row requests G10 after export;
- every demand preset preserves G10;
- enabling and then disabling treatment decisions preserves G10;
- a legacy v37 session is migrated from TBT PAP rows to G10;
- a non-G10 weekly PAP edit is rejected before solve;
- the directly affected demand, washing, UI, persistence and backend suite
  passed **98 tests**;
- the wider pre-washing, M16, monitor, grade-profile, flow, configuration and
  import/export regression produced **112 passes** plus one stale 71-BPL
  expectation, which was corrected to the canonical provisional G10 target of
  75 and passed on rerun.

The software-contract verdict is **PASS**. Physical maturity remains
**conditional** because the six numeric G10 intervals are provisional rather
than an approved plant standard.

## Version 1.6.0 semantic update — RAW-TBT/G10 recalibration and S0 feasibility

The project-team-supplied RAW-TBT vector is now BPL 54.10%, Cd 16 ppm, MgO
1.52%, SiO2 14.39% and CO2 8.32%. Per explicit instruction, the separately
supplied 5% H2O value was not imported: the prior 12% RAW-TBT moisture and the
existing +20% relative treatment response remain active. The supplied G10
profile is represented as BPL `[62.5,100]`, Cd `[12,18]`, MgO `[0,0.8]`,
SiO2 `[0,7.5]`, CO2 `[6,6.5]`, and moisture `[0,25]`; 100% is a neutral BPL
ceiling because no upper limit was supplied.

The provisional relative response vectors
`[BPL,Cd,MgO,SiO2,CO2,moisture]` are Simple
`[+12,-5,-30,-28,-26,+20]%` and Full
`[+23,-20,-64,-50,-27,+20]%`. RAW-TBT therefore produces
`[60.592,15.2,1.064,10.3608,6.1568,14.4]` after Simple and fails G10, while
Full produces `[66.543,12.8,0.5472,7.195,6.0736,14.4]` and passes. The
existing low-Cd raw grade still passes under Simple, so mode selection is
quality/economics-driven rather than imposed by a minimum treatment share.

Two solver defects exposed by the recalibration were corrected. A chevron
matrix is now constrained by reachable receiving specifications even when its
construction window has zero demand, with component count used only as a
lowest-order tie-break. The label-only washing precursor now omits PAP
certification demand and chevron construction that it cannot represent; both
remain unchanged in the authoritative typed CP-SAT model. This removed the
reproducible first-window `UNKNOWN` on washing-enabled `BLEND_SHOWCASE`.

Verification evidence:

- **131 affected-subsystem tests passed**, covering model context, raw grades,
  material profiles, washing configuration, treatment mechanics, pre-washing
  blending, optimized chevron scheduling, receiving checks, and weekly demand;
- the focused physical-seed regression confirms that PAP and chevron are
  removed only from the private precursor copy and unchanged on the scenario;
- an exact UI-equivalent S0 (`BLEND_SHOWCASE`, saved grade catalogue, washing
  enabled) completed all seven windows as `FEASIBLE` in **313.613 s**, with
  **237 jobs** and zero minute-execution validation errors;
- the plan selected **14,959 t Full feed → 10,015 t product** and **9,711 t
  Simple feed → 8,544 t product**. Full used 10,782 t RAW-TBT, while Simple
  used 8,326 t RAW-BT-LOW-CD; both treatment options are therefore active;
- the bounded windows were W1–W5 `FEASIBLE` and W6–W7 `OPTIMAL`; this is not a
  whole-week optimality certificate;
- final-product service was **33.21%**, reported on-spec mass share was
  **67.14%**, and one **1 t** allocation remained. With default
  `MIN_JOB_VOLUME_T=0`, the parcel penalty is a preference below protected
  service/raw-feed/cost tiers and cannot guarantee removal of every small job.

The complete 1,175-test suite was not accepted as globally green: a diagnostic
run was stopped early after reproducing unrelated baseline failures in stale
Benguerir evidence/display assertions, a bounded S2 signature, and an existing
source-encoding sweep. These are outside the affected 131-test calibration
set and are recorded rather than concealed.

The package quality score remains **20/20 for semantic fidelity** after hash
synchronization and package validation. The operational/model verdict is
**CONDITIONAL**: software feasibility and intended mode selectivity are
demonstrated, but the response coefficients, yields and G10 intervals still
need paired plant/laboratory validation, the week is not globally optimal, the
service level is low, and a hard positive job minimum is required if zero
micro-jobs is an acceptance condition.

## Version 1.7.0 semantic update — site-specific pre-washing share modes

Pre-washing configuration now selects Louta and Benguerir independently. Each
site exposes the same two share-policy choices used by the MPH blend workflow:
an exact fixed matrix or optimizer-selected shares inside lower/upper bounds.
The distinction is active in CP-SAT rather than display-only: fixed matrix
shares are translated to equal lower and upper daily source-share constraints,
while optimized mode retains the configured envelope. Both modes continue to
require one common treatment option for all components of a line/day campaign.

Validation covered configuration schema, invalid fixed-share totals, route
gating, UI persistence and numerical solver behavior. A controlled 50/50
matrix produced exactly equal source feed tonnage; a 110% matrix was rejected
before solve. The affected blending/washing and UI set passed **71 tests**.
The software-contract verdict is **PASS**. Physical validity remains
**CONDITIONAL** because fixed matrix percentages remain blank pending approved
plant recipes and the 5–95% optimized envelopes are commissioning defaults.

## Version 1.8.0 semantic update — shared catalogues and enable-only assets

The previous MPH form copied mode, batch and matrix data into an individual
stockpile record, which implied a permanent matrix assignment. Version 1.8.0
replaces that contract with one shared M16 policy and a reusable matrix
catalogue. Each M16 stockpile now owns only its blending enable flag and
physical campaign state. With more than one fixed matrix, CP-SAT selects
exactly one feasible catalogue entry per unresolved campaign; the selected ID
and exact whole-tonne composition are recorded in the plan.

Louta and Benguerir pre-washing now follow the same pattern at site level: one
share mode and one catalogue per site, separate line enablement, and no
configured recipe assignment. A nonzero line/day campaign selects one of the
site's fixed matrices, while optimized mode uses the shared bounds.

Verification covered syntax, v4/v5-to-v6 migration, configuration validation,
UI persistence, existing optimized-blend behavior, and controlled solver
cases. In the MPH case, an outage made the A/B matrix infeasible and the solver
selected the B/C alternative, recording `B-C` with an exact 50/50 composition.
In the pre-washing case, two alternatives were available to the same line and
the daily shares matched one complete recipe rather than mixing recipes. The
focused blend/model/UI set passed **41 tests**, including the two new
multi-matrix solver tests. The software-contract verdict is **PASS**; matrix
values remain operating inputs that require plant approval.

## Version 1.9.0 semantic update — demand-driven MPH blending

Version 7 of the MPH policy removes prescribed result grade, construction
batch, ready hour, and fixed chevron-pass count. The shared adjustable input
floor defaults to 500 physical tonnes per selected grade per pile construction.
Optional activation, variable total up to physical capacity, exact rational
matrix fractions or bounded optimized shares, and at least two input grades
are explicit in `dynamic_blending.add_dynamic_pile`. A selected blend's actual
component chemistry is checked for each receiving profile it supplies through
`add_receiving_quality`; a nominal output label never replaces the mixture.

The former documentation incorrectly implied that all shared-policy versions
force a batch and constrain every reachable receiver. The canonical formulation,
manifest and code map now distinguish new v7 behavior from reproducible legacy
v5/v6 fixed-campaign semantics. They record integer-ratio feasibility, component
floor scope, end-of-stacking readiness, exact proposal carry, actual-state
freezing, and the limit of one new construction lot per pile per uniform solve.
Pass counts require explicit stacking traverse speed and geometry; no speed is
invented from the machine's repositioning parameter.

Focused execution/material-candidate verification:

`python -m pytest -q tests/test_dynamic_blend_execution.py tests/test_simulator_chevron_quality.py tests/test_review_blend_integrity.py`

Result: **29 passed**. New cases confirm accepted selected tonnes enter the
execution scenario without declaring physical completion; actual completed
stacking releases reclaim without a preset time hold; an outage leaves the
blend held; an accepted replan cannot overwrite the executed recipe; and washing
candidate expansion preserves one physical mixture per reclaim route.
Sequential execution also confirms that a 200 t lot can be completely
reclaimed before a 150 t lot with a different recipe/result identity is built
on the same pile. Snapshotting retains only the current lot's component totals
and does not recount old jobs. An incomplete first lot blocks the next lot,
including when both recipes match; completed but nonempty stock also blocks
the next construction and cannot be credited to its reclaim instruction.

Final integration checks (2026-09-20):

```text
python -m pytest -q tests/test_dynamic_blend_audit.py tests/test_dynamic_blend_schedule.py tests/test_dynamic_blend_helpers.py tests/test_dynamic_blend_execution.py tests/test_blend_policy_dataset.py
```

Result: **53 passed**. Independent adversarial checks include dry-solids versus
wet-mass quality, missing specifications, zero dry-solids exclusion, preserved
physical inputs after changing the floor, unused incompatible receivers,
reproducibility, and sequential 2,000 t / 6,000 t demand for distinct result
labels in both weekly and mixed-resolution plans. Fine-prefix jobs retain the
first lot's metadata rather than inheriting the last weekly lot.

```text
python -m pytest -q tests/test_dynamic_blend_audit.py tests/test_dynamic_blend_schedule.py tests/test_dynamic_blend_execution.py tests/test_dynamic_blend_helpers.py tests/test_chevron_stacking.py tests/test_optimized_blend_schedule.py tests/test_prewash_blending.py tests/test_simulator_chevron_quality.py tests/test_review_blend_integrity.py tests/test_weekly_solver_hardening.py tests/test_mixed_resolution_planning.py tests/test_blend_policy_dataset.py tests/test_analysis_tools.py tests/test_ux.py
```

Broader result: **162 passed, 2 failed** in 55.05 s. The two failures are outside
the blend path: `test_empty_state_when_no_plan` expects the text "No plan
generated", and `test_dashboard_appears_after_plan` expects navigation to stay
on Plan rather than Monitor after a solve. These assertions and the unrelated
overview/navigation behavior were not changed to make this suite green.
The blend UI tests passed, including persistence of a changed minimum and
removal of fixed grade/batch/ready-hour/pass controls.

`python scripts/sync_model_manifest.py` and the model-formulation skill's
`scripts/validate_model_package.py .` refreshed and verified the source
fingerprint and package structure. The canonical simulator entry point remains
on port 8505 with polling reload enabled; its health endpoint returned 200.

Verdict: **CONDITIONAL** for physical interpretation; the targeted software
contracts pass. Evidence uses synthetic routed fixtures, not independently
measured plant data or a new full-week S0 performance study. Spatial segregation,
reclaim-face evolution, traverse speed and real throughput calibration remain
unvalidated. The uniform solve represents one new blend per pile; drained
piles can be reused at later planning boundaries, but within-window repeated
turnover and longer lookahead are not introduced. Pre-washing retains its
separate daily percentage-based policy in this change.

A pre-existing reporting ambiguity remains: legacy `onspec_pct` returns 100
when no specification buckets exist; canonical on-spec mass KPIs correctly
report zero certified tonnes and an unavailable percentage in that case.
No new PDF publication was requested.
