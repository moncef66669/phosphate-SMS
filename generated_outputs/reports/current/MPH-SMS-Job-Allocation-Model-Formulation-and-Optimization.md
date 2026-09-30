# MPH — Model Formulation & Optimization Algorithms
### Phosphate Value-Chain Job-Allocation Simulator

**Edition:** Code-verified split report · **Date:** 17 September 2026 · **Canonical source:** `model_knowledge/MODEL_FORMULATION.md`

> This report is generated from the versioned model-knowledge package. Run-specific configuration and the model-context snapshot remain authoritative for interpreting an individual solve.

Package version: **1.2.0**  
Verified against implementation: **2026-09-17**  
Model type: deterministic mixed-integer constraint optimization (OR-Tools
CP-SAT) with a minute-level execution overlay.

This document describes implemented behavior. Project evidence and scenario
assumptions remain governed by `ASSUMPTIONS.md`, the scenario registers, and
the source documents cited there. Synthetic washing values are commissioning
inputs, not plant-validated performance or an approved G10 specification.

## Scope and decision boundary

The planner selects whole-tonne material movements through a directed
stockyard/process network over a finite time horizon. It chooses eligible
machine/space/destination/route/material/treatment candidates, their tonnes by
time bucket, and associated activity states. It accounts for inventories,
route and machine capacity, calendars, fixed jobs, material transformations,
destination demand, quality, safety stock, continuity, and operating events.

The solver does not predict geology, spatial reclaim faces, breakdowns,
measured treatment performance, market value, or operator behavior. A
minute-level overlay places the accepted bucket decisions in physical time and
can reject an otherwise feasible solver incumbent if positioning or execution
validation fails; it does not change planned mass.

## Evidence and bases

Definitions labelled `code-verified` were traced to the declared source set in
`model_manifest.json`. Scenario inputs can override defaults, so an actual run
must be interpreted from its embedded scenario and model-context snapshots.

- Solver mass is integer **physical/as-handled tonnes** unless explicitly
  identified as dry solids.
- `dt` is the native solver bucket duration in hours.
- Chemistry other than moisture is a dry-solids concentration. Moisture is a
  wet-basis percentage.
- Rates are t/h; a bucket mass is rate multiplied by `dt`.
- Quality slack has tonne-quality-unit basis after internal integer scaling.
- Costs for washing are dollars per product dry-solids tonne (TSM) in the
  implemented expression.

## Sets and indices

| Symbol | Meaning |
|---|---|
| `c in C` | generated movement candidate, including route/material/treatment |
| `t in T` | native planning bucket |
| `g in G` | activity group sharing one machine/space/destination state |
| `m in M` | machine |
| `s in S` | stock or process space |
| `d in D` | destination/demand stream |
| `l in L` | network link |
| `r in R` | grade-resolved material pool |
| `p in P` | quality characteristic |

Candidates absent after validation, reachability, lifecycle, material-class,
and quality-data filters cannot be selected. The candidate list is therefore
part of the causal explanation of every result.

## Parameters

Material parameters include initial inventory and capacity, safety targets,
machine and link rates, calendars, demand and priority by bucket, quality
assays/specifications, transport delays, process yields, residence/dependency
delays, fixed jobs, and scenario configuration.

Important configurable objective/scheduling parameters are:

- `OBJECTIVE_MODE`: `service_first` or `weighted`;
- `MIN_JOB_VOLUME_T`: hard minimum on a complete contiguous job, default 0;
- `SERVICE_DEGRADATION_T`: allowed deterioration of qualified service during
  protected refinement, default 0;
- `w1_unmet`, `w2_offspec`, `w4_giveaway`, `w5_moves`, `w6_safety`, and
  `w7_utilisation`; `w3_process_cost` is present in the data model but has no
  general process-cost curve implementation;
- continuity, equipment-cycle, parcel, energy, and tie-breaker coefficients.

## Variables

For candidate `c` and bucket `t`:

- `X[c,t]` is selected input physical mass in whole tonnes.
- `Y[c,t]` is output mass for yield-changing process candidates; for ordinary
  transfers it aliases input behavior.
