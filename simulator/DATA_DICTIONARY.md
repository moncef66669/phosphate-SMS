# Data Dictionary — scenario tables (`mph_sim/domain.py`)

Every entity carries an `Evidence` block: `source` (document/placeholder),
`status` ∈ {CONFIRMED, DERIVED, INFERRED, ASSUMED, TBC} (report §16 vocabulary)
and `confidence` ∈ {H, M, L}. Time is expressed in integer **buckets** of
`sim.dt_h` hours starting at `sim.start_iso`.

## sites
| field | unit | meaning |
|---|---|---|
| id, name | — | site identifier (LOUTA / BENGUERIR / MPH) — Domain A is the three-site chain LOUTA →C1→ MPH M16 ←C2← BENGUERIR (report Rev D); M17 areas at MPH form Domain B |
| role | — | `mine` (source yard feeding the hub) or `hub` (M16 receiving/blending/buffer) |
| sms_endpoint | — | future adapter endpoint; `"simulated"` in the MVP |

## spaces (stockpiles / storage cells)
| field | unit | meaning |
|---|---|---|
| id, site, area | — | space identity; area e.g. `M16-humid`, `M17-closed`, `mine-yard` |
| kind | — | open / closed |
| capacity_t, mass_t, reserved_t, safety_stock_t | t | capacity, current inventory, reservation, safety target |
| grade | — | dominant grade id (lookup into grades) |
| quality | per param | override quality vector; empty → grade vector used |
| state | — | empty/building/full/reclaiming/mixed/reserved |
| designated_grade | — | anti-contamination: only this grade may be stacked ("" = free) |
| x_from, x_to | m | rail coordinate range (geometry ASSUMED until plot plan/GA) |
| repere_at_x_from, repere_at_x_to | repère | operational labels at the physical X endpoints; default `0, 1000`, or `1000, 0` when the site numbering runs in the opposite direction |
| node_id | — | route-graph node (default `SPACE:<id>`) |

## segments (coarse mirror of the ASP 1-m inventory cache)
space_id · x_from/x_to [m] · mass_t [t] · grade · quality · age_days [d]

## repère coordinate layer

Every pile has an operator-facing longitudinal scale **R0–R1000**. The
endpoints are boundaries, so the scale contains exactly **1,000 equal
intervals**. Job instructions use two boundaries, for example
`pile=LT-P1, repere_from=400, repere_to=600`.

Metres remain the canonical model, inventory, interface and safety unit.
For a pile whose approved physical work area is `[x₀_m, x₁_m]`:

| conversion | formula |
|---|---|
| repère → metres | `x_m = x₀_m + (repere / 1000) × (x₁_m − x₀_m)` |
| metres → repère | `repere = 1000 × (x_m − x₀_m) / (x₁_m − x₀_m)` |

`R400–R600` therefore selects 40–60% of that pile's longitudinal work area;
it does **not** imply that the pile is 1,000 m long. The Innomotics FRS
confirms numeric `StartX`/`StopX`, work-area validation and 1-m inventory
segments. The term **repère** and R0–R1000 scale are USER_CONFIRMED
(project team, 24-Jul-2026). The physical calibration remains TBC until each
pile has a confirmed R0 endpoint/direction, surveyed origin/usable length and
production-ICD X units/scale.

## assumed pile-grade distribution metadata

`Scenario.config["PILE_GRADE_DISTRIBUTIONS"]` maps a pile id to ordered
component rows. `Scenario.config["PILE_GRADE_DISTRIBUTION_BASIS"]` maps the
same pile id to provenance and caveat text.

| field | unit | meaning |
|---|---|---|
| component_id | — | unique identifier within the pile |
| grade_id | — | stage-compatible profile from `grades` |
| mass_fraction | 0–1 | positive share; rows must total exactly 1 |

For a solver-modelled pile, component tonnes equal
`StockSpace.mass_t × mass_fraction`; `StockSpace.quality` is the
mass-weighted aggregate used by current planning. The component distribution
does **not** create independently reclaimable material. Rendered bands are
mass-share graphics—not surveyed longitudinal order, stacking chronology or
reclaim sequence. Valid `Scenario.pile_zones` or imported inventory segments
override the assumed composition in the UI. Profile-selective reclaim
requires surveyed boundaries and an active reclaim face.

S3 uses this schema for Louta product lanes:

