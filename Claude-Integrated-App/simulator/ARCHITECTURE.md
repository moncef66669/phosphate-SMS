# Architecture — Phosphate Value-Chain Job-Allocation Simulator MVP

Functional baseline: project report **Rev D (13-Jul-2026)**. Domain A is a
**three-site system**: Louta and Benguerir mine stockyards (source yards,
peer controlled sites with their own SMS) feed the MPH M16 receiving/
blending/buffer hub through dedicated corridors C1/C2; the engine plans all
three yards plus corridor transport delay and in-transit material in one
coordinated plan (scenario S3). S2 exercises the M16 hub alone with
simulated corridor arrivals; S1 is Domain B (M17 sulphur).

## Layering (per the build specification)

```
 ┌──────────────────────────── ui/ (8. presentation) ─────────────────────────────┐
 │ app.py: sidebar nav · status bar · issue drawer · 16 pages                     │
 │ pages_overview │ pages_data │ pages_planning │ pages_meta │ theme │ common     │
 └───────┬──────────────────────────────────────────────────────────────┬────────┘
         │ Scenario / PlanResult / Simulator objects (no engine logic in UI)
 ┌───────▼──────────────────────── mph_sim/ (engine) ─────────────────────────────┐
 │ 1 domain.py      dataclasses + Evidence tags (planning digital twin)           │
 │ 2 validation.py  Scenario -> [Issue(severity, code, entity, message)]          │
 │ 3 routes.py      RouteGraph: nodes, links, paths, conflict/shared groups       │
 │ 4 optimizer.py   solve_plan(Scenario, SolveOptions) -> PlanResult   [CP-SAT]   │
 │ 5 simulator.py   Simulator: step()/replan(), events, JobUpdate feedback        │
 │ 6 kpis.py        compute_kpis(Scenario, PlanResult, Simulator?) -> dict        │
 │ 7 persistence.py JSON snapshots · table CSV/XLSX · SQLite RunStore             │
 │ 8 inventory_snapshot.py validated, atomic file/live planning-t0 ingestion      │
 │ 9 adapters.py    AspAdapter (abc) · SimulatedAspAdapter · RabbitMqAspAdapter*  │
 │ 9 integration.py three-SMS plan batches + separate live-inventory registry      │
 │ 9 rabbitmq_inventory.py read-only SpaceValue connector**                       │
 │   datasets.py    S1/S2/S3 demonstration scenarios (evidence-tagged)            │
 └─────────────────────────────────────────────────────────────────────────────────┘
 * plan submission / JobUpdated stub only — raises NotImplementedError
 ** implemented and tested with injected transports; production profile and
    OCP endpoints are uncommissioned pending the per-site RabbitMQ ICD/config
```

## Process mimic (`ui/hmi_symbols.py` · `ui/layout_geometry.py` · `ui/process_mimic.py` + page 1)

