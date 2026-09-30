# Phosphate Value-Chain Job-Allocation Simulator — Engineering MVP
### Louta & Benguerir Mine Stockyards → MPH M16 Hub · M17 Sulphur

Offline simulation of the **automated job-allocation layer above the Stockpile
Management Systems** (OCP SP2M), per the project report *MPH — Automated Job
Allocation Layer for the SMS*, **Rev D, 13-Jul-2026** (functional baseline:
report §14–§18 + `CLAUDE-CONTEXT.md`).

**Domain A is a three-site system, not an M16-only simulator**: the LOUTA
mine stockyard feeds the MPH **M16 receiving/blending/buffer hub** through
the **nearer C1 corridor** (provisional 30 km, ≈2 h 05), while the
BENGUERIR mine stockyard feeds it through the **farther C2 corridor**
(provisional 60 km, ≈4 h 10). This relative order was corrected by the
project team on 22-Jul-2026; exact route lengths, effective speeds and
transfer dwell remain TBC.
Louta, Benguerir and M16 are three
peer controlled planning sites — the layer plans mine-yard inventory and
stacking, mine reclaim-to-corridor dispatch, C1/C2 transport delays and
in-transit material, M16 receipt/blend-while-stacking, and M16 reclaim to
PAP and drying, issuing hourly job plans for all three SMS. **M17 sulphur**
is the separate Domain B on the same platform.

> **Status: engineering MVP.** Not production software. Read-only RabbitMQ
> `SpaceValue` inventory retrieval is implemented as an **uncommissioned**
> capability: no OCP endpoint or production telegram profile is claimed as
> validated. Plan submission, `JobUpdated` feedback and equipment control
> remain offline/simulated. Dataset values tagged `ASSUMED`/`TBC` are
> placeholders, never engineering facts.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

The optional read-only RabbitMQ client is installed separately with
`pip install -r requirements-rabbitmq.txt`; the normal offline application and
tests do not require it.

Open http://localhost:8501.

**Console UX** (industrial planning-console model):
- The top domain bar exposes **BG + LO + MPH** and the reserved **MS**
  workspace. Only **BG + LO + MPH** is commissioned; selecting MS shows a
  non-destructive placeholder and preserves the current phosphate plan.
- **S0** is the sole operator-facing planning scenario. It maps to the
  validated internal **S3 three-site dataset** without renaming engineering
  entities, evidence IDs or persisted results. Additional planning scenarios
  will be added to the Plan workspace later; the legacy S1/S2/S3 builders
  remain available only for diagnostics and QA.
- The sidebar owns the single **Generate Plan** action. It runs a staged solve
  shown in a status panel at the top of
  the main area (validate → build model → warm start → CP-SAT → results) with
  the typical duration for the scenario; a failed solve keeps the previous
  plan; duplicate requests are ignored while a solve is queued/running.
- **Navigation** = three operator sections in workflow order — **Configure**
  (grades/model inputs, starting inventory, machines/paths), **Plan** (the S0
  demand and planning-case page), and **Monitor** (overview/KPIs,
  stockyards/flow, jobs/schedule, machine coverage). Specialist legacy tools
  including detailed planning settings remain under **Advanced**. The 16
  operator-facing titles are unnumbered,
  while the original numeric IDs remain behind the UI so `?page=N` links stay
  compatible. Deep links: `?page=N`; `?scn=s1|s2|s3` pre-loads a scenario (no
  solve); `?demo=s3|s2|1|s1` loads a pre-solved demo bundle (`s3w` = below-
  safety warning showcase, `s3u` = unmapped-job integration showcase, `s3rc2` = 140LG-RC-01 maintenance → RC-02 feeds C1).
- **Before a plan exists** the app lands on **Plan → S0 · Three-site
  planning**; after a successful solve it remains on S0 with a compact result
  summary and a link to **Monitor → Jobs & Schedule**. The
  overview still provides its focused planning-readiness empty state when
  opened without a plan.

## Starting inventory snapshot (page 3)

The simulator can now initialize a run from a manual **CSV, XLSX or JSON
SMS/YardMaster inventory snapshot**. On **Stockyards & Inventory**, download
the exact-ID CSV template, upload the completed export, review the pile-level
diff and validation issues, then explicitly apply it. Preview is read-only;
apply is atomic and clears any plan/simulation made from the previous starting
state. The accepted snapshot time becomes planning `t0`, and provenance,
coverage and payload hash are retained in scenario configuration/export.