- `A[g,t]` is one when activity group `g` is assigned in bucket `t`.
- `I[s,t]` is physical inventory at a bucket boundary.
- `I_material[s,r,t]` is material-pool inventory when washing is enabled; the
  pool totals equal physical inventory and do not duplicate capacity.
- `U[d,t]` is unmet-demand mass. This explicit slack makes ordinary demand
  shortage costly rather than automatically infeasible.
- `QSlack` represents soft quality violation; hard quality bounds do not get
  this relaxation.
- `SafetyShort[s,t]` is inventory below the configured safety target.
- `Gap[d,t]` and `Episode[d,t]` represent continuous-feed gap state and a new
  gap episode.
- `Start[m,t]`, `Stop[m,t]`, `Changeover[m,t]`, and `Hold[m,t]` encode machine
  state transitions. A hold keeps the machine on while moving zero material.
- `ShortParcel[g,t]` marks a permitted below-policy parcel and is penalized.

## Core balances and transformations

### Physical inventory

For every space and bucket, the implemented balance is conceptually:

`I[s,t+1] = I[s,t] + incoming[s,t] + exogenous_in[s,t]
             - outgoing[s,t] - exogenous_out[s,t] - spill[s,t]`.

Opening inventory limits reclaim. Inventory is bounded by physical capacity;
route, machine, and conflict capacities independently bound movement.

### Demand

For ordinary delivery demand:

`Delivered[d,t] + U[d,t] = Demand[d,t]`.

Process-feed demand is treated as an exact daily grade campaign total plus one
explicit unmet variable for the campaign, allowing the optimizer to choose its
operating buckets.

### Quality

Delivered quality is mass-weighted. Moisture uses wet mass; other chemistry is
weighted on a dry-solids basis. Hard bounds are enforced. Soft bounds introduce
quality slack priced by the objective. Unknown required assays are not silently
treated as zero.

The quality envelope may be selected by the demand campaign. A physical grade
keeps the destination's fallback `Destination.spec`; a grade marked
`is_specification` replaces that fallback with its `Grade.receiving_spec` for
the affected buckets. A specification grade is therefore an acceptance
contract, not stock identity: candidate eligibility does not require the
source label to equal the specification label. Quality windows are split at a
campaign-grade boundary and are never averaged across two different contracts.

### Gantour train-export receiving grades

Four hard receiving profiles are available only at `BG-TRAIN-PROD`:

| Grade | Executable hard intervals |
|---|---|
| BG-22 | BPL 68–70%; CO2 5–6.5%; SiO2 ≤4.5%; H2O ≤3.5%; MgO 0.45–0.65%; Cd ≤7 ppm |
| BG10 | BPL 65–67.5%; CO2 5–6.5%; SiO2 3.5–7%; H2O ≤3.5%; MgO 0.45–0.65%; Cd 7–9 ppm |
| BG11 | BPL 67–69%; total SiO2 5–10%; reactive SiO2 2.8–3.6%; F 3.2–4.2% |
| BG AD | BPL 64–65%; CO2 ≤5.5%; SiO2 6–8.5%; MgO ≤0.53%; Cd 8–12 ppm; H2O ≤3%; P2O5 S,C 8.79–8.93% |

`H2O` maps to `moisture`, `SiO2 T` to `SiO2`, `SiO2 R` to
`SiO2_R`, and `P2O5 S,C` to the distinct `P2O5_SC` assay. The P2O5
ranges reported with BG-22 and BG10 are retained as source metadata while BPL
is the executable phosphate-content constraint; enforcing both would duplicate
an equivalent measurement. The source's strict `<` statements are preserved
verbatim in grade provenance, but the current inclusive `SpecRange.pmax`
contract implements their numeric boundaries without an invented epsilon.

BG11 and BG AD can be served only when every required distinct assay is known.
Missing `SiO2_R`, `F`, or `P2O5_SC` excludes the affected material from that
campaign and leaves demand explicitly unmet. The default S0 train grade remains
unchanged; adding these profiles does not silently activate an export order.

### Washing and G10

When `WASHING_TREATMENTS.enabled` is true, the planner creates grade-resolved
material pools and two available treatment alternatives:

| Option | Dry-solids yield | Cost ($/product dry t) | Relative changes `[BPL,Cd,MgO,SiO2,CO2,moisture]` |
|---|---:|---:|---|
| Simple washing | 0.85 | 0.63 | `[+17%,-20%,-30%,-60%,-50%,+20%]` |
| Full washing | 0.65 | 1.04 | `[+45%,-60%,-60%,-80%,-70%,+20%]` |

For every known characteristic `p`:

`q_out[p] = q_raw[p] * (1 + change[p]/100)`.

These are relative changes, not percentage-point additions. Thus 15% moisture
with `+20%` becomes 18%. Missing assays remain missing.

Wet output mass is derived from dry-solids conservation and the configured dry
yield `eta`:

`wet_output / wet_input = (1 - M_raw/100) * eta / (1 - M_out/100)`.

The synthetic G10 receiving intervals are BPL `[65,90]`, Cd `[0,18]`, MgO
`[0,1.2]`, SiO2 `[0,6]`, CO2 `[0,4.5]`, and moisture `[0,20]`. They are hard
constraints for configured PAP destinations. G10 is a receiving specification,
not a physical stock grade.

## Objective

The model minimizes. The effective objective depends on `OBJECTIVE_MODE`.

### `service_first` mode

The primary solve minimizes:

`(B + 1) * Emergency + QualifiedServiceShortfall`,

where `B` is a finite upper bound on all priority-weighted final-product demand
in the window. Therefore one unit of emergency slack outweighs all possible
qualified-service improvement in that window. `Emergency` comprises spill,
surge/unstacked slack, and process-stage target shortfall.

Accepted secondary passes explicitly protect every earlier incumbent ceiling.
With washing enabled, the implemented order is:

1. qualified service shortfall (within `SERVICE_DEGRADATION_T`);
2. unqualified/off-spec dispatch;
3. raw washing-feed dry tonnes;
4. washing treatment cost;
5. continuous-feed gap minutes and episode starts;
6. equipment starts, stops, changeovers, and holds;
7. remaining preferences (soft quality, safety, parcel, energy, flow/activity
   tie-breakers, giveaway, utilisation, and other configured terms).

Later passes cannot buy an improvement by degrading a protected earlier tier.
If a pass times out, fails, or is rejected by minute/quality acceptance, the
last accepted incumbent is retained. A `FEASIBLE` pass is not proof that its
tier is optimal.

The raw-feed term is total washing input dry mass. The treatment-cost term is:

`sum[c,t] cost_per_product_dry_t[c] * product_dry_mass[c,t]`.

Consequently, when both treatments meet G10 and face the same upstream and
capacity conditions, simple washing is structurally preferred: its 0.85 dry
yield needs less raw dry feed than 0.65, and its unit cost 0.63 is also below
1.04. Full washing becomes rational only when its stronger assay improvement
is needed for qualification or another constraint changes the comparison.

### `weighted` mode

The legacy mode minimizes one scaled sum. Major terms include
priority-weighted unmet demand (`w1`), hard/soft quality and blend slack (`w2`),
BPL giveaway (`w4`), equipment events (`w5`), safety-stock deficit-hours
(`w6`), and optional utilisation reward (`w7`), plus emergency, stage, parcel,
continuity, energy, washing-cost, and tie-breaker terms. Raw coefficients alone
are not comparable because the terms have different units and integer scaling.
`w1` is a demand-first heuristic, not a lexicographic guarantee.

## Constraints

The principal implemented groups are:

- candidate eligibility, reachability, lifecycle, material class, and assays;
- whole-tonne flow linked to activity and rate envelopes;
- at most one activity group per machine/bucket, calendars and fixed jobs;
- link, shared-capacity, corridor-confluence, and conflict-group capacity;
- physical and grade-resolved inventory conservation;
- process yield, residence/dependency delay, maturity, stage targets, and
  terminal-inventory/corridor targets;
- demand equalities with explicit unmet slack;
- hard or soft mass-weighted quality limits and G10 certification;
- campaign-selected train-export specifications, destination eligibility, and
  explicit rejection of missing required assays;
