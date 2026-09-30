# MPH stockyard planning model formulation

Package version: **1.15.0**
Verified against implementation: **2026-09-22**  
Model type: deterministic mixed-integer constraint optimization (OR-Tools
CP-SAT) with a minute-level execution overlay.

This document describes implemented behavior. Project evidence and scenario
assumptions remain governed by `ASSUMPTIONS.md`, the scenario registers, and
the source documents cited there. Washing values are provisional behavior-
calibration inputs, not plant-validated performance or an approved G10
specification.

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

### Mine arrivals and operating inventory

Status: `code-verified` (`mine_arrivals.refresh`,
`Simulator._execute_mine_arrivals_interval`). Configure > Mine arrivals defines
whole physical/as-handled tonnes by civil day and raw grade for Benguerir and
Louta. Generated `MINE-ARRIVAL:` exogenous flows have no internal donor and enter
the existing finite upstream buffers. Stacking/reclaim routes and capacities,
opening inventories, grade assays, moisture and yields are unchanged.

For daily quantity Q and fractional civil-day bucket boundaries a and b, the
prescribed bucket quantity is `round(Q*b) - round(Q*a)`. A full day conserves Q
exactly on hourly/subhourly grids; partial days are clipped, not rescaled.
Absolute offsets prevent rolling windows restarting the profile. Adjacent
equal-rate buckets may share one flow row. Typed washing and the native grid
are required; legacy single-grade and mixed-resolution combinations reject
enabled mine arrivals explicitly.

The UI enables a seven-day profile by default: approximately 24,000 t/day BEN
and 12,000 t/day LOU, with daily volume/composition variation and 4--6% RAW-MT.
These are editable **assumptions**, not measured production or validated
sustainable rates. The low-level `datasets.end_to_end` fixture remains legacy;
offline studies must explicitly apply the UI washing and mine-arrival overlays.

The active raw-arrival catalogue is `RAW-TBT`, `RAW-BT`,
`RAW-BT-HIGH-CD`, `RAW-BT-LOW-CD` and `RAW-MT`. The former
`RAW-BT-NOMINAL` duplicate is not a model grade. Legacy scenario and arrival
records migrate it to `RAW-BT`, adding its physical tonnes to any existing
`RAW-BT` quantity so supply mass is conserved.

`stock_policy=OPERATING` removes only recognized legacy exact closing-stock
defaults for BEN mine/raw, LOU ore and LOU product piles. Capacities,
conservation, soft safety-stock penalties, non-default operator commitments,
minimum stocks and zero-WIP closure remain. `EXACT_DAILY` restores missing
legacy targets. The stocked-route demonstration originally imposed these
equalities to require replenishment rather than serving everything from
opening stock (ASSUMPTIONS.md, Louta stocked-route policy). Weekly interpolation
makes opening-equals-final targets daily equalities; this is not a physical law.

Arrivals are prescribed, not selected by the optimizer. Planning overflow stays
explicit spill. Execution shares free buffer capacity proportionally between
simultaneous mine grades. `actual_mine_arrivals` and warning logs record
scheduled/accepted/rejected tonnes by grade and interval; rejected mass stays
outside the receiving boundary, with no hidden carryover. Execution can reject
an arrival that bucket-level planning expected to fit: compare accepted supply,
not only the configured daily total.

Pre-washing percentages still constrain **source-stockpile shares**, not
individual incoming raw-grade percentages. Mixed-grade sources retain their
actual material pools and treatment assays; their designated grade label is
not their full composition. External flows disable the label-only physical
seed, so the full typed feasibility fallback is used with all hard constraints.
This supply revision alone does not guarantee a successful weekly plan.

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
| Simple washing | 0.85 | 0.63 | `[+12%,-5%,-30%,-28%,-26%,+20%]` |
| Full washing | 0.65 | 1.04 | `[+23%,-20%,-64%,-50%,-27%,+20%]` |

For every known characteristic `p`:

`q_out[p] = q_raw[p] * (1 + change[p]/100)`.

These are relative changes, not percentage-point additions. Thus 15% moisture
with `+20%` becomes 18%. Missing assays remain missing.

Wet output mass is derived from dry-solids conservation and the configured dry
yield `eta`:

`wet_output / wet_input = (1 - M_raw/100) * eta / (1 - M_out/100)`.

The project-team-supplied, still non-approved G10 receiving intervals are BPL
`[62.5,100]`, Cd `[12,18]`, MgO `[0,0.8]`, SiO2 `[0,7.5]`, CO2 `[6,6.5]`,
and moisture `[0,25]`. They are hard
constraints for configured PAP destinations. G10 is a receiving specification,
not a physical stock grade. In the built-in calibration, the Benguerir normal
washing campaigns use `RAW-TBT` with BPL 54.10%, Cd 16 ppm, MgO 1.52%, SiO2
14.39%, CO2 8.32%, and the retained prior moisture of 12%. The separately
supplied 5% H2O value is excluded by instruction. Simple treatment produces
`[60.592,15.2,1.064,10.3608,6.1568,14.4]` and fails G10; Full produces
`[66.543,12.8,0.5472,7.195,6.0736,14.4]` and passes. The low-Cd campaign uses
`RAW-BT-LOW-CD`, which can pass with Simple washing. These mappings create mode
selectivity without adding a global treatment-share constraint. A configured
pre-washing blend point may separately restrict its allowed treatment modes.

PAP-1 and PAP-2 have an invariant demand contract: the destination default and
every explicit weekly demand bucket request `G10`. Demand presets may change
PAP quantity and timing, but cannot replace `G10` with a physical stream grade.
The contract remains active when treatment generation is disabled; that toggle
controls the availability of washing alternatives, not PAP acceptance. Source,
stock and treatment output identities remain physical grades, and `G10` is
assigned only as final receiving certification when the hard assay envelope is
satisfied. Non-G10 PAP campaign edits are rejected before solve.

### Pre-washing feed blending

`PRE_WASHING_BLENDING` version 4 stores `enabled_source_spaces` per site.
It exposes only explicit cross-stockpile routes that join at the modelled
common reclaim header. A dormant `BLEND-ALT` transfer becomes a candidate
only when its source belongs to an eligible blend at that physical stage.
Washing-line availability remains an independent equipment/routing setting.
Status: `code-verified` (`prewash_blending.enabled_source_spaces`,
`effective_process_stages`, `enabled_points`; optimizer candidate generation).

Let `E_b` be the enabled source stockpiles at site `b`. A fixed recipe is
eligible only if all positive-share input sources belong to `E_b`; disabling
an input excludes the entire recipe, never renormalizes its percentages.
Optimized bounds are filtered to `E_b`. Fewer than two usable sources or an
infeasible remaining share envelope means no active blending at that site.
Ordinary requested-grade washing remains available; a disabled pile cannot
use dormant blend-only cross-feeds. Recipes and bounds are retained for later
re-enablement. Status: `code-verified` (`eligible_matrix_catalog`,
`candidate_components`, `_has_effective_blend`).

For enabled blend point `b`, washing stage `l`, civil day `d`, and component
source `s`, define the selected input mass:

`F[b,l,d,s] = sum X[c,t]`

over process candidates whose stage is `l`, source is `s`, and bucket `t` is
in day `d`. With `F_total = sum_s F[b,l,d,s]`, the implemented bounds are:

`min_share[b,s] * F_total <= F[b,l,d,s] <= max_share[b,s] * F_total`.

Here `s` ranges only over effective blend-participating sources. Ordinary
nonparticipating feed can coexist at the same stage/day without belonging to
this denominator. Result interpretation must not aggregate all washing jobs
and then mistake that aggregate for the selected blend. Pre-washing selected
matrix IDs are not currently exported as a separate campaign result.
Status: `code-verified` (optimizer `stage_cis` and `source_spaces`).

Louta and Benguerir are selected and configured independently. Within one
site, the share mode and recipe catalogue govern enabled source stockpiles
across all physical washing stages; a matrix is not assigned to a line or
source stockpile. Legacy imported points without `enabled_source_spaces`
retain their earlier line-scoped semantics until explicitly converted. Each site has
one of two code-verified share modes:

- `MATRIX`: the operator may define several fixed recipes `k in K_b`. For a
  nonzero line/day campaign the model selects one recipe with binary
  `Z[b,l,d,k]`; the selected recipe enforces
  `F[b,l,d,s] = r[b,k,s] * F_total`, with `sum_s r[b,k,s] = 1`;