| pile | capacity | starting mass | assumed components | aggregate planning quality |
|---|---:|---:|---|---|
| `LT-P1` | 191,512 t | 95,756 t (50%) | 60% TBT + 40% BT | BPL 65.0 · Cd 33.0 · MgO 1.28 · SiO₂ 3.22 · moisture 12 |
| `LT-P2` | 191,512 t | 95,756 t (50%) | 60% BT + 40% MT | BPL 70.4 · Cd 26.8 · MgO 0.98 · SiO₂ 2.48 · moisture 12 |

The equal pile masses give a yard aggregate of 30% TBT / 50% BT / 20% MT.
TBT/BT/MT are washed demonstration vectors with Louta mapping and cut-offs
TBC. Raw `LOU-RAW-*` profiles remain separate and are not assigned to the
`110LG` lanes.

### display-only Benguerir raw composition

`Scenario.config["BG_RAW_DISPLAY_GRADE_COMPONENTS"]` maps each display pile
to ordered component rows:

| field | unit | meaning |
|---|---|---|
| component_id | — | unique identifier within the display pile |
| grade_id | — | documented proxy profile from `grades` |
| mass_fraction | 0–1 | positive share; rows must total exactly 1 |

The pile mass remains sourced once from
`BG_RAW_DISPLAY_START_MASS_T`; the current t0 baseline is **100,000 t per
200,000 t-capacity pile (50%)**, and component tonnes are derived as
`pile_mass × mass_fraction`. Each 50/50 component is therefore 50,000 t.
`PileGeo.display_grade_components` adds mass-proportional
`frac_from/frac_to` band bounds for rendering only. Under the current
front-filled display convention, the components occupy **R0–R250** and
**R250–R500**, while **R500–R1000 is empty**. These are display locations,
not surveyed longitudinal order. `PileGeo.display_quality` is the
mass-weighted composite
for parameters available on every positive-mass component; missing chemistry
is never treated as zero. These reference piles remain outside
`Scenario.spaces`, inventory, jobs, solver constraints and KPIs.

## starting inventory snapshot (transient import schema)

The snapshot is validated and applied to a deep-copied Scenario; it is not a
replacement for the `spaces` topology table.

| field | requirement | meaning |
|---|---|---|
| captured_at | required once/repeated | one coherent timezone-aware ISO-8601 planning t0 |
| source | recommended | SMS/YardMaster export or source-system label |
| site | recommended | exact owner site; validated against the model |
| pile_id | required | exact model pile ID, or external ID with explicit mapping |
| record_type | CSV/XLSX flat files | `pile` (default) or `segment` |
| mass_t | required | pile total, or segment mass; finite and within pile capacity |
| grade | optional | known, stage/material-compatible grade |
| quality | optional | JSON `{parameter: value}`; `q_BPL`, `q_Cd`, ... columns also accepted |
| state | optional pile field | empty/building/full/reclaiming/mixed/reserved |
| x_from, x_to, age_days | segment fields | longitudinal bounds [m] and non-negative age [d] |

JSON may use `{schema_version,captured_at,source,site,piles:[...]}` with nested
`segments`. XLSX may use `piles`, optional `segments`, and optional `metadata`
sheets. Segment totals must reconcile with a declared pile total within 1 t;
when only segments are supplied the parent total, prevalent grade and
mass-weighted quality are derived. Applied provenance is stored under
`Scenario.config["INVENTORY_SNAPSHOT"]`; capacity/topology Evidence is never
overwritten.

## grades (materials)
id · name · quality {param: value} · rock_value [rel./t — economics placeholder]

## machines
| field | unit | meaning |
|---|---|---|
| kind | — | stacker / reclaimer / tripper |
| rate_min_tph, rate_max_tph | t/h | minimum stable and maximum rate |
| reachable_spaces | — | configured machine↔pile reachability (never inferred). At M16 this is the bay-adjacency matrix from `M16_BAYS`/`M16_MACHINE_LINES` (PFD-0001[3] + project-team rule): ST01-A→HP-01/05 · RC01-A→HP-01/05/02/06 · ST01-B→HP-02/06/03/07 · RC01-B→HP-03/07/04/08 · ST01-C→HP-04/08 — never all-to-all |
| rail | — | canonical machine-line tag (M16: `M16A-A-T06`, `M16A-B-T06`, `M16A-C-T06`, `M16A-A-T11`, `M16A-B-T11`; there is no shared `T06`/`T11` corridor) |
| travel_speed_m_min | m/min | long-travel speed (ASSUMED without GA) |
| setup_min | min | setup/changeover time |
| min_separation_m | m | ASP UC-28 configurable minimum distance |
| start_stop_limit_per_h | 1/h | 0 = unconstrained; sulphur = 8 (sub-bucket, penalised) |
| work_area | m | optional {space: [x_from, x_to]} restriction |
| position_x | m | current position (simulation state) |