Minimum data needed to use it are a single timezone-aware `captured_at`,
`pile_id`, and `mass_t` for each covered pile. `site`, `grade`, measured
`quality` (JSON object or `q_<parameter>` columns), and segment
`x_from`/`x_to`/mass/grade/quality/`age_days` are optional but improve
blending and FIFO fidelity. External SMS IDs require an explicit mapping; no
fuzzy match is performed. Complete coverage is the default. Partial patches
are allowed only as a visibly mixed basis in which untouched piles retain
scenario values. Stale, future-dated, over-capacity, inconsistent or partially
invalid snapshots are rejected without changing any pile.

## Repère-based job ranges

Operators locate stockpile work with **repères**. Each pile uses the
normalized boundary scale **R0–R1000**, which creates exactly **1,000 equal
longitudinal intervals**. A stack or reclaim job can therefore specify
`R400–R600` for the selected pile.

The Innomotics FRS confirms required Start/End positions (`StartX`/`StopX`),
work-area validation, range-based inventory checks and a 1-m inventory cache.
The repère name and R0–R1000 convention are user-confirmed project inputs
(24-Jul-2026). They are not evidence that a pile is 1,000 m long.

The core model keeps physical metres canonical. For approved pile bounds
`[x₀_m, x₁_m]`, `x_m = x₀_m + (R/1000) × (x₁_m − x₀_m)`; the UI may show
repères while inventory, machine travel, separation checks and ASP messages
use metres. Operational commissioning still requires each pile's R0
endpoint/direction, surveyed physical origin/usable length and the ICD's
StartX/StopX units and scale.

### Read-only live inventory — implemented, not commissioned

Page 11 includes a guarded RabbitMQ commissioning path for
`SpaceValueRequest/Response` only. It loads a secret-free per-site profile,
checks readiness, probes each loaded SMS endpoint, and fetches every loaded
site before building one atomic preview. Correlation IDs, response size/row
limits, server timestamps, freshness, cross-site ownership, completeness and
capture-time skew are validated. A successful fetch changes no scenario
value: the operator must review and explicitly apply the preview on page 3,
which then clears any plan/simulation based on the previous inventory. A
failed or incomplete pull never falls back to simulated values under a live
label.

Commissioning requires all of the following, obtained from approved
site/vendor sources rather than inferred from the FRS:

- the RabbitMQ ICD for each site: request template and per-pile/all-pile mode,
  exchange/routing key, reply-to and correlation behavior, response item and
  server-timestamp paths, telegram type/namespace/version and site/SMS fields;
- the exact payload semantics: aggregate versus 1-m segment/layer records,
  pile/mass/X/age field names, units and scale factors, plus Grade/SubGrade and
  quality-parameter names/units;
- each pile's repère calibration: R0 endpoint, increasing direction, surveyed
  physical origin/usable R0–R1000 length, and the production ICD's X units,
  origin/orientation and scale;
- an exact one-to-one external pile-ID to model-ID map for every loaded site;
- an approved AMQPS endpoint/vhost and least-privilege publish, exclusive
  reply-queue and consume permissions; TLS CA/server-name and, when required,
  client-certificate/key references;
- protected credentials in one AMQP URL environment variable per site. The
  JSON profile stores only each variable's name—never its value.

The live decoder requires an ICD-defined protocol version and at least one
verifiable response site/SMS identity; a per-pile request must explicitly
carry `{pile_id}`. Mass-only responses can be reviewed and applied only after
an explicit missing-quality acknowledgement; the UI keeps that fallback
visible afterward. Map grade, measured quality and age before treating the
result as an operational quality/blending plan.

Set either `MPH_SMS_LIVE_INVENTORY_CONFIG_PATH` (preferred) or
`MPH_SMS_LIVE_INVENTORY_CONFIG_JSON` to a schema-1.0 profile; page 11 provides
a deliberately blank template. Enable a site only after its ICD-dependent
fields and mappings have been verified. A complete operational three-site
planning `t0` will also need active jobs and in-transit corridor material;
this read-only capability initializes pile inventory only.

## Three-SMS Integration Console (page 11)