- `OPTIMIZED`: CP-SAT selects the source tonnes within the configured lower
  and upper share envelope.

The coefficients use an integer scale of 10,000. Positive minimum shares on
at least two sources prevent token-free pseudo-blends in optimized mode; a
fixed matrix requires at least two positive components. Positive campaign
flow requires exactly one catalogue selector; zero flow does not require one.
One treatment mode is selected for the entire line/day campaign; candidates
in other modes are zero. The selected component assays remain separate material pools through
the treatment transformation, so downstream quality and dry-solids yield use
the actual source and mode rather than a relabelled average.

This is a daily feed-blend approximation, not a prediction of spatial
stockpile-face homogenisation. The built-in commissioning policy selects
optimized shares with broad 5–95% limits and permits both Simple and Full
washing. Both Louta source piles (`110LG-OL1`, `110LG-OL2`) and both Benguerir
source piles (`BG-RAW-1`, `BG-RAW-2`) participate by default. These defaults are commissioning assumptions,
not approved plant matrices. The model still represents daily common-header
feed shares, not physical blend construction inside the source piles.

Important interpretation boundary: treated components remain separate material
pools and can be selectively withdrawn downstream. A G10-certified receipt
can therefore use a different composition than the whole daily feed campaign,
leaving poorer product in inventory. A physically inseparable blended-output
interpretation is not supported. Compare complete campaign assays, delivered
assays and residual component inventories before attributing treatment choice
to quality or economics. Status: `code-verified`; isolated regression evidence
is recorded with the 2026-09-20 blending-preset assessment.

The UI saves stockpile checkbox and pre-washing master-switch changes
immediately to the active scenario and to the scenario-keyed
`config/blending_preferences.json` overlay. It invalidates previous outputs
after an accepted change. The overlay stores only enablement, not recipes,
inventory, objectives or physical campaign state; a failed write rejects the
edit. Built-in fresh UI sessions restore these switches. Custom scenario
exports retain their own configuration and do not implicitly load an overlay.
Status: `code-verified` (`blend_preferences.save_blend_enablement`,
`apply_saved_blend_enablement`; `ui.common`; `ui.pages_data` callbacks).

### MPH humid-stockpile blend catalogue

`CHEVRON_STACKING` version 7 uses `quantity_mode=DEMAND_DRIVEN`. Its shared
configuration contains the matrix/optimized selection mode, catalogue or
share bounds, and `min_input_grade_t` (default **500 physical t**). The only
stockpile-specific policy input is enabling blending. Enabling a pile permits
construction; it does not force construction or a preset quantity, result grade,
completion hour, or number of stacking passes. Inventory, geometry, capacity
and physical progress remain specific to each pile. Status: `code-verified`.

All eight MPH pile permissions default to enabled, with global optimized
shares. `chevron_settings.enabled_spaces` retains configured permissions;
`active_spaces` excludes occupied, not-yet-started v7 piles. Those piles keep
their original inventory identity and ordinary handling until empty at a later
planning-window boundary. Enabling them neither clears stock nor relabels it.
Existing BUILDING/COMPLETE campaign safeguards remain active. A one-time
`BLENDING_DEFAULTS_REVISION=1` hot-session migration installs these defaults;
later operator edits are preserved. Pre-drying remains a UI placeholder, not
an implemented blending process. Status: `code-verified`.

For enabled pile `h`, let `A[h]` be optional construction activity, `Q[h]` its
selected total physical tonnes, `q[h,g]` the tonnes of input grade `g`, and
`U[h,g]` its binary use indicator. With physical capacity `C[h]` and adjustable
minimum `L`:

`0 <= Q[h] <= C[h] A[h]`, `sum_g q[h,g] = Q[h]`,
`L U[h,g] <= q[h,g] <= C[h] U[h,g]`, and
`sum_g U[h,g] >= 2 A[h]`.