## nodes / links (route graph — the REAL network, report §14.2)
Nodes: id · site · kind (space/tower/gate/dest/source) · label · x,y (drawing hint).
Links: id · src · dst · capacity_tph [t/h] · min_stable_tph [t/h] ·
transit_buckets [buckets — corridors] · available · shared_group (links that
share one physical resource; group capacity = min of members — M16 uses `shared_group="M16A-A-T01"` on every stacking leg for the ONE current 2,600 t/h reception path) · conflict_group
(mutually exclusive links, one active per bucket) · gate_id/gate_position ·
changeover_min [min].

## destinations
id · site · node_id · priority (1 = highest) · demand_default_tph [t/h] ·
spec = list of SpecRange: param · pmin/pmax/target (quality units) ·
averaging_h [h — recorded; MVP enforces per bucket] · margin ·
**hard** (bool): hard → enforced by the optimiser (allocation blocked, demand
left unmet rather than violated); soft → violations permitted with objective
penalty w₂ and reported in the quality breakdown. Spec evidence status =
the destination's Evidence tag (ASSUMED/TBC specs are placeholders).

## demand
dest_id · bucket · rate_tph [t/h] — explicit override of the destination default.

## calendars (work calendars / maintenance)
scope (`GLOBAL` or machine id) · start_bucket · end_bucket (inclusive) · reason.

## exogenous (flows the layer does not command)
id · target_space (filled) · source_space (drained, optional — payloaders) ·
rate_tph [t/h] · grade · start/end_bucket · quality.

## belt_profile (initial in-transit material, report §15.3 eq. 5)
link_id · arrive_bucket · mass_t [t] · grade · quality.

## fixed_jobs (operator-fixed; must be honoured)
id · machine · kind (stack/reclaim/dispatch) · space · dest_id · rate_tph ·
start/end_bucket · grade · start_repere/stop_repere [R0–R1000 boundaries] ·
note. The repère pair is converted to canonical metre `StartX/StopX` before
work-area, travel, separation and ASP validation.

## quality_params
name (BPL, Cd, MgO, SiO2, moisture, S, …) · unit (%, ppm) · description.
All parameters blend linearly by mass.

## assumptions (live register)
id · text · module · status (OPEN/CONFIRMED/RETIRED) · source · confidence ·
placeholder · impact · owner · action.

## weights (ObjectiveWeights, report §15.2)
w1_unmet (must dominate) · w2_offspec · w3_process_cost (inactive — needs
GradeCostModel) · w4_giveaway · w5_moves · w6_safety · w7_utilisation.

## sim (SimParams)
dt_h [h] · horizon_buckets · frozen_buckets · seed · start_iso ·
max_solve_s [CP-SAT deterministic-time units] · deviation_tolerance [–] ·
replan_on_deviation.

## Outputs
- **Job**: id · machine · kind · space · dest_id · grade · rate_tph ·
  tonnage_t · start/end_bucket · path [link ids] · method (→ ASP Method) ·
  origin (AUTO/HUMAN) · locked · status · start_x/stop_x [m] (uniform-density
  cut; canonical coordinates) · derived UI/export fields
  repere_from/repere_to [R0–R1000] · comment. The repère values are calculated
  from `start_x/stop_x`; they are not duplicate persisted `Job` attributes.
- **PlanResult**: status · jobs · per-bucket unmet/offspec/safety_short/
  surge/delivered/inventory · objective + breakdown · reasons · relaxations.
- **KPI `onspec_pct`**: share of (destination × bucket) pairs WITH delivered
  material inside every limit; zero-delivery buckets are not evaluated.
  `quality_breakdown` rows: destination · parameter · limits · hard/soft ·
  evidence · source · evaluated/compliant/violations · avg/max deviation;
  `onspec_principal` = most-violating pair (drives the overview message).