- continuous-feed gap definition and optional hard maximum gap;
- soft safety-stock shortfall;
- same-pile stacking/reclaim policy and positioning reservations;
- optional hard minimum volume on a complete contiguous job;
- optional parcel floors with a priced short-parcel exception.

`MIN_JOB_VOLUME_T` differs from the parcel floor. The former is a hard
whole-job constraint and may increase unmet demand or infeasibility. The latter
allows an explicit penalized exception. A default minimum job volume of zero
does not prevent one-tonne allocations.

## Optimization algorithms

The planner has one public entry point and three execution strategies. These
procedures are part of the model semantics because they determine the horizon
seen by each CP-SAT solve, which state is carried forward, what is protected in
later passes, and which feasible fallback can be returned.

### Entry-point routing

`solve_plan` validates washing settings and initial rolling state, prepares any
optimized blend design, and then dispatches as follows:

1. reject washing combined with mixed resolution because grade-resolved
   washing currently requires the native uniform or weekly grid;
2. use `solve_mixed_resolution_plan` when mixed resolution is enabled;
3. otherwise use `solve_weekly_rolling_plan` when weekly rolling is enabled and
   the horizon exceeds 24 hours;
4. otherwise use `_solve_uniform_plan` once on the configured native grid.

Input/model errors return an explicit `ERROR` result. A successful result is
augmented with the authoritative optimized-blend campaign records.

### Uniform CP-SAT solve

The uniform algorithm performs these ordered stages:

1. validate the grid, objective weights, initial state, washing configuration,
   and configured policy consistency;
2. prepare grade/material pools and generate only reachable, eligible movement
   candidates with their routes, transformations, and assays;
3. build integer flow, activity, inventory, demand/slack, quality, continuity,
   operating-event, and policy variables and constraints;
4. add deterministic hints or a bounded feasibility seed where configured;
5. solve the primary CP-SAT objective with one worker, the configured seed and
   deterministic-time budget, plus a wall-clock hang guard;
6. extract a candidate minute schedule and reject or repair an incumbent whose
   execution windows fail the minute-placement contracts;
7. in `service_first` mode, run the protected secondary objective levels in the
   exact order stated above, retaining the last accepted incumbent after any
   timeout, failure, or rejection;
8. when enabled, attempt parcel consolidation while fixing route/grade totals,
   service, terminal inventories, slacks, and protected objective quantities;
9. extract jobs, material flows and terminal state, compute reconciliation and
   objective evidence, and return the accepted incumbent.

The optional consolidation pass cannot invalidate a valid primary plan: an
exception or rejected candidate retains the incumbent. An `OPTIMAL` secondary
pass is not reinterpreted as proof of primary global optimality.

### State-coupled weekly rolling solve

For a multi-day native-grid horizon, civil-day windows are built from actual
dates. Opening windows are coalesced through the latest unresolved optimized
blend readiness boundary so a blend recipe and its construction schedule are
chosen jointly. Each accepted window carries forward physical/grade inventory,
pending delayed material, belt parcels, machine activity group, continuous-feed
gap state, open-gap duration, and resolved blend recipes.

Window target profiles and terminal targets are allocated across the remaining
horizon. Controlled dispatches that would arrive beyond an intermediate window
are banned from that window. Each window calls the uniform algorithm with a
bounded share of deterministic and wall time. The procedure stops at the first
non-`OPTIMAL`/`FEASIBLE` window or exhausted wall budget, then
`_merge_weekly_windows` returns either the coupled merged plan or an explicitly
partial/failed weekly result. Optimality is therefore a window-level statement,
not a certificate for the entire seven-day problem solved monolithically.

### Mixed-resolution solve

The mixed algorithm first solves the complete tactical horizon on the native
grid (using weekly rolling when configured). A feasible tactical result defines
the near-term boundary inventory and blend composition. The near-term prefix is
then resampled to the fine grid, tied to that boundary, solved again with the
uniform algorithm, and merged back with the tactical tail using canonical
minute bounds and the replan/commit rules.

If tactical solving fails, that status is returned. If resampling, wall budget,
or the fine-prefix solve fails after a feasible tactical incumbent exists, the
tactical plan is retained and labelled `cp-sat-mixed-fallback`. Washing with
mixed resolution is currently rejected before this procedure starts.