Louta, Benguerir and MPH use **identical Innomotics/YardMaster SMS**
solutions (user-confirmed) deployed as three independent instances; the MPH
FRS defines the common interface profile, and its application to the two
identical mine-SMS deployments is confirmed by the user. The console shows
three SMS instance cards (connection mode, batch size, validation E/W/I,
lifecycle, spaces exposed), per-site tabs whose job queues never mix
ownership, a site-scoped eight-rule validation per instance, four feedback
previews per SMS (plan items · evaluation · JobUpdated · SpaceValues) and an
UNMAPPED error queue for jobs without exactly one owner. Site endpoints,
queue names, Device/Pile IDs, Material/Grade mappings, AspRoutes and
permissions remain **TBC** until ICD/configuration exports. Plan-item
evaluation, lifecycle and `JobUpdated` views remain offline simulated. The
separate read-only inventory panel exposes the implemented but uncommissioned
`SpaceValue` probe/fetch path described above; it cannot submit a plan or
control equipment. Exports (page 14) write a per-SMS `integration/` tree plus
an aggregate with a mandatory `sms_site` column. QA deep link: `?demo=s3u`
shows a deliberately unmappable job.

## Tests

```bash
python -m pytest tests -q        # 345 tests collected, ~12 min (includes CP-SAT solves)
```

Covers: mass conservation, machine double-booking, route capacity, transport
delay, quality blending (report §15.6 worked example), demand slack,
safety stock, work calendars, operator-fixed jobs, all 8 ASP rule severities,
deterministic replay, scenario import/export, run store, UI smoke of all 14
pages, and an end-to-end plan→simulate→fail→replan→export smoke test.

## Built-in scenarios (`mph_sim/datasets.py`, exports in `sample_data/`)

| Scenario | Content | Evidence |
|---|---|---|
| **S3 — Three-site phosphate value chain: Louta + Benguerir → M16** | **The primary Domain-A scenario (report Rev D architecture).** Louta (C1, nearer, provisional ≈2 h) + Benguerir (C2, farther, provisional ≈4 h) feeding the M16 hub; initial in-transit belt material; Benguerir raw + product train routes (demand 0, planning scope TBC) | corridor relative order USER_CONFIRMED; numeric distance/lag TBC/ASSUMED; Benguerir per the **B02 mechanical BoD** (Q80130-BEN-B02-ME-BOD-0001 Rev B, IFR, 05-Feb-2025, §§2.3.1–2.3.3 and §2.6) — staged raw → washing → washed-product yard; **the project team accepts the B02 table as the current configuration (ST3\*\*/RC2\*\*/RC3\*\*), with `**` retained as source notation only**. Louta L01 confirms two 102MG stockpiles, dedicated `102MG-ST-01/-02` stackers (2,200 t/h) and shared `110MG-RC-01`; these remain reference-only until live state, metric reach and the L01→L02 solver interface are configured |
| **S2 — MPH M16 hub only (simulated corridor arrivals)** | M16-only demonstration: 8×125 kt humid yard in **four two-hangar bays**, 3 stackers + 2 reclaimers each on their own machine line and serving only their adjacent bays, two separate clamshell gate banks (`M16A-A-H01…H04` / `M16A-B-H01…H04`) as CONFIRMED discharge equipment, with the PAP-versus-drying choice modelled separately as a DERIVED/TBC route abstraction, and ONE shared 2,600 t/h current reception envelope. **Upstream corridor arrivals are simulated inputs — the mines are NOT modelled here** | bay/machine-line sequence, capacities, machine identities and rates CONFIRMED (PFD-0001[3]); hangar names inside a bay INFERRED; pile contents, specs, PAP/drying confluence and arrival profile ASSUMED/TBC |
| **S1 — Sulphur minimal (M17)** | Domain B: closed-store reclaim RC01 (1100 t/h) → melting; manual payloaders 300 t/h exogenous; maintenance-window demo | storage/machine rates CONFIRMED; demand & assay ASSUMED |

**The operator default is S0**, backed by the internal **S3** dataset: the
primary end-to-end scenario aligned with the report's Domain-A hierarchy
(solve ≈ 35 s wall). **S2** remains available only as a faster M16 diagnostic
dataset with simulated corridor arrivals.

Plan generation has one primary control in the sidebar. The overview and
Advanced **Planning settings** page refer users to that control instead of
duplicating solve buttons. Machine timelines show compact tonnage labels on
every job rectangle and disclose known idle/display-only machines as empty
annotated rows; these rows never promote reference equipment into the solver.

