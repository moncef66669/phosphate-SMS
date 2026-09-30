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
  banned tuples (operator rejections).
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
4. Link outages inside a replan snapshot are conservative (unavailable for
   the remaining horizon); machine outages are correctly windowed via
   calendar entries.
5. Job x-cuts use a uniform-density approximation over the pile x-range,
   shared with the simulated ASP inventory rule.
6. Route utilisation attributes each job's tonnage to its dominant path when
   a stream splits across parallel legs.