Pure pipeline: `build_process_mimic_state(sc, plan, sim, basis, bucket)`
returns an immutable `MimicState` (wraps the Stockyard-Monitor view-model;
adds per-link `FlowVM` flows from `sim.link_load`/plan job paths, active
`JobVM`s, `MachinePosVM` rail positions and outage windows from the
simulator's additive `down_series`/`link_down_series`).
`process_mimic_figure(state, geometry, options)` renders one
`SiteGeometry` — geometry is CONFIGURATION (`layout_geometry.py`: pile
lanes, rail polylines, conveyor polylines with bound `link_ids`, transfer
nodes, SMS scope zones, tag chips, NOT-TO-SCALE stamps, evidence flags,
`planning_enabled`) and never lives in drawing code. `hmi_symbols.py` holds
the reusable vector symbols (stacker, bucket-wheel reclaimer, stock lane,
double-line conveyor with chevrons, transfer tower, clamshell gate,
diverter, sampler/scale/analyser, junction, corridor interface). Drawing
never mutates scenario/plan/simulation; display-only assets (102MG mine
storage + stackers, Benguerir raw stage + optional B02 machines, beneficiation stages) are
never solver resources, and no 102MG → C1 connection is drawn. The mimic is
a planning/monitoring view — NOT a control or safety HMI; it issues no
commands.

## Three-SMS integration (`mph_sim/integration.py` + page 11)

Louta, Benguerir and MPH run **identical Innomotics/YardMaster SMS**
solutions (user-confirmed) as three independent instances. The layer uses
ONE adapter implementation with three independently configured instances
(`MultiSmsAdapterRegistry`); `AspPlanItem` stays faithful to the common
vendor telegram schema and SMS ownership lives only in the `SmsPlanBatch`
envelope. `partition_jobs_by_sms` assigns every job to exactly one SMS via
`Job.machine → Machine.site` (cross-checked against the pile's site;
mismatches/unknowns go to a visible UNMAPPED error queue). Per-SMS
validation runs the eight-rule gate on `scenario_site_projection(sc, site)`
so FIFO, spatial-conflict, inventory and calendar checks never cross sites.
The console (page 11, "Three-SMS Integration Console") shows fleet cards,
per-site job queues/lifecycles and four feedback previews per instance —
plan-item evaluation and job lifecycle remain labelled offline-simulated.

Read-only inventory is a separate capability. `MultiSmsInventoryRegistry`
creates at most one immutable `RabbitMqInventoryConnector` per loaded site
from a secret-free schema-1.0 profile. The connector uses an optional Pika
transport and an ICD-configured JSON codec; no exchange, routing key or wire
field is hard-coded from the functional FRS. Credentials remain in protected
AMQP URL environment variables and never enter a dataclass, scenario, UI
field or export. The page-11 flow is:

```
load config -> readiness -> endpoint probe -> fetch all loaded sites
-> strict decode/site/freshness/skew validation -> read-only preview
-> explicit page-3 apply
```

The fetch uses request-scoped connections, publisher confirms, a unique
exclusive auto-delete reply queue, AMQP and optional body correlation checks,
one bounded deadline, response limits and timezone-aware server capture
times. All sites are fetched and validated before one snapshot is exposed;
there is no partial mutation or simulated fallback. The accepted global
planning `t0` is the latest site capture within the configured skew limit,
with every site's timestamp and the connector-config revision retained in
snapshot provenance.

The optimiser and simulator never import Streamlit; the UI never builds
constraints. `ui/common.py` is the only bridge (session-state plumbing) and
owns the console state machine: the navigation model (Configure, Plan and
Monitor plus Advanced over 16 pages, `NAV_GROUPS`), scenario auto-load
(selector change → load → clear plan/sim), and the solve-request flow
(`request_solve` duplicate guard →
`run_plan` staged via `solve_plan(progress_cb=…)`, stages: validate → build
model → warm start → CP-SAT → results; plan replaced only on success).
`progress_cb` is a pure notification hook — it cannot affect the model or the
deterministic result.

## Planning model (optimizer.py)

Bucketised MILP on OR-Tools CP-SAT implementing report §15.1–15.4:

- **Variables**: integer mass flows `x[cand, t]` (tonnes/bucket) per
  (machine, space, destination-or-corridor, path) candidate; one activity
  boolean per (machine, space, destination) **group** — a machine works one
  job at a time but its stream may split across parallel route legs
  (e.g. the two H01–H04 conveyors to PAP). Blend fractions are derived
  after solving (`f = x / delivered`), never independent variables.
- **Constraints**: inventory mass balance with Δt + initial/terminal
  conditions; demand slack `delivered + unmet ≥ demand` (dominant w₁ — the
  model cannot be made infeasible by demand); per-bucket mass-weighted
  quality (hard or soft per spec); corridor transport delay with initial
  belt profile (eq. 5); machine exclusivity, reachability, work calendars,
  travel/setup gaps between far spaces, min-stable rates; route-link
  capacities, shared groups (min-capacity), conflict groups (one active
  link per bucket); stockpile capacity with spill slack; safety stock
  (soft, w₆); move/start-stop penalties (w₅); operator-fixed jobs;
  banned tuples (operator rejections); the configured **minimum parcel** per
  active bucket with a priced `short_parcel` exception, and an optional
  **minimum dwell** in one activity group (see *Campaign, parcel and
  minimum-run policy* below).
- **Determinism**: single worker + stored seed + `max_deterministic_time`
  budget. Quality at that setting is recovered by a **complete greedy
  warm-start hint**: a dispatcher-style pass assigns stacking of arrivals,
  reclaim by destination priority and quality fit, then computes exact
  values for *every* auxiliary variable (inventory chain, slacks, conflict
  booleans, move indicators) so CP-SAT's first solution is already an
  operable plan.
- **Graceful gaps**: unmet demand, off-spec slack, safety shortfall and
  surge/spill slacks are reported with heuristic **reason codes**
  (machine capacity / route bottleneck / inventory / hard spec) and
  **suggested relaxations** in `PlanResult`.
- **Continuous-feed test**: one shared definition
  (`continuity.continuity_gap_threshold_t`) drives the CP-SAT `feed_gap`
  variable, the KPI layer and every export. With `minimum_tph = 0` the historic
  rule was "less than ONE physical tonne in a demand-active bucket", which a
  single token tonne satisfied; `minimum_service_fraction` additionally
  requires that share of the bucket's **own** requested mass, so the incentive
  is corrected without asserting an unapproved stable feed rate (S3-A33).
- **Weekly decomposition**: the 168-hour S0 problem is solved as seven
  consecutive civil-day windows under one hard wall. Each window receives the
  previous terminal inventory, pile segments, machine positions and in-transit
  parcels; jobs retain absolute weekly bucket indexes. A failed window returns
  an explicitly partial plan and marks the untouched tail as unplanned.

## Schedule granularity: allocation slices, physical runs, planning days

Three different objects were previously conflated, which is what made S0 look
fragmented:

| object | definition | where it lives |
|---|---|---|
| **allocation slice** | one `JobBucketFlow`: tonnes on one route path in one planning bucket | `Job.bucket_flows`, `Job.allocation_slice_count` |
| **allocation job** (`Job`) | one activity group, one rolling planning window, one contiguous block of active buckets | `PlanResult.jobs` |
| **physical run** | one machine, one activity group, continuous in time — the object an operator schedules | `mph_sim/runs.py`, `Job.physical_run_id` |

The relationship is **many-to-many**. One run may span several allocation
slices, several allocation jobs and the rolling civil-day boundary; and one
allocation job may span several runs, when two of its buckets are separated by a
real stop. Run identity therefore lives on each `JobExecutionWindow`
(`physical_run_id`), and the Gantt and the execution-window export read it from
there; `Job.physical_run_ids` lists every run a job contributes to, while the
singular `Job.physical_run_id` names the first for legacy consumers. Labelling
only the parent job gave every later bar of a split job the *first* run's id.

`Job.continues_previous_planning_day` marks a job that exists only because a
solve window ended. Runs are derived from the **minute** schedule, never from
bucket indices, so two jobs are joined only when nothing physically stopped
between them.

`mph_sim/runs.py` also names every blank interval on a machine:

- **HOLD** — deliberately kept running between two bursts of the *same*
  activity group: either because the hourly plan explicitly pays to hold the
  machine through those buckets (`PlanResult.machine_hold_buckets`) or because
  every bucket the blank interval touches is one this machine works, or failing
  both because the gap is within the configured `max_hold_min`;
- **CHANGEOVER** — between two *different* groups, covered by the reserved
  positioning time or the same allowance;
- **IDLE** — anything longer. The machine genuinely stopped, and the executed
  accounting counts one stop plus one later start.

The plan breakdown therefore carries two transition counts, which answer
different questions and are both reported:
`scheduled_starts/stops/group_changeovers` (how many *buckets* changed state,
the CP-SAT decision) and `executed_starts/stops/changeovers` plus
`executed_hold_minutes` / `executed_idle_minutes` (what the placed minute
schedule actually does). A blank gap is never presented as continuous
production.

They are reconciled by an identity rather than a tolerance. The hourly model
cannot see how long a changeover takes, so a group change in adjacent buckets
always costs one changeover; when the minute schedule finds the real positioning
gap too long for one, that single hourly event becomes a stop plus a start.
Hence `executed_transitions - hourly_transitions ==
changeover_reclassified_as_stop_start`, and
**`transition_reconciliation_residual` must be zero** — asserted per machine and
in aggregate on real solves. `hourly_*` is recomputed independently from the
extracted allocation, so it also cross-checks the solver's own `scheduled_*`
counters; for a single-window solve they must be equal, and in a rolling solve
the per-window-sum difference is reported as
`transition_window_boundary_delta`.

An explicit **held** state (`EQUIPMENT_HOLD_BUCKET_PENALTY`) lets a machine stay
on through a bucket without moving material. Without it the cheapest way to
bridge a bucket was a token parcel, and the audited S0 week carried five 1 t
bridge buckets; see `docs/S0_PARCEL_AUDIT.md`.

## Campaign, parcel and minimum-run policy (campaigns.py)

`CAMPAIGN_POLICY` resolves default → machine kind → machine and is the single
definition used by the optimiser, the minute overlay, the KPI layer, exports
and the schedule views:

- `min_parcel_t` — smallest parcel worth commanding in one bucket. Without it
  the group activity rule degenerates to `tot ≥ 1 t` for every machine whose
  `rate_min_tph` is zero (every stacker here), so a campaign could be padded
  with meaningless one-tonne hours at no cost. A below-floor parcel remains
  representable through a `short_parcel` boolean priced by
  `SHORT_PARCEL_PENALTY`, and its count is exported — feasibility is preserved
  and low-tonnage work is never discarded.
- `min_run_min` — shortest continuous material run worth starting for.
- `max_hold_min` — longest gap represented as held-running rather than stopped.
- `min_dwell_buckets` — hard campaign length; `1` (default) disables it, and it
  is skipped wherever a calendar block or the window end would conflict.
- `prefer_continuous` — lengthen adjacent buckets of one campaign into a single
  continuous run. A parcel **below** `min_parcel_t` is never lengthened:
  stretching 1 t over 20 or 60 minutes implies a 3 or 1 t/h rate, so such a
  window keeps its physical minimum duration and is labelled
  `rate_credibility = BELOW_MIN_PARCEL`.
- `allow_short_parcel` — `true` (default) keeps the floor relaxable, so the
  smoothing policy can never make the model infeasible; `false` makes it hard,
  at a measured cost of 16,226 t of S0 service.

All four defaults are **ASSUMED** (S3-A32) and adjustable per scenario, per
machine kind and per machine.

## Closed loop (simulator.py)

`plan → validate → dispatch → execute → feedback → detect deviation → replan`
(report §15.8). Per bucket: apply injected events → execute active jobs
(rates limited by machine availability/factors, link availability, inventory,
space capacity) → move corridor parcels (`BeltParcel`, arrive_bucket = dispatch
+ transit) → emit `JobUpdate` actuals (suppressed during comms loss) → detect
shortfall > tolerance → **replan**: snapshot live state (inventories,
qualities, belts, outage calendars), freeze the frozen-horizon/locked/HUMAN
jobs as fixed jobs (clipped around outage windows), ban rejected tuples,
re-solve the remaining horizon, splice. All 11 required event types are
implemented in `_apply_events`.

## Stockyard Monitor (`ui/stockyard_monitor.py`)

Pure view-model `build_monitor_state(Scenario, PlanResult?, Simulator?,
basis, bucket) -> MonitorState` (no Streamlit; fully unit-testable):
Pile/Machine/Corridor/Site view-models, deterministic `grade_color`,
coverage audit vs `EXPECTED_COVERAGE` + `REPORT_UNMODELLED` gap register.
`site_figure(SiteVM, …)` renders one yard as Plotly shapes (lanes = capacity
outline + grade-coloured fill scaled to fill %, real Segment slices when
usable, safety-stock ticks, stack/reclaim rails with machine markers, flow
arrows for active jobs, outlet boxes); `render_monitor` is the only
Streamlit-aware layer (toolbar, corridor header, panels). Simulation history
(`quality_series`, `activity_log`) was added to `Simulator` — additive,
deterministic behaviour unchanged (pinned signature test).

## Longitudinal coordinate model

The domain and integration boundary use **physical metres** for pile bounds,
inventory segments, `Job.start_x/stop_x`, machine positions, work areas,
travel and separation constraints. This matches the Innomotics FRS
`StartX`/`StopX` position model and its 1-m inventory cache.

The operator-facing layer adds the user-confirmed **repère** convention:
every pile is represented by boundary coordinates **R0–R1000**, or exactly
1,000 equal intervals. The UI can author and display a job such as
`R400–R600`; the domain/adapter resolves it through the selected pile's
calibration:

`x_m = x₀_m + (R / 1000) × (x₁_m − x₀_m)`.

This is deliberately a coordinate translation, not a replacement geometry.
All feasibility and safety logic stays metre-canonical, and reverse
conversion may be used only for display. The repère name/scale is
USER_CONFIRMED (24-Jul-2026); physical calibrations remain configuration
inputs. Commissioning must provide, per pile, the R0 endpoint and increasing
direction, surveyed origin and usable length, plus the production ICD's
StartX/StopX units, origin/orientation and scale.

## Data & persistence

`Scenario` is one self-contained snapshot (entities + weights + sim params +
seed + assumptions register). JSON round-trip is loss-free (tested); every
entity table exports/imports as CSV/XLSX/JSON with evidence columns
(`source`, `evidence_status`, `confidence`). Runs are appended to SQLite
`runs.db` (timestamp, scenario, seed, solver, status, KPIs).

## SMS/ASP capability boundary

`adapters.AspAdapter` defines plan submission, job-update and inventory
surfaces matching the functional vendor telegram families. The production
capabilities deliberately have different status:

- **Read-only pile inventory:** implemented in `rabbitmq_inventory.py`, but
  uncommissioned. Production use requires the approved per-site RabbitMQ ICD,
  AMQPS endpoint/vhost and permissions, exact request/reply/correlation and
  payload paths, explicit mass/X/age/quality units, per-pile repère-to-X
  calibration, TLS references, protected credentials and one-to-one
  pile/master-data mappings. Install the optional
  dependency through `requirements-rabbitmq.txt` and provide the secret-free
  profile via `MPH_SMS_LIVE_INVENTORY_CONFIG_PATH` or
  `MPH_SMS_LIVE_INVENTORY_CONFIG_JSON`.
- **Plan submission and `JobUpdated`:** not implemented live.
  `SimulatedAspAdapter` remains the explicitly labelled eight-rule
  approximation (5 ERROR / 2 WARNING / 1 INFO), and `RabbitMqAspAdapter`
  remains a stub until the corresponding ICD details, write permissions and
  plan co-authoring governance (H3) are approved.

The FRS supports the functional `SpaceValue` concept and 1-m inventory basis;
it does not evidence the production exchange names, routing, JSON field paths,
units or site master data. The blank configuration template therefore records
those as commissioning inputs rather than inventing defaults. H2 is resolved
only at solution-family level: all three sites use the same SMS family, while
each endpoint, profile and mapping remains independently commissioned.

## Known simplifications (engine)

1. Planning uses a constant per-space quality snapshot per solve; the
   simulator applies mass-weighted quality updates and each replan refreshes
   the snapshot (S3-A7).
2. Spec `averaging_h` recorded but enforcement is per bucket.
3. Start/stop limits finer than one bucket (sulphur 8/h) are penalised, not
   enforced (S1-A4) — a sub-hourly dispatcher iteration would own them.
4. Timed machine, link and receiving-plant outages are carried as bounded
   calendar windows in the replan snapshot. Overlapping derates restore the
   remaining active cap independently when each window expires.
5. Solver job x-cuts still use a uniform-density approximation over the pile
   x-range. The separate simulated ASP gate now uses supplied inventory
   segments and chronological reservations (see below); a solver-feasible
   plan may consequently be rejected by that more detailed gate.
6. Route utilisation attributes each job's tonnage to its dominant path when
   a stream splits across parallel legs.

### Position-aware simulated ASP inventory gate

`asp_inventory.InventoryProjection` is a fresh, non-mutating ledger for each
`SimulatedAspAdapter.submit_plan_items` batch. Jobs are ordered by start time
and ID. Repères are converted to metres by the existing coordinate layer;
reverse physical travel ranges are normalized for inventory/work-area checks.

- Supplied segments define mass, grade, assay and age locally. Gaps are empty;
  overlapping, out-of-bounds, non-finite or mass-inconsistent snapshots reject
  affected jobs. Splitting at job boundaries preserves mass. Within a segment,
  distribution is assumed uniform; this is not a vertical-layer model.
- Reclaim jobs reserve their complete mass at start, withdrawing proportionally
  across all lots in the selected range. They cannot selectively take only the
  convenient grade. The range's unique most-prevalent grade must match the job.
  Every positive-mass selected lot must have a finite, nonempty quality vector.
- Stacking jobs reserve capacity at start and release material at completion.
  Reclaiming frees physical capacity at completion, not at reservation time.
  These conservative rules do not support using partially completed stacking
  output within the same job. Adjacent validated minute windows remain separate
  items, so completed windows can supply later ones.
- Items with structural/calendar/overlap ERRORs, or failed inventory checks,
  do not change the ledger. Concurrent jobs cannot reserve the same mass twice.
  Duplicate IDs and malformed times/numbers fail closed. Evaluation is repeatable
  and never changes the scenario's opening inventory.
- `ASP_ALLOW_ASSUMED_INVENTORY` defaults to `true` only for offline compatibility:
  absent spatial inventory uses a disclosed uniform pile distribution, and
  missing assays may use the scenario grade-quality basis. `InventoryBasis`
  INFO issues disclose assumptions in the existing integration issue table.
  Set the configuration value to `false` to require explicit segment inventory
  and local assays. Explicit unknown-stock quality is never filled from a
  grade catalogue. A stacked grade's catalogue assay is a forecast, not an SMS
  measurement; strict mode cannot certify it for subsequent reclaiming.
- Unlocated exogenous arrivals and process-transfer outputs are not credited
  automatically. They need an authoritative located snapshot or explicit
  stacking items. FIFO advice remains the older pile-level approximation;
  cuboid age, vertical accessibility, assay confirmation events and full 3-D
  capacity are not implemented by this change. The vendor ICD and live
  validation/authorization remain commissioning requirements.

Regression coverage: `tests/test_asp_inventory_projection.py`, including
sequential overdraw, partial overlap, nonuniform/empty ranges, grade and assay
checks, completion-time replenishment, capacity reservations, rejected-job
isolation, reversed repères, conservation and integration rejection propagation.