S3 uses the **ASSUMED demand-driven coverage policy S3-A27**: exact demand
balance prevents surplus work, the terminal M16 on-ground plus C1/C2
in-transit position is capped at its t0 value, and a low-priority coverage
tie-breaker may rotate work only across available, planning-enabled candidate
machines. Reference/display-only, future and disabled assets remain ineligible.
Job rectangles may use validated integer-minute post-solve windows, while mass
allocation and stockyard state remain bucket-authoritative; see
[`docs/MINUTE_EXECUTION_OVERLAY.md`](docs/MINUTE_EXECUTION_OVERLAY.md).

### Phosphate material and grade profiles

The model separates **process/material state** from **chemical grade** through
`Scenario.material_profiles`, `StockSpace.material_class/profile_id` and
stage-compatible `Grade` metadata. S3 includes Benguerir raw-screened,
filtered-washed and settled-product profiles; Louta mine and washed-product
profiles; and the M16 received humid-washed profile. Benguerir's documented
PSD/moisture/density/repose values are populated. Unknown assays and grade
cut-offs remain explicitly `TBC`. `RAW-BT-GLOBAL`, `RAW-BT-HIGH-CD` and
`RAW-BT-LOW-CD` are a shared Louta/Benguerir raw-grade catalogue; their current
chemistry provenance is the Louta BoD, while site-specific physical properties
and live assays stay separate. `TBT`/`BT`/`MT` are simulation-only
**washed-product** vectors, while `RAW-TBC` has no invented chemistry. The Input Data and Demand & Quality
pages, stockyard monitor, process mimic, CSV/XLSX exports and validation all
expose or enforce this separation.

## Layout

```
app.py               Streamlit entry (sidebar, status bar, issue drawer, 16 pages)
mph_sim/             engine package (UI-independent)
  domain.py          dataclass models + evidence tags        (layer 1)
  validation.py      input validation -> issue list          (layer 2)
  routes.py          directed route graph (real network)     (layer 3)
  optimizer.py       CP-SAT planning engine                  (layer 4)
  simulator.py       discrete-time closed-loop simulation    (layer 5)
  kpis.py            KPI calculation                         (layer 6)
  persistence.py     JSON/CSV/XLSX + SQLite run history      (layer 7)
  inventory_snapshot.py validated CSV/XLSX/JSON t0 inventory import
  adapters.py        ASP adapter interfaces + 8 rules        (layer 9)
  integration.py     three-SMS partition/batches/registry    (layer 9)
  datasets.py        built-in demonstration scenarios
ui/                  presentation layer (layer 8) — no optimisation logic
  hmi_symbols.py     high-performance-HMI vector symbols (pure Plotly)
  layout_geometry.py yard/display geometry CONFIGURATION (evidence-tagged)
  process_mimic.py   DCS/HMI process mimic: view-model + figure + toolbar
tests/               pytest suite
sample_data/         exported scenario snapshots (JSON + CSV)
../generated_outputs/simulator_visual_qa/    visual QA evidence + scenario variants
ARCHITECTURE.md · DATA_DICTIONARY.md · ASSUMPTIONS.md
```

## Determinism

Plans are bit-reproducible: CP-SAT runs single-worker with the stored scenario
seed and a **deterministic-time** budget, warm-started by a complete greedy
solution hint. `sim.max_solve_s` is therefore in deterministic-time units
(≈ seconds; wall time may be 2–5×). Replay = same scenario JSON + same events
→ identical plan, identical simulation (verified by test and by the button on
the Audit page).

## Global Stockyard Layout & Flow (page 15 — Monitor) & same-pile concurrency