- **Simulator history** (monitor inputs): `inv_series[space][t]` [t],
  `quality_series[space][t]` {param: value}, `activity_log` rows
  (bucket · job · machine · kind · space · dest_id · grade · moved_t ·
  rate_tph · note) — index 0 = initial state, t+1 = after bucket t.
  **Outage history** (mimic inputs, additive): `down_series[t]` = machines
  inside a failure window during bucket t; `link_down_series[t]` = tripped
  links during bucket t.
- **SiteGeometry** (`ui/layout_geometry.py`, display configuration — never
  solver data): bounds · not_to_scale · source · PileGeo (lane rect,
  `modelled`/`future` flags, optional display-only grade components and
  mass-weighted composite quality) · **RailGeo** (horizontal machine line: x0/y/x1
  · kind · `flanks` served-pile bank · working_sides · travel limits ·
  parking_frac · source · metric/topological `confidence`) · MachineGeo
  (home rail, parking_frac, `display` flag, `physical_tag` e.g.
  M16A-A-ST01) · ConveyorGeo (polyline, bound `link_ids`,
  `planning_enabled`, tbc) · EquipGeo (tower/gate/instrument/corridor
  interface) · ZoneGeo (SMS scope / process stage) · ChipGeo (confirmed but
  unassigned tags) · StampGeo (NOT-TO-SCALE stamps) · gaps register.
  `validate_geometry` checks rail↔pile adjacency, travel-span coverage and
  confidence statements.
- **Site concurrency policy** (`domain.Site`, 15-Jul-2026):
  `same_pile_concurrency_allowed` (True = USER_CONFIRMED at MPH · None =
  TBC, treated as permitted · False = site-configured prohibition) ·
  `concurrency_evidence` · `min_machine_separation_m` (50, UC-28 default —
  value TBC) · `work_zone_buffer_m` (25, ASSUMED) · `travel_clearance_m` ·
  `machine_envelope_m` · `concurrent_route_allowed`.
- **Asset lifecycle & options** (`domain`, 19-Jul-2026, B02 BoD):
  `Machine.lifecycle` / `Link.lifecycle` (EXISTING · SELECTED ·
  OPTIONAL_PROPOSED · FUTURE · REFERENCE_ONLY · OUT_OF_SCOPE) ·
  `Machine.planning_enabled` (False ⇒ planner NEVER assigns jobs — separate
  from lifecycle and from `available`) · `Machine.physical_tag` ·
  `Machine.rate_design_tph` / `Link.capacity_design_tph` (documented DESIGN
  capacity; planning uses the NOMINAL `rate_max_tph`/`capacity_tph`) ·
  `Link.material_class` ("raw" | "washed" | "") · `Scenario.config`
  (BEN_B02_OPTION_ENABLED · BEN_RAW_TO_C2_ENABLED · CAPACITY_ENVELOPE) ·
  `Site.material_profiles` (documented material-state property display
  metadata, e.g. Benguerir B02 §2.3.1).