Tonnes are integer; the implemented positive input floor is
`max(ceil(L),1)` per selected grade. This is the aggregate quantity contributed
by that grade to one pile construction, not a minimum per stack job or bucket.
No independent batch lower/upper target is configured. Physical storage,
inventory, routes, equipment, calendars, and receiving requirements still
bound feasible amounts. The new floor does not retroactively change a frozen
physical recipe or a fixed tactical recipe being refined on a finer grid.
Fresh optimized bounds are 0–100%; migration removes the exact inherited
5–95% defaults but preserves custom operator bounds. Status: `code-verified`.

In `MATRIX` mode the common catalogue contains recipes `k in K_MPH` with
fractions `r[k,g]`. Binary selectors obey `sum_k Z[h,k] = A[h]`. When
`Z[h,k]=1`, the model enforces `q[h,g] = r[k,g] Q[h]` using rational integer
equalities. Thus the matrix scales with chosen throughput, and integer tonnes
can restrict feasible totals to multiples of its denominator. Every matrix is
eligible at every enabled pile; none is permanently assigned. In `OPTIMIZED`
mode `l[g] Q[h] <= q[h,g] <= u[g] Q[h]` uses the shared envelope. A positive
lower percentage requires that component when the pile is active; optional
components require a zero lower percentage. Status: `code-verified`.

For each grade, selected tonnes equal carried construction plus planned stack
flows for a newly selected construction lot. Let `B[h,t]` be cumulative
stacked tonnes at the start of bucket `t`. The readiness literal becomes true
when `B[h,t]=Q[h]` for an active pile; component upper bounds ensure all
selected components are complete. Reclaim in bucket `t` requires this literal.
The first permissible reclaim bucket therefore follows actual planned
construction completion; there is no operator-entered ready hour. During
simulation an outage or shortfall keeps reclaim held until measured
grade-specific tonnes complete the selected recipe. A planned timestamp alone
cannot complete a pile. Status: `code-verified`.

Each used receiving destination/specification profile activates constraints
on the pile's actual component-weighted chemistry (dry-solids weighting, wet
mass for moisture). Unused destinations impose no quality condition on that
pile. Required missing assays exclude the affected component from a blend
serving that receiver. A blend is one physical mixture, not selectively
reclaimable constituent pools; a requested output label never substitutes its
nominal assay for the calculated mixture. Reported result grades identify
actually served demand, while unresolved material may retain a neutral
`BLEND-<stockpile>` identity. Status: `code-verified`.

Selected composition, total, matrix, result label and completion timing are
carried in plan records. On first actual stacking, the exact physical recipe
and selected quantity are frozen for that construction lot. Replanning must
finish those physical commitments before reclaiming them; unexecuted proposals
may be reconsidered. The current uniform solve represents at most one new
construction lot per enabled pile, and does not support simultaneous stacking
into and reclaiming from an incomplete blend. At a later solve boundary an
enabled, completed/depleted pile with zero physical inventory may reopen for
a new lot. Each job carries its lot identity and exact selected metadata;
merged weekly results retain ordered campaign history. Execution activates the
job's lot only after the previous lot is physically depleted, and keeps the
old job-accounting ledger so snapshots cannot count earlier stacking twice.
An outage leaving a prior lot incomplete blocks the next lot even if both
recipes are identical. Status: `code-verified`.

Chevron traversal counts are derived metadata, not a fixed recipe parameter.
With explicit stacking traverse speed `v` (m/min), supported traverse length
`ell` (m), stacking rate `f` (t/h), and scheduled mass `M` (t),
equivalent passes are `N = (60 M/f) v/ell`. Split execution windows are summed
using their validated flow durations; otherwise job mass/rate supplies duration.
Job compatibility fields round the equivalent pass count upward; operator
views show estimated equivalent traversals, not repeatable cycles. Missing speed or geometry leaves the
estimate unavailable; ordinary repositioning speed is not silently treated
as stacking speed. This does not model individual deposited layers or change
the optimization objective. Formula status: `derived`; implementation status:
`code-verified`.

Legacy imported v5/v6 policies retain their fixed quantity, output identity,
and readiness semantics for reproducibility. Their construction-quality
constraints may cover every reachable receiver even without demand in the
construction window. New/default idle configuration migrates to v7; a physical
lot already under construction retains its commitment. The existing component
use penalty remains a lowest-order tie-break after service and quality.

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