Page 15 shows the three phosphate sites as compact but recognisable
stockyard plans — LOUTA (Mine SMS 102MG screened-product storage with two
dedicated stackers + one shared reclaimer; Beneficiation SMS: 110/120LG ore
storage, two washing lines, 130/140LG product storage with the modelled
lanes + `140LG-RC-01/-02`), BENGUERIR (staged view: upstream screened-ore
yard — 2 piles, 2 DEDICATED stackers (`BG-RAW-ST-1/-2`, one per pile) + 1
SHARED reclaimer (`BG-RAW-BWR`, reaches both piles), topology
USER_CONFIRMED 20-Jul-2026 per user-supplied Table 20, tags/positions TBC
→ washing with three lifecycle cards — **Line 1 OPERATING; Lines 2/3 FUTURE**
(numeric display aliases for design-source Trains A/B/C, project-team UI/layout
instruction 22-Jul-2026; commissioning/as-built status evidence TBC) →
downstream B02 washed-product yard with ONE twin-boom `ST3` +
reclaimers `RC2`/`RC3` on separate schematic lines — B01 2023 screening
evidence retained upstream; process stages connected, tag/PFD
reconciliation TBC) and MPH M16 — joined by the C1/C2 double-line corridor conveyors
(flow,
capacity, transport delay, in-transit, next arrival), with an alarm-summary
strip and drill-down buttons into each site detail. **M16 machine placement
follows `Q80130-MZI-M16-ME-PFD-0001[3]` Rev 3 (IFU, 28-Oct-2025): FOUR
two-hangar bays with the machine lines INTERLEAVED between them —
`M16A-A-T06` (ST01-A) · bay 1 · `M16A-A-T11` (RC01-A) · bay 2 ·
`M16A-B-T06` (ST01-B) · bay 3 · `M16A-B-T11` (RC01-B) · bay 4 ·
`M16A-C-T06` (ST01-C). There is no shared T06/T11 corridor and no detached
machine corridor. Each machine serves ONLY its adjacent bays (project-team
rule, 25-Jul-2026): ST01-A→HP-01/05 · RC01-A→HP-01/05/02/06 ·
ST01-B→HP-02/06/03/07 · RC01-B→HP-03/07/04/08 · ST01-C→HP-04/08 —
`reachable_spaces` is never all-to-all, so an unreachable job such as
`ST01-A → HP-08` is rejected by scenario validation and by the ASP
work-area rule. SEQUENCE CONFIRMED; metric positions SCHEMATIC (flow sheet,
not to scale) and hangar names inside a bay INFERRED** (`validate_geometry` enforces rail/pile
adjacency; boom tips stay inside the served pile). **Same-stockpile
stacking and reclaiming are permitted at MPH M16, subject to spatial
separation, work-zone, route, geometry and ACS/PLC constraints. Exact
numerical safety limits remain TBC** (USER-CONFIRMED operating rule,
15-Jul-2026; demonstration separations ASSUMED; no fixed A/B
double-buffering rule; Louta/Benguerir applicability configurable/TBC via
`Site.same_pile_concurrency_allowed`). The optimiser enforces the
conditions (no overlapping zones: reclaim works the low-x face, stacking
the high-x end; no same-bucket pass-through during concurrency; a
conservative capacity-share room rule) and the mimic renders both work
zones, the hatched protected buffer and the separation status
(green/amber/red). Future-expansion items (ST01-D*/RC01-C*) render only
behind a toggle and are never solver inventory: with Future expansion on,
the mimic continues the PFD sequence with bay 5 · `M16A-C-T11`
(M16A-C-RC01) · bay 6 · `M16A-D-T06` (M16A-D-ST01) and four future
125,000 t hangars; hidden, they consume no layout space.

The Benguerir washing-line cards are display/reference geometry only, not
solver assets and not live equipment-state telemetry. Their OPERATING/FUTURE
text is a lifecycle baseline, not a dynamic RUNNING/STOPPED signal. They refer
to beneficiation/washing Trains A/B/C and must not be conflated with the B01
upstream screening/pre-beneficiation lines `TM1`/`TSM2`/`TSM3`.

## Process mimic (page 1 — Executive Overview)