### Incumbent acceptance and termination

A solver incumbent is usable only when CP-SAT reports `OPTIMAL` or `FEASIBLE`
and the execution acceptance callback validates minute placement; quality-aware
secondary levels also apply their quality acceptance callback. Each bounded
refinement records status, before/after value, and acceptance. Hard-wall
expiration never converts absence of an incumbent into infeasibility, and an
accepted earlier incumbent is retained whenever an optional later stage fails.

## Solver semantics

CP-SAT uses one worker and a fixed seed. The configured solve budget is applied
as deterministic time, with a wall-clock guard. Integer scaling and rounding
are part of the implemented model. Status meanings:

- `OPTIMAL`: optimal only for the objective scope reported by the run;
- `FEASIBLE`: every hard constraint checked by the solver is satisfied, but the
  objective may be improvable and demand can remain unmet;
- `INFEASIBLE`: no solution exists for the built hard model;
- `UNKNOWN`: no usable incumbent/certificate was obtained within the search
  conditions; it is not proof of infeasibility.

In `service_first`, the reported optimality scope can be limited to primary
emergency and qualified service. Each later pass records before/after, status,
and whether it was accepted.

## Post-processing and displayed jobs

Solver bucket flows are extracted into allocation jobs. The minute execution
overlay places jobs around positioning, route, and occupancy constraints,
labels holds, and derives physical-run identity. One allocation job can contain
multiple physical runs; one physical run can span several allocation jobs.

The Gantt chart renders extracted execution windows. Very thin bars therefore
can represent a real low-tonnage/short-duration selected slice, not merely a
plotting defect. The evidence keys `below_parcel_floor_slices`,
`smallest_allocation_slice_t`, rate credibility, scheduled/executed transitions,
and the reconciliation residual distinguish optimization fragmentation from
display artifacts.

## KPIs

- `service_pct = 100 * credited_delivery / requested_demand`. Ordinary demand
  is credited by due bucket; process-feed campaigns are credited by daily
  grade-campaign totals.
- `unmet_t = requested_demand - credited_delivery`.
- `priority_weighted_service_pct` applies configured demand priority to both
  requested and credited mass.
- `final_product_service_pct` excludes process-feed campaign demand.
- `onspec_pct` is the share of destination/bucket observations with positive
  delivered mass that satisfy every configured quality limit. Zero-delivery
  buckets are service gaps, not quality observations.
- `on_spec_mass_pct` is on-spec final-product delivered mass divided by
  quality-evaluated final-product mass; unknown-quality mass is separate.
- `washing_<mode>_actual_dry_yield_pct = 100 * product_dry / feed_dry`.
- `washing_treatment_cost = sum(product_dry_t * cost_per_tsm_t)`.
- `safety_shortfall_t_h` integrates tonnes below safety stock over time.
- machine utilisation counts active native buckets; route utilisation is
  average transported rate divided by link capacity over the evaluated horizon.
- schedule-fragmentation KPIs separately report planned bucket transitions and
  executed minute transitions. `transition_reconciliation_residual` must be 0.

Every KPI must be read with its run-specific evaluation horizon, basis, and
planned-versus-actual status.

## Limitations and interpretation boundaries

- Washing coefficients, yields, G10 limits, and residence defaults are
  synthetic demo assumptions, not plant validation.
- Treatment costs currently omit transport, energy, water, reagent, disposal,
  and capital effects unless separately represented.
- Grade-resolved material pools approximate selective/campaign withdrawal; they
  do not model pile-face geometry or layer progression.
- Unmet slack prevents demand alone from proving infeasibility. Other hard
  constraints, fixed jobs, calendars, maximum-gap rules, or minimum job volume
  can still make the model infeasible.
- Candidate exports cover generated alternatives only. A candidate marked
  unused is not proven force-feasible without an isolated forced-alternative
  solve.
- Reported constraint residuals are diagnostic recomputations, not a complete
  export of every internal CP-SAT constraint.
- Weighted-objective values mix scaled terms and are not physical cost.
- A model-consistent plan is not evidence of physical or commercial validity
  where inputs are assumed, missing, or stale.