conceptually `sum[c,t] cost_per_product_dry_t[c] * product_dry_mass[c,t]`.
Here `c` is the treatment candidate, `t` the bucket, unit cost is dollars per
product-dry tonne, and product dry mass is tonnes. The implemented integer
objective actually uses a proxy: input wet tonnes times a per-input-tonne
coefficient obtained from input dry fraction, nominal dry yield and unit cost,
rounded to 0.001 dollars. It therefore precedes whole-tonne output rounding.
The planned cost KPI currently copies that proxy; the treatment ledger and
simulated cost use actual recovered dry output. These bases can differ and
must be labelled separately when comparing small cost changes.

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

For the physical washing seed, treatment selection is quality-aware: among
modes whose transformed assay satisfies the configured G10 envelope it chooses
the highest-yield mode. It falls back to the highest-yield available mode only
when none is individually G10-capable, leaving blending and the final CP-SAT
model authoritative. The label-only precursor cannot create certified G10 or
authoritatively schedule a chevron campaign, so PAP demand and chevron
construction are disabled only on that precursor copy. Both remain unchanged
in the full typed model. Managed dispatch requirements are clamped at zero
while the seed is constructed, so repeated dispatches cannot turn a satisfied
requirement negative and incorrectly suppress an available opening pool.

The physical mapper replays typed exogenous inflow/outflow after commanded
work in each bucket, matching the authoritative no-passthrough balance. Any
bounded external-arrival overflow is allocated across typed pools rather than
silently forbidden. Because one label-only transfer can cover materials with
different moisture-dependent wet-feed ceilings, its selected-mode capacity
and yield use the conservative envelope across every reachable material.
Intermediate pre-washing buffers may differ by a few integer tonnes when the
precursor rounds one aggregate stream but the typed model rounds each assay
pool. Such reconciliation is permitted only for process-chain variables that
the typed bootstrap deliberately leaves free; mismatches on fixed portions of
the seed still reject the mapping.

Before the richer physical attempt, the solver verifies a no-command emergency
incumbent against the **same hard model** by fixing all commanded `X` flows to
zero and clearing only the objective. This is possible only where configured
demand, planning-stage, and planning-corridor shortfalls and bounded external
overflow already provide explicit feasibility escapes; fixed work or hard
inventory commitments can still make it infeasible. The richer mapper then
gets only a short bounded attempt when this incumbent exists, preserving most
of the window budget for the authoritative objective search.

If the mapped restrictions are inconsistent, they are never interpreted as
proof that the full model is infeasible: a repairable-hint attempt or the
verified no-command incumbent is used instead. The authoritative search gets
that solution as a hint and may improve every decision. If it times out, the
verified incumbent is returned as `FEASIBLE`, with all unmet demand, planning
shortfall and overflow still visible. This guarantees usability, not service
quality or optimality; an emergency fallback with large slack is evidence of
insufficient search/calibration and must not be presented as an operationally
good plan. The normal extraction and minute acceptance gates remain active.
`WASHING_BOOTSTRAP` diagnostics identify the path taken.

The optional consolidation pass cannot invalidate a valid primary plan: an
exception or rejected candidate retains the incumbent. An `OPTIMAL` secondary
pass is not reinterpreted as proof of primary global optimality.

### State-coupled weekly rolling solve

For a multi-day native-grid horizon, civil-day windows are built from actual
dates. Legacy fixed-deadline opening windows are coalesced through the latest
unresolved optimized blend readiness boundary. Demand-driven v7 blending has
no prescribed deadline and does not request this coalescing. Its fresh optional
construction decision uses the current uniform solve window. Each accepted
window carries forward physical/grade inventory,
pending delayed material, belt parcels, machine activity group, continuous-feed
gap state, open-gap duration, and resolved blend recipes with selected totals,
matrix identities, result labels and calculated completion times. These are
accepted planned state at a rolling boundary; actual execution is tracked
separately and freezes a physical lot only after positive measured stacking.

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

- Washing coefficients, yields, G10 limits, and residence defaults are a
  provisional behavior calibration, not plant validation. The intended
  TBT-to-Full and low-Cd-to-Simple selectivity is model-verified; the numeric
  process response still requires laboratory or plant data.
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