The overview's primary visualization is a **DCS/HMI-style process mimic**
(high-performance HMI palette: light-grey canvas, dark-grey statics, green
only for running flow, amber warning/ASSUMED, red only for trip/fault, blue
selection, dashed = TBC/reference-only). Two levels: an **end-to-end chain
view** (Louta Mine SMS → 102MG screened-product storage → beneficiation SMS →
product-storage abstraction → C1 · Benguerir → C2 · both corridors converge
on M16 with its four two-hangar bays, five distinct machine lines
(M16A-A/B/C-T06 + M16A-A/B-T11), ST01-A/B/C + RC01-A/B, the two separate
`M16A-A/B-H01…H04` clamshell gate banks and the `PAP / DRYING ROUTE
CONFLUENCE — TBC` switchyard ahead of PAP + DRY-FEED) and **detailed yard mimics** per site. Machines are
drawn as travelling stacker / bucket-wheel reclaimer symbols constrained to
their configured rail polylines; boom tips map `position_x` into the served
lane (`POSITION MODELLED — GEOMETRY ASSUMED` — no GA/plot plan exists).
Conveyors are double-line belts with direction chevrons, flow t/h,
utilisation and trip state resolved from real `Scenario.links` (display-only
routes carry `planning_enabled=False` and are never solver resources; the
corrected Louta boundary shows **no direct 102MG → C1 connection**). Benguerir's
three washing-line rectangles likewise stay outside `Scenario.machines`, jobs
and live state resolution; colour, line style and OPERATING/FUTURE text encode
the project-team lifecycle display baseline without implying telemetry.
The quick state switch exposes **Initial state** and **Final plan** directly;
Final plan reads terminal inventory boundary `T` after the last work bucket.
Full engineering controls retain optional plan-bucket inspection and current
simulation actuals (`down_series`/`link_down_series` outage history). Architecture:
`build_process_mimic_state(...)` → immutable view-model →
`process_mimic_figure(view_model, geometry)` → Streamlit toolbar/details.
On Overview and **Stockyards & Flow**, one persistent sidebar control selects
the Initial boundary, an hourly **Plan timeline** from `t=0` through `t=24`,
the Final boundary, or the latest **Current simulation** bucket. The same
selection survives navigation and site-view changes. Plan and simulation
histories are stored only at native bucket boundaries (`Δt=1 h` in the
built-in scenarios), so the UI deliberately does not invent minute-level
interpolation.
Deep links: `?mimic_view=chain|louta|benguerir|m16`,
`?mimic_basis=initial|plan|sim`, `?mimic_bucket=N`.

## Stockyard Monitor (page 3 — Stockyards & Inventory)

Dynamic, data-driven schematics of the three phosphate yards (Louta,
Benguerir, MPH M16). **State views**: *Initial scenario* (Scenario entities),
*Final plan* (terminal `inventory[T]`, no active job at the boundary),
*Selected plan bucket* (plan inventory series + jobs active in the bucket),
and *Current simulation* (per-bucket `inv_series`/`quality_series`/`activity_log`
history — added to the simulator for this monitor, additive only). The bucket
slider replays state without mutating the scenario. **Colour semantics**:
fill = grade (TBT teal · BT amber · MT violet · empty grey · unknown =
deterministic fallback via `grade_color`); border = status (green active ·
amber below safety · red hard violation · grey idle · dashed = ASSUMED/TBC),
duplicated by icons/labels so nothing relies on colour alone. Louta/Benguerir
drawings are labelled **SCHEMATIC — NOT TO SCALE** with TBC banners; the
coverage panel lists report equipment **not modelled** (Louta
`102MG-ST-01/-02` + `110MG-RC-01`, future ST01-D*/RC01-C*) and
audits the scenario against the expected register. Deep links:
`?mon_site=louta|benguerir|m16`, `?mon_basis=initial|plan|sim`,
`?demo=s3w` (below-safety showcase).

## On-spec KPI definition

On-spec % = share of **(destination × bucket) pairs with delivered material**
whose mass-weighted blend quality lies inside every configured limit.
**Zero-delivery buckets are not evaluated** (feed gaps are captured by the
demand-service KPI). **Soft** limits are penalised in the objective (w₂) and
reported transparently — they never block the plan; **hard** limits are
enforced by the optimiser, which leaves demand unmet rather than violate them.
Several limits are ASSUMED/TBC placeholders and are tagged as such in the
per-destination/parameter breakdown (Overview expander + Results & KPIs page).
For realistic use, load separate raw and washed assay distributions and the
site's actual Material/Grade/SQP mappings; live SMS/SQMS pile values supersede
the demonstration vectors.

## Known MVP simplifications

See `ASSUMPTIONS.md` (engine section) — the most important: planning treats
space quality as a constant snapshot per solve (simulation applies
mass-weighted quality updates; a replan refreshes the snapshot); spec
averaging windows are enforced per bucket; the sulphur 8 start-stops/h limit
is sub-bucket and only penalised (w5) at 1 h resolution; link outages are
conservative (unavailable for the remaining horizon) inside a replan snapshot.
Consequently a pile normally carries one dominant/designated grade during a
plan run. Segment-level variation is visible but multi-grade zone allocation
inside one pile is not yet optimised; this must not be presented as a confirmed
operating rule.