- **MachineGeo `twin_boom`** (`ui/layout_geometry.py`, 19-Jul-2026, display
  layer only): ONE twin-/double-boom machine (e.g. B02 "Twice boom
  stacker ST3**"), rendered by `hs.twin_boom_stacker` — never drawn as
  two machines.
- **MachineGeo `package_scope` / `planning_scope`** (`ui/layout_geometry.py`,
  20-Jul-2026, display layer only): e.g. Benguerir's Table 20 reclaimer
  `BG-RAW-BWR`: `package_scope="OUT_OF_SCOPE"` — the SOURCE PACKAGE's own
  scope statement, never inferred to mean outside the SMS/application;
  `planning_scope="TBC"` separately — application ownership
  independently unresolved.
- **Site feeder exclusivity** (`domain.Site`, 18-Jul-2026):
  `exclusive_machine_groups` (list of machine-id lists; at most ONE machine
  of each group active per bucket — S3 configures the Louta C1 feeders
  `140LG-RC-01/-02` while RL3/RL4 route confluence is TBC) ·
  `exclusivity_evidence`. Config data, enforced by the CP-SAT model and
  respected by the greedy warm start; never a hard-coded rule.
- **ConcurrencyVM** (`ui/process_mimic.py`): space · stack_jobs · out_jobs ·
  sep_m · req_m · buffer_m · status (ok | warning | violation) · site
  policy + evidence — drives the work-zone/buffer overlay.
- **JobUpdate** (simulated `JobUpdated`): job_id · bucket · status · moved_t ·
  effective_rate_tph · machine_position_x · quality · forecast_end_bucket.
- **AspPlanItem**: Device · JobType · Method · Pile · Material · Grade ·
  ReferenceLocation · StartTime · StartX/StopX · PlannedTonnage ·
  PlannedFlowRate · Comment (report §15.6 mapping). Faithful to the common
  vendor telegram schema — carries **no site/SMS field**. `StartX/StopX`
  remain metre-canonical; operator repères are converted using the selected
  pile's approved calibration before submission.
- **SmsInstanceConfig** (`integration.py`): site_id · sms_id · display_name ·
  endpoint (blank/TBC) · adapter_profile (`INNOMOTICS_YARDMASTER_ASP`) ·
  connection_mode (reference | offline-simulated | configured | connected) ·
  evidence · configured. Defaults: LOUTA-SMS / BENGUERIR-SMS / MPH-SMS. This
  is the offline/simulated ASP surface; it does not represent commissioning of
  the separate read-only inventory connector.
- **RabbitMqInventoryConfig** (`rabbitmq_inventory.py`, read-only): site_id ·
  sms_id · enabled · `amqp_url_env` (environment-variable name, never the
  secret value) · request exchange/routing key · `JsonSpaceValueProfile` ·
  external_to_model pile map · connect/socket/RPC timeouts · heartbeat · TLS
  environment references/server name · response size/row/skew limits ·
  profile revision. `safe_dict()` is exportable and credential-free.
- **JsonSpaceValueProfile**: request mode (`per_pile` | `all`) and template ·
  response-items/server-capture paths · canonical field map · Grade and
  quality maps · aggregate/segment flag · explicit mass/X/age/quality scales ·
  optional body-correlation/type checks · required protocol-version check ·
  at least one required response site/SMS identity check · pile ID wire type.
  Per-pile mode must put `{pile_id}` in the request template. Every wire-
  specific value is an ICD commissioning input.
- **SpaceValueResponse**: site_id · sms_id · captured_at (server time) ·
  received_at · correlation_ids · canonical rows · protocol_version · source ·
  payload hash · connector-config revision. It is a read-only acquisition
  result, not an applied scenario state.
- **InventorySnapshot** (`inventory_snapshot.py`): captured_at · source ·
  piles/segments · schema/source-file/payload hash · acquisition_mode
  (`file`, `adapter`, `live-read-only`) · per-site capture timestamps ·
  connector-config revision. Validation and apply are atomic; apply records
  provenance and replaces planning `t0`.
- **LiveInventoryPullResult**: optional combined InventorySnapshot · per-site
  SpaceValue responses · typed issues · requested sites · scenario inventory
  fingerprint. `ok` requires a snapshot and zero ERROR issues; failure never
  mutates or silently substitutes the loaded scenario.
- **SmsPlanBatch** (site-targeted envelope, batch → SMS → AspPlanItem[]):
  site_id · sms_id · plan_revision (job-signature hash) · generated_at ·
  horizon_buckets · items · jobs · validation_issues (site-scoped 8-rule
  gate) · dispatch_state · item_states {job_id → simulated lifecycle} · note.
- **partition_jobs_by_sms(sc, jobs)** → {LOUTA, BENGUERIR, MPH, UNMAPPED}:
  ownership `Job.machine → Machine.site`, cross-checked against the pile's
  site; mismatch/unknown → UNMAPPED (never silently MPH); lossless and
  duplicate-free.

### Link fields with no enforcement (be explicit)

| field | status |
|---|---|
| `changeover_min` | **documented, NOT modelled.** The 10-minute PAP/drying gate-route changeover is recorded in the evidence text and in S2-A4, but the planner enforces no inter-bucket switching loss; every link ships `changeover_min = 0.0` so the value cannot imply enforcement. |
| `gate_id` / `gate_position` | `gate_id` names CONFIRMED discharge equipment (`M16A-{A,B}-H01…H04` on `L-H-{A,B}`); `gate_position` marks the DERIVED destination selection on `L-RTE-*`. A confirmed belt rating never makes the destination assignment confirmed. |
