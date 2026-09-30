# Functional Design (Part 1) — Domain Model, Allocation Decision Model & Engineering Basis
### Louta & Benguerir Mine Stockyards → MPH M16 Hub · M17 Sulphur  ·  Phase 1

**Status:** Draft functional design — implemented engineering baseline  **Companion to:** Job‑Allocation Scope note  **Date:** 8 September 2026 (Rev AD)

---

## 1. Domain / Data Model (planning digital twin)

The layer keeps a **planning abstraction**, continuously refreshed from the authoritative SMS/SQMS 3D inventory and machine state, then augmented with demand, routes, recipes and jobs. It never replaces the physical twin. The canonical three-site value chain, system boundary and site assets are defined once in scope §§1, 4 and 13; this part defines only the information and optimisation model.

### 1.1 Entities

| Entity | Key attributes | Source |
|---|---|---|
| **Site** | SMS endpoint, role, spaces and assets | configuration + SMS |
| **StockSpace / Inventory** | capacity/state; material profile; aggregate planning quality; optional grade-distribution metadata; 1-m segment mass, quality and age | SMS/SQMS + planning |
| **MaterialProfile** | process state (`raw_screened`, `washed`, `dry`), physical envelope and evidence | engineering + master data |
| **QualityClass** | stage-compatible chemistry, receiving intervals and anti-contamination matrix | operations master data |
| **Machine** | rate, reachability, rail, travel/setup, separations, calendar, state and position | SMS state + configured capability |
| **RouteGraph** | physical nodes/links/gates, rates, conflicts, transitions, delays and valid paths | PFD/GA + planning |
| **Destination / Demand** | dated grade campaign, physical tonnes/rate, intraday window and priority; several non-overlapping campaigns may target one unit in one day | ERP/operations |
| **Job** | machine, space, type, route, pattern, rate, tonnes, timing and status | planning output |
| **BlendCampaign** | target stockpile, eligible incoming grades, fixed matrix or optimised shares, physical batch tonnes, readiness and execution-lock state (§2.6) | operator configuration + planning output |
| **GradeCostModel** | grade-dependent process consumption and rock value; data not yet received | process engineering/finance |

### 1.2 Route graph (layer‑owned) — versus the ASP "Route"

| ASP “Route” | Route graph owned by this layer |
|---|---|
| `(Device, JobType, free-text Location)`; filters plan-editor choices | Physical nodes/links/gates; capacities/minimum rates; delays/in-transit mass; states, conflicts, transitions, availability and valid source→destination paths |

Every emitted job must pass the physical graph first; ASP validation is a **second gate**. PFD/TBE data define connectivity and rates; GA/plot-plan data are still required for metric travel and transition times.

### 1.3 Entity relationships

```text
MaterialProfile + QualityClass ──type──► StockSpace ──holds──► Inventory
                                           ▲                    │
Machine ──reachability──────────────────────┘                    │
Demand ──targets──► Destination ◄──valid RouteGraph path────────┘
                            ▲
Job = Machine + StockSpace + RouteGraph path + BlendCampaign/Pattern
```

### 1.4 Phase 1 instances (from the scope note)

| Domain / site | Planning role | Solver coverage | Canonical evidence |
|---|---|---|---|
| Louta | 110LG ore stock → L1/L2 recovery → 130/140LG product yard → C1 | **Stocked route solver-backed from 110LG through C1; only 102MG remains reference-only** | Scope §13.1/13.5; evidence §16 |
| Benguerir | Mine storage → raw storage → three washing campaigns → washed-product yard → C2/train | Six stockpiles and nine mobile machines are solver-backed; washing lines are recovery-bearing process stages | Scope §13.3; evidence §16 |
| MPH M16 | Receive, construct humid-product blends, buffer and reclaim → PAP/drying | Current bays, routes and machines modelled; PAP-2 is configurable and Future by default | Scope §13.1; evidence §16 |
| MPH M17 | Open/manual → closed → melting | Domain-B abstraction; open reclaim exogenous | Scope §13.1; evidence §16 |

Exact capacities, assets, statuses and unresolved source conflicts are intentionally not repeated here; §13 is the asset summary and §16 the authoritative evidence register.

---

## 2. Allocation Decision Model

Each planning cycle selects mass flows, assets, routes and timing to meet on-grade demand while respecting the coupled three-site constraints.

### 2.1 Decision variables

| Variable | Meaning | Domain |
|---|---|---|
| `m[mine, c, t]` | mine reclaim rate onto its **dedicated corridor** `c` in bucket *t* (TPH), with quality | ≥ 0 |
| `x[s, d, t]` | reclaim **mass flow** from space *s* to destination *d* in bucket *t* (TPH) — **the primary blend variable** | ≥ 0 |
| `delivered[d, t]` | total feed to destination *d*: `delivered[d,t] = Σ_s x[s,d,t]` (TPH) | derived |
| `unmet[d, t]` | **unmet‑demand slack** for destination *d* (TPH) — see eq. (4) | ≥ 0 |
| `f[s, d, t]` | blend fraction — **derived, reporting only**: `f[s,d,t] = x[s,d,t] / delivered[d,t]` | [0,1], Σ = 1 |
| `a[reclaimer, s, t]` | reclaimer assigned to a space | {0,1} |
| `y[stacker, s, pattern, t]` | stacker building a space with a pattern | {0,1} |
| `b[p, g]` | whole physical tonnes of incoming grade *g* assigned to blend campaign/pile *p* | integer ≥ 0 |
| `ready[p]` | exact minute at which construction of campaign *p* is complete and reclaim may begin | integer minutes |
| `g[gate/route, t]` | gate / route‑path selection (per reclaim stream: which clamshell gate bank routes toward PAP or drying) | {0,1} |
| `seq / start[job]` | job ordering & timing | — |

> **Flow and fraction are not independent.** The model is solved in **mass flows `x`** (linear); blend fractions satisfy the identity `x[s,d,t] = f[s,d,t] · delivered[d,t]` and are recovered after solving. Treating `f` and the delivered rate as independent variables would decouple recipe from throughput and make the quality constraints nonlinear.

### 2.2 Objective — minimise a weighted sum

| Term (weight) | Drives | Priority |
|---|---|---|
| `w₁` unmet demand `Σ unmet[d,t]·Δt` (eq. 4) | **feed continuity** — no starving PAP / molten‑S; **`w₁` dominates all other weights** | highest |
| `w₂` off‑spec deviation | **on‑spec grade** | highest |
| `w₃` downstream process cost *(grade‑dependent)* | Placeholder for governed PAP consumption curves; **inactive (`0`) in the implemented baseline** | inactive |
| `w₄` rock value consumed (give‑away) | don't waste scarce high‑grade rock beyond its economic return | high |
| `w₅` machine moves / changeovers / start‑stops | efficiency, wear | medium |
| `w₆` safety‑stock violation | strategic stock cover | medium |
| `w₇` handled-flow / utilisation reward *(maximise)* | optional throughput/coverage preference; **inactive (`0`) by default** | configurable |

*The active scenario exposes the implemented weights beside the Results KPIs: `w₁` unmet demand, `w₂` off-spec deviation, `w₄` quality give-away/rock value, `w₅` moves/changeovers, `w₆` safety-stock violation and `w₇` utilisation. They are operator-configurable and persisted with the planning assumptions. `w₃` remains inactive until validated GradeCostModel curves are supplied; the interface does not imply an economic optimum that the available data cannot support.*

**S0 Louta objective ruling.** Machine coverage is disabled (`MACHINE_COVERAGE_REWARD=0`), and a small positive cost is applied to every commanded tonne (`FLOW_TONNE_PENALTY=1`). Exact stocked-route constraints therefore create the work; the objective only removes unnecessary excess and tie-driven “token” jobs. This is an **ASSUMED demonstration policy**, not a rule found in either Louta design document.

### 2.3 Dimensional formulation — units, mass balance, quality, delay

**Conventions.** The operator baseline is a civil week: **T = 168 buckets of Δt = 1 h**. An optional rolling-refinement mode uses 15-minute decisions in the first four hours and an hourly tail. Outage, derate, readiness and actual-event boundaries retain their exact minutes; therefore emitted jobs are continuous variable-duration runs and are not forced to one-hour length. Every stockpile capacity, inventory, machine/conveyor/plant rate, demand and scheduled job is expressed in **physical/as-handled tonnes or t/h** at that material stage.

**(1) Space mass balance** — for every space *s* and bucket *t* (dimensionally consistent: tonnes = tonnes + h·t/h):

```
inventory[s, t+1] = inventory[s, t]
                  + Δt · stacked_rate[s, t]
                  − Δt · reclaimed_rate[s, t]              [tonnes]

0 ≤ inventory[s, t] ≤ capacity[s]
reclaimed_rate[s, t] = Σ_d x[s, d, t]
```

*Initial condition:* `inventory[s, 1]` = live SMS snapshot (`SpaceValueResponse`). *Horizon boundary:* terminal stock `inventory[s, T+1]` is held ≥ the safety‑stock target (soft, `w₆`) so the optimiser cannot "win" by emptying the yard at the end of the horizon.

**(2) Delivered flow & blend coupling** — solve in mass flow, report in fractions:

```
delivered[d, t] = Σ_s x[s, d, t]                            [TPH]
x[s, d, t] = f[s, d, t] · delivered[d, t]   (identity; f recovered post-solve)
```

**(3) Mass‑weighted quality** — per destination *d*, parameter *k*, bucket *t* (linear in `x`; enforced **per bucket**, i.e. continuous compliance, not a horizon average):

```
Σ_s q_k[s, t] · x[s, d, t]  ≥  q_k_min[d] · delivered[d, t]     (min specs, e.g. BPL)
Σ_s q_k[s, t] · x[s, d, t]  ≤  q_k_max[d] · delivered[d, t]     (max specs, e.g. Cd)
```

`q_k[s, t]` comes from the planning twin (1‑m segments aggregated over the planned reclaim cut). When stacking modifies a space, its quality updates mass‑weighted: `q_k[s,t+1] = (inventory·q_k + Δt·stacked_rate·q_k_in) / (inventory + Δt·stacked_rate)`.

For an M16 constructed blend, chemistry is weighted by **dry-solids mass** and moisture by physical mass. This avoids treating a wetter tonne as if it contained the same solids as a drier tonne.

**(3a) Stage-aware physical ↔ TSM conversion** — one editable yield/moisture profile is selected per merchant grade; no size-cut variant is inferred:

```
raw_to_TSM       = raw_physical × (1 − raw_moisture) × beneficiation_yield
wet_product      = TSM / (1 − wet_product_moisture)
wet_product_TSM  = wet_product_physical × (1 − wet_product_moisture)
after drying:      1 physical dry tonne = 1 TSM
```

Yield loss occurs through washing/beneficiation; retained water explains why wet-product physical mass remains above TSM. The planning balance never substitutes TSM for the physical capacity or rate of handling equipment.

**(4) Demand with unmet slack** — **no hard `delivered ≥ demand` constraint** (a hard constraint plus a penalty on the same quantity is redundant and makes tight periods infeasible):

```
delivered[d, t] + unmet[d, t] ≥ demand[d, t],    unmet[d, t] ≥ 0
objective += w₁ · Σ_{d,t} unmet[d, t] · Δt       (w₁ ≫ every other weight)
```

The slack guarantees the model stays feasible when demand physically cannot be met; the **dominant penalty** ensures any feasible way of serving demand is always preferred, and the reported `unmet` is an honest feed‑gap signal.

**(5) Corridor transport delay & in‑transit inventory** — with `L_c = round(delay[c]/Δt)` whole buckets (Δt = 1 h → provisional L_C1 ≈ 2, L_C2 ≈ 4; Louta/C1 nearer and Benguerir/C2 farther is user-confirmed; exact metric values are TBC):

```
MPH_arrival[c, t] = mine_dispatch[c, t − L_c]               for t > L_c
MPH_arrival[c, t] = belt_profile₀[c, t]                     for t ≤ L_c
in_transit[c, t+1] = in_transit[c, t] + Δt · (mine_dispatch[c, t] − MPH_arrival[c, t])
```

`belt_profile₀` is the **material already on the belt at the start of the horizon** (mass *and* quality along the belt, from mine‑SMS dispatch history / belt scales) — it arrives during the first `L_c` buckets regardless of any new decision. **Dispatch time and MPH arrival time are distinct**: a mine job dispatched at *t* only becomes MPH stock at *t + L_c*; the quality arriving at MPH at *t* is the quality dispatched at *t − L_c*. Mine jobs must **lead** MPH needs by `L_c`.

**(6) Louta process transfer, recovery and residence** — L1/L2 are transformation stages, not mobile machines:

```text
recovered_output[line] = recovery[case] × ore_feed[line]
WIP[line, t+1] = WIP[line, t] + recovered_output[line, t]
                 − product_stacked[line, t]
product_stack_start ≥ ore_reclaim_finish + 30 min
```

The BT, High‑Cd and Low‑Cd feed/recovery pairs (**584 t/h / 68.22%**, **579 t/h / 68.83%**, **588 t/h / 67.71%**) are documented design cases. The **30-min overall residence is ASSUMED** by the project team; only the scrubber’s 2.5–3 min is confirmed by the BoD and is included within, not added to, that total. Same-bucket passthrough is prohibited. A cumulative matured-mass constraint permits product stacking only after enough washing output has completed its execution window plus the 30-min delay; transient WIP may carry between hourly boundaries and must finish the horizon at 0 t.

### 2.4 Constraints

| Constraint | Rule / formalisation | MPH source |
|---|---|---|
| **Corridor (dedicated)** | Louta only on `C1`, Benguerir only on `C2`; flow ≤ 2600 TPH each | dedicated per mine |
| **Louta S0 purpose targets** | When the stocked-route switch is enabled: exact C1 dispatch = **8,000 t**; exact recovered output = **4,000 t per L1/L2**; exact terminal `110LG-OL1/-OL2` = **123,568.5 t each**, `LT-P1/-P2` = **95,756 t each**, and line WIP = **0 t**. L1 BT→TBT and L2 Low-Cd→BT are **ASSUMED** case/product mappings. | project-team demonstration policy; not in design PDFs |
| **Louta shared paths** | Common RB4/RB5 limited conservatively to **588 t/h** and one ore reclaimer per bucket; unresolved RL3/RL4/C1 confluence permits one product reclaimer per bucket. `130LG-ST-01` uses a **1,000 t/h ASSUMED S0 envelope** because no stacker/SL11 rate is supplied. | process-description topology + conservative model assumptions |
| **Corridor transport lag** | eq. (5): arrival(t) = dispatch(t − L_c); **C1/Louta NEARER, provisional Δ_C1 ≈ 2 h 05; C2/Benguerir FARTHER, provisional Δ_C2 ≈ 4 h 10** (30/60 km @ 4 m/s working values); initial full-rate `belt_profile₀` ≈5,400 / ≈10,800 t. Relative order user-confirmed 22-Jul-2026; exact lengths/speeds/dwell TBC. | project team + provisional 30 / 60 km · 4 m/s |
| **Mine stock** | mine reclaim ≤ mine-yard inventory; mine build follows its plan. Benguerir contention is **stage-internal**: raw outlets share upstream reclaim capacity; washed outlets share RC2/RC3. Raw and washed stages share no piles/reclaimers (see §§13.2, 16). | mine SMS / B02 BoD |
| **End‑to‑end continuity** | mine ship + in‑transit + MPH buffer sustain the MPH→PAP feed over the horizon | chain coupling |
| **Mass balance & capacity** | eq. (1) per space/bucket, with initial + terminal conditions | physical |
| **Blend / grade spec** | eq. (3): mass‑weighted quality per bucket (`Σ q·x ≥ q_min·delivered`, `Σ q·x ≤ q_max·delivered`) | quality specs; Cd for SPH |
| **Machine rate, reach & stability** | `min_stable_rate·run ≤ x ≤ max_rate·run` (semi‑continuous); only **reachable** (machine, space) pairs — machine‑to‑pile reachability from configured **work areas** | machine data / work areas |
| **Machine positioning & setup** | The post-solve minute schedule inserts a separate **non-material** cross-space activity before the next job: estimated translation time (job-range midpoint→midpoint at configured travel speed) + setup time. A **green** bar is an exact reserved minute interval; the machine may reposition early and wait around a blocked calendar. A **red positioning conflict** means no valid interval was reserved and targeted replanning is required; that exact-profile job is withheld from ASP plan-item export until timing validates. Positioning carries no tonnage/grade/path. Blank time is idle/unallocated. First-job positioning and same-space face progression are not separate bars. | current geometry/speed/setup are ASSUMED; obtain plot plan / GA, surveyed common rail frame, live position and operating travel/setup data |
| **Same‑device exclusivity** | a device executes **one job at a time**; no overlapping jobs (ASP ERROR) | ASP Same‑Device Overlap |
| **Reclaimer↔space exclusivity** | a reclaimer serves **one space at a time, movable** (`Σ_s a ≤ 1`) | confirmed movable |
| **Concurrent stack+reclaim on a space** | **PERMITTED at MPH M16 (project‑team confirmation, 15‑Jul‑2026)**: stacking and reclaiming may occur simultaneously on the same stockpile, subject to spatial separation, work‑zone, route, geometry and ACS/PLC constraints; the scheduler prevents collisions and overlapping work zones and must **not** prohibit same‑pile concurrency categorically (no fixed A/B double‑buffering rule). **Exact numerical safety limits remain TBC** (YardMaster/ASP configuration, ACS specification, machine supplier data); applicability at Louta/Benguerir stays configurable/TBC | project team 15‑Jul‑2026 · ASP UC‑28 |
| **Spatial minimum separation** | machines on the same (or, per `CurrentPileOnly` switch, any) pile keep ≥ the **configurable minimum distance** (1‑D X positions) | ASP Spatial Conflict (WARNING) |
| **Maintenance / work calendars** | no job in a global or device‑specific calendar window (recurring entries expanded ≤ 1 year) | ASP Work Calendar (ERROR) |
| **Whole-run and timed controls** | machines, conveyors, washing lines and receiving plants obey their selected lifecycle/availability state and physical rate; timed outage or derate windows split capacity at exact-minute boundaries | operator scenario + contingencies |
| **Routing / gate (graph)** | every job on a **valid source→destination path** of the route graph (§1.2): link capacities, min stable rates, gate states (each clamshell gate bank routes its stream toward PAP or drying — one destination per bank per bucket), **route conflicts / shared conveyors mutually exclusive**, transition times between successive gate states | route graph (layer‑owned) |
| **Anti‑contamination** | incompatible qualities not co‑stacked in a space | compatibility matrix |
| **Constructed blend readiness** | exact grade tonnes close to the campaign batch; eligible-share and receiving-quality intervals hold; target capacity and all incoming routes/resources remain feasible; no reclaim precedes the exact completion minute; executed matrix is immutable on replan | implemented M16 blend campaign |
| **Sulphur (two‑stage)** | open reclaim exogenous (payloader→`CLOSED‑1`); layer runs `RC01`→melting; **start/stop limits** ≤ **8 start‑stops/hr** (lime dosing) | payloader manual; 8/hr |
| **Drying‑feed cap** | humid→dry reclaim ≤ drying‑plant intake capacity | drying plant (OOS) |
| **Demand service** | eq. (4): `delivered + unmet ≥ demand` with dominant `w₁` penalty — **no separate hard feed constraint** | PAP / molten‑S demand |
| **FIFO / material age** *(soft)* | prefer reclaiming older material of the same grade first (stacking‑age per 1‑m segment); avoid degradation of exposed stock | ASP FIFO (INFO) / age analysis |

***Defense in depth:** every constraint marked with an ASP rule is also enforced vendor‑side — plans submitted to ASP are re‑validated (`AspEvaluatePlanResponse`), an independent second gate (§2.7).*

### 2.5 Implemented solve and replanning approach

| Layer | Horizon / cadence | Solves | Implemented method |
|---|---|---|---|
| **Weekly tactical planner** | 7 civil days · 168 × 60 min by default | demand campaigns, physical mass, machines, conveyors, routes, washing transfers, inventory and M16 blend construction | deterministic **OR‑Tools CP‑SAT**, decomposed by civil day with carried inventory and plan hints |
| **Rolling refinement** | optional 15-min first 4 h → hourly tail | the same coupled decisions with finer near-term timing | mixed-resolution CP‑SAT; exact-minute event boundaries retained |
| **Event/replan loop** | on outage, derate, state/rate deviation or job feedback | preserve executed work; regenerate feasible remaining work | replay actuals, lock executed blend composition, update calendars/capacities and re-solve |

Machines, conveyors, washing lines and receiving plants can be Available, Disabled/Future where applicable, or subject to a physical rate cap for the whole run. Exact timed `UNAVAILABLE` and `RATE_CAP` windows model planned or sudden contingencies. Demand is never silently deleted: an unreachable campaign remains visible as unmet demand.

### 2.6 Implemented M16 stockpile-construction blending

The current implemented blend point is **construction of an M16 humid-phosphate stockpile before PAP/drying feed**. Several eligible incoming qualities are stacked into the selected stockpile; only after the campaign is complete may one reclaimer withdraw the homogenised campaign toward PAP‑1, PAP‑2 or DRY‑FEED. The model does not claim a direct simultaneous two-stream reclaim blend.

| Mode | Operator supplies | Planner decides |
|---|---|---|
| **Blend matrix** | fixed physical mass percentages for each eligible grade | feasible routes, equipment, timing and readiness |
| **Optimized shares** | eligible grades and min/max share bounds | integer physical tonnes and percentages **jointly** with inventory, competing stockpiles, routes, machines, calendars, outages, derates and the weekly schedule |

For campaign `p`, the exact construction closure is `Σ_g b[p,g] = batch[p]`; each selected grade respects its share bounds and dry-solids-weighted chemistry must satisfy the receiving interval. Scheduled stacking tonnes for every grade equal `b[p,g]`. Reclaim is prohibited before the exact `ready[p]` minute. Status progresses `NOT_STARTED → BUILDING → COMPLETE → DEPLETED`. A proposed but unexecuted matrix may change on replan; the first positive executed stacking movement locks the physical matrix and replanning preserves it.

A focused 12,000 t verification case produced one feasible incumbent of 1,000 t TBT plus 11,000 t BT‑LOW‑CD and held reclaim until the configured 48-hour readiness boundary. This is **test evidence, not a fixed product recipe**: optimized shares depend on the scenario, quality intervals, stocks and active resource controls. More detailed within-pile layer/zone composition during reclaim, Benguerir washed-product blending to BG‑TRAIN and raw-feed blending before washing remain future extensions.

### 2.7 ASP validation rules — severities, governance & quality limits

The vendor‑side severities matter for how the layer treats the second gate:

| ASP severity | Rules | Vendor behaviour | This layer |
|---|---|---|---|
| **ERROR** *(blocking)* | **Parameter sanity** · **Work area** · **Inventory availability** (mass within StartX–StopX + analysis availability + **most‑prevalent grade** = requested) · **Same‑device overlap** · **Work calendar** | plan item invalid; critical errors raise a notification | modelled as hard constraints (§2.4) — an AUTO item should never draw an ERROR |
| **WARNING** *(non‑blocking)* | **Device availability / busy** · **Spatial proximity** (1‑D min distance, `CurrentPileOnly` switch) | operator may proceed | treated as **hard constraints internally**; **project governance may promote selected ASP WARNINGs to blocking conditions in the external optimiser / dispatch gate** (configuration decision) |
| **INFO** *(advisory)* | **FIFO** (older same‑grade surface material first, incl. other piles) | advisory only | soft objective term (`age/FIFO`, §2.4) |

**ASP quality limitations (why blending is owned here).** For a reclaim item ASP checks inventory **mass**, **analysis availability** and the **most‑prevalent grade within StartX–StopX** — it does **not** prove that a simultaneous multi‑source blend meets **continuous SQP limits**. **One ASP job corresponds to one material grade**; the separate **YardMaster Blending Simulation** may display blend results but **has no role in ASP** (or its validation). The **SQP‑versus‑operator‑defined Sub‑Grade behaviour remains an unresolved ICD/configuration question** (the FRS states SQP is outside ASP, yet Sub‑Grades built on SQP combinations should reflect in ASP). Consequence: all blend/quality optimisation and continuous spec compliance are computed and guaranteed by **this layer**; ASP validates per‑job grade plausibility only.

### 2.8 Execution interface & closed loop — mapping to the vendor ASP (Digimine‑YardMaster)

Our **Job** maps directly onto the vendor's **AspPlanItem** (per FRS Q80130‑MZI‑M16‑TAK‑45000183621‑DG):

| Our model | AspPlanItem field | Our model | AspPlanItem field |
|---|---|---|---|
| Machine | Device | target pile / repère range | Pile + metre-canonical StartX / StopX |
| job type | JobType (Stacking / Reclaiming / …) | stacking pattern | **Method** (e.g. cone‑shell) |
| QualityClass | Material + Grade | gate / conveyor routing | Source / Destination (**ASP Route** — 2nd gate only; real routing validated on the route graph §1.2) |
| quantity + rate | PlannedTonnage + Rate | timing | StartTime (end = start + tonnage/rate) |

The UI and operating plan display `R_start–R_stop`; the adapter converts
those values to physical `StartX/StopX` using the selected pile's approved
R0/R1000 calibration. The optimiser and all distance-based rules continue to
use metres.

**Dispatch loop:** the layer submits plan items (`AspPlanItem` telegram) → ASP validates against its 8 rules and returns `AspEvaluatePlanResponse` (second feasibility gate, §2.7) → jobs are **auto time‑triggered** to YardMaster job management with operator **Accept/Reject** (human‑in‑the‑loop) → **`JobUpdated` telegrams return actuals** → the layer re‑validates and **re‑plans** on deviations.

**Closed‑loop feedback content (`JobUpdated`) and what it drives:**

| Fed back | Used by the layer for |
|---|---|
| **actual status** (job state; Run / Finished / Cancelled …) | job lifecycle: Run sets **actual start**, Finished/Cancelled set **actual end** |
| **actual start / end times** | plan‑vs‑actual deviation tracking |
| **moved mass + material progress** | remaining‑work estimate; inventory reconciliation with the twin |
| **effective rate** (from moved mass) | rate‑shortfall detection; recalibrating job‑duration models |
| **forecast completion** | look‑ahead conflict check for successor jobs on the same device/route |
| **current machine position** (X/Y/Z) | travel‑time estimate for the machine's next assignment |
| **source / target + quality** | verification of executed routing and grade against the plan |

**Deviation → validation → replanning:** each `JobUpdated` republishes the linked `AspPlanItem` and re‑triggers plan evaluation vendor‑side (critical errors raise a notification). Layer‑side, configurable thresholds on **rate shortfall, forecast‑end slip, quality mismatch, cancellation/fault** first fire the **dispatcher** (re‑sequence within the tactical plan); deviations beyond the tactical plan's envelope trigger a **tactical re‑solve**. ASP's Inventory Forecasting (60‑day projection, depletion alerts) and Planning Deviation Report complement the layer's own KPIs.

---

### 2.9 Implemented operator workflow, diagram and results

| Area | Current behaviour |
|---|---|
| **Configure → Grade configuration** | One coherent page with Chemical grades, Yield & moisture, Material profiles and Receiving limits. The grade catalogue is add/delete editable and persisted in `config/grade_catalog.json`; a referenced grade must first be reassigned. User-facing grade names use the canonical BT naming only. |
| **Plan → Demand planning** | One dated weekly table; each plant/unit/day can contain several grade campaigns, each with physical quantity, priority and intraday start/end. Positive windows for the same unit/day may not overlap. |
| **Plan → Blending** | Dedicated page, enabled per user-facing humid stockpile. Only implemented points expose a toggle; current implementation is M16 humid-stockpile construction with matrix or optimized-share mode. |
| **Plan → Routing availability** | Uses the same global flow diagram as Monitor. Clicking a machine, conveyor, washing line or destination sets whole-run state/rate or an exact timed control. The former Planned contingencies page is consolidated here (a compatibility alias only). PAP‑1, PAP‑2 and DRY‑FEED use the same Available/Disabled/Future selector; PAP‑2 defaults Future. |
| **Monitor → global flow** | Site filter All/Louta/Benguerir/MPH; reduced labels; explicit machine/plant/conveyor status and rate. Stockpile fill width is proportional to physical occupancy. Hover exposes component grade, tonnes, quality, capacity/fill, material stage and TSM equivalent when a conversion profile exists. |
| **Monitor → Results / Job Schedule** | Readiness, solver and objective cards; demand/service, quality, inventory/safety, machine/route coverage and contingency/replan results. The job chart has site/machine/operation/status filters plus Full run or exact-day selection. Touching solver slices are coalesced; real gaps, route changes, positioning and exact-minute events start a new run. |

The objective-driver cards show their active scenario weight beside the measure. Current demonstration defaults are:

| Driver | Weight | Results interpretation |
|---|---:|---|
| unmet demand | `w₁ = 1000` | physical tonnes unserved; operational service remains a separate KPI |
| soft quality deviation | `w₂ = 200` | tonne × quality-unit outside a preferred interval |
| downstream process cost | `w₃ = 0` | inactive until validated consumption curves exist |
| BPL give-away / scarce quality use | `w₄ = 5` | tonne × BPL-point above the necessary target |
| activity changes | `w₅ = 2` | machine moves/changeovers/start-stop burden |
| safety-stock shortfall | `w₆ = 20` | tonne-buckets below safety target |
| handled-flow/utilisation reward | `w₇ = 0` | inactive by default; coverage is still reported |

Emergency overflow/unstacked-arrival slack uses a protected dominant penalty; `FLOW_TONNE_PENALTY=1`, `MACHINE_COVERAGE_REWARD=0`, and mixed-resolution active buckets use a default penalty of 25. These are model controls, not business KPI targets. Operational KPIs remain delivered-versus-demand/unmet, on-spec delivery, grade give-away, feed interruptions, stock risk/capacity, equipment and route utilisation/coverage, conflicts, solve status/time, replan count/latency/stability, and physical↔TSM traceability.

## 3. Evidence & Traceability Register

Status vocabulary: **Confirmed** (stated in a referenced document) · **Derived** (computed from confirmed values) · **Inferred** (working hypothesis, confirmation required) · **Conflicting** (two sources / internal inconsistency, preserved) · **TBC** (awaiting document/decision). Confidence H/M/L.

| Site / domain | Parameter / rule | Value [unit] | Source · rev · ref | Status | Conf. | Design impact | Owner / action |
|---|---|---|---|---|---|---|---|
| MPH · M16 | humid open storage | 8 spaces × 125,000 t (→ 12 future) | PRO‑0001 / PFD‑0001 [3] | Confirmed | H | yard capacity model | — |
| MPH · M16 | machine lines, bays & reachability | **FOUR two-hangar bays with the machine lines interleaved between them**: `M16A-A-T06` (M16A-A-ST01, 2,600 TPH) · bay 1 · `M16A-A-T11` (M16A-A-RC01, 2,500 TPH) · bay 2 · `M16A-B-T06` (M16A-B-ST01) · bay 3 · `M16A-B-T11` (M16A-B-RC01) · bay 4 · `M16A-C-T06` (M16A-C-ST01). The mimic draws each bay as **one side-by-side horizontal hangar pair**, with every serving line spanning both members (display ruling 26-Jul-2026). **No shared T06/T11 corridor.** Reach follows the project-team bay-adjacency rule: ST01-A→bay 1 · RC01-A→bays 1-2 · ST01-B→bays 2-3 · RC01-B→bays 3-4 · ST01-C→bay 4 — never all-to-all. FUTURE continues bay 5 · `M16A-C-T11` (M16A-C-RC01) · bay 6 · `M16A-D-T06` (M16A-D-ST01) + 4 × 125,000 t hangars | `Q80130-MZI-M16-ME-PFD-0001[3]` Rev 3, IFU, 28-Oct-2025 + project-team reach rule 25-Jul-2026 + display ruling 26-Jul-2026 | **Machine identities, rates, lines and the bay sequence CONFIRMED**; hangar names and left/right order inside a bay **INFERRED**; metric positions schematic (flow sheet, not to scale); horizontal pairing is geometry-only and changes no solver input | H | machine set, machine lines, `reachable_spaces`, solver candidates, ASP work-area rule and the mimic | plot plan / surveyed GA + official SMS pile-ID register + per-line travel limits |
| MPH · M16 | same‑pile concurrent stack+reclaim | permitted subject to spatial separation, work‑zone, route, geometry and ACS/PLC constraints; numerical safety limits TBC; Louta/Benguerir applicability configurable/TBC | project‑team confirmation, 15‑Jul‑2026 | Confirmed | H | scheduler concurrency rule (§2.4) | obtain numerical separations (YardMaster/ACS/machine supplier) |
| MPH · M16 | PAP / drying take-off | **TWO separate clamshell + rod gate banks**, one per reclaim stream: `M16A-A-H01…H04` and `M16A-B-H01…H04`. Each stream keeps its own `T11` → `T11S` magnetic separator → `T12` (belt scale `T12W`) → gate bank → `T13` outbound belt at 1,500 TPH. PAP is fed by six parallel 1,500 TPH conveyors (3 running + 3 future); the drying unit by two parallel 1,299 TPH conveyors, both OUT OF SCOPE | `Q80130-MZI-M16-ME-PFD-0001[3]` Rev 3 + `Q80130-MZI-M16-PR-PRO-0001` Rev 2 §2.1 | **Gate banks + belt ratings Confirmed** and modelled as the confirmed links `L-H-A`/`L-H-B`; the **destination assignment is DERIVED/TBC** and modelled separately as `L-RTE-{A,B}-{PAP,DRY}` — the earlier '2-of-4: 2→PAP, 2→drying' reading is withdrawn, and confirmed ratings never confirm the assignment. Changeover documented but not modelled | H | route‑graph arcs: `HUB:M16-T13-{A,B}` then one PAP and one drying link per bank, mutually exclusive (DERIVED from the single outbound belt) | **PAP/drying route confluence TBC** + gate→conveyor assignment from the downstream sheets |
| MPH · M16 | PAP feed | ≈1500 TPH/line; Phase 1: one line | PRO‑0001 | Confirmed | H | demand model | — |
| MPH · M16 | dry closed storage | 2 × 50,000 t → rail → SPH | PRO‑0001 / PFD‑0001 | Confirmed | H | dry‑stream buffer | — |
| MPH · M16 | reception analysers | PGNAA (BPL·Cd·MgO·SiO₂·CaO/CO₂) + microwave moisture; autosampler + lab | TEN‑0002 [B] | Confirmed | H | quality inputs | — |
| MPH · M17 | sulphur two‑stage | open 2 × 100,000 t (payloaders ≈300 TPH, manual) → closed 1 × 100,000 t (RC01 1100 TPH) | PRO‑0001 / PFD‑0004 [3] | Confirmed | H | Domain B model | — |
| MPH · M17 | start‑stop limit | ≤ 8 start‑stops/h (lime dosing) | PRO‑0001 | Confirmed | H | dispatch constraint | — |
| Corridors | C1 Louta→MPH | **NEARER (user-confirmed)** · 2600 TPH · provisional ≈30 km / 4 m/s | capacity: TBE/PFD; proximity: project team 22-Jul-2026 | Relative order Confirmed; distance/speed TBC | M | corridor model | obtain TBE/datasheet or surveyed route length + effective speed/dwell |
| Corridors | C1 transit / in‑transit | provisional ≈2 h 05 · ≈5,400 t @ full rate | derived from provisional length/speed | Assumed / Derived | L | lag `L_c` (eq. 5) | replace with measured lag + live belt profile |
| Corridors | C2 Benguerir→MPH | **FARTHER (user-confirmed)** · 2600/3120 TPH · provisional ≈60 km / ≈4 h 10 / ≈10,800 t | capacity: B02 BoD; proximity: project team 22-Jul-2026 | Relative order Confirmed; distance/lag TBC | M | corridor model | obtain TBE/datasheet or surveyed route length + effective speed/dwell |
| Benguerir (B02 washed‑product yard) | stockpiles | 2 × 191,512 t (total 383,024 t) · combined autonomy ≈6.1 d nom / ≈5.1 d design (BoD 5/6 d) | Q80130‑BEN‑B02‑ME‑BOD‑0001 Rev B, IFR, 05‑Feb‑2025 §2.3.3 | Confirmed | H | mine‑yard model | reconcile earlier ≈10‑day datasheet claim (document conflict) |
| Benguerir (3 qualities on 2 piles) | operating rule | in‑pile segregation via SMS 1‑m tracking (assumed) | earlier conveying‑system datasheet (doc ref TBC) — NOT established by the B02 BoD | Inferred | L | blending/inventory model | confirm operating rule (zones / campaigns) |
| Benguerir (B02) | machines | ONE twin‑boom stacker `ST3**` 2100/2520 · reclaimers `RC2**`/`RC3**` 2600/3120 [TPH nom/design] | Q80130‑BEN‑B02‑ME‑BOD‑0001 Rev B, IFR, 05‑Feb‑2025 §2.3.2 + project‑team current‑state instruction 21‑Jul‑2026 | **Current configuration confirmed; `**` is source notation only** | H | current machine set | — |
| Benguerir (B02) | outlets | C2: washed 2600/3120 + **conditional raw** (hopper mod / RB9‑RB10) · train: raw + washed 1000/1200 each (one station — sharing ASSUMED/TBC) | Q80130‑BEN‑B02‑ME‑BOD‑0001 Rev B, IFR, 05‑Feb‑2025 §2.6 | Confirmed; **train planning scope TBC** | H | route model — raw train + conditional raw→C2 may share **upstream raw‑yard reclaim**; washed train + washed→C2 may share the **downstream product‑yard reclaimers**; one shared loading station **ASSUMED/TBC** | confirm train planning scope + raw‑route prerequisites |
| Benguerir (B01 2023 screening — connected upstream stage) | lines & storage | TM1 + TSM2 + TSM3 → common transfer tower `04BG‑DT‑01`; 1 reclaimer `04BG‑RC‑01`; stacker 2200 TPH nom; direct beneficiation bypass (mode deferred) | REP‑0002 Rev B, 26‑Jul‑2023 (IFR) | Confirmed (PFDs indicative; vendor simulation pending) | M | connected upstream stage — tag mapping to B02 unproven, registers kept separate | reconcile B01↔B02 via PFD‑0010/0011 + DBD‑0001 |
| Benguerir (Table 20 — upstream screened-product ore storage) | topology, machines & starting state | 2 stockpiles × 200,000 t; 100,000 t assumed t0 each; 2 dedicated stackers plus 1 shared reclaimer; physical/as-handled rates 2,100/2,500 t/h | User-supplied Table 20 + project-team topology and starting-state instructions | Topology/capacity USER_CONFIRMED; t0 is an assumption rather than timestamped telemetry | H/M | **BG-MINE-1/2 are solver-backed**; source package still marks the reclaimer out of its scope, so operational authority remains TBC | obtain timestamped SMS stock/grade, source document revision, tags and GA |
| Benguerir upstream raw piles | shared raw grades + assumed t0 composition | `RAW-BT`, `RAW-BT-HIGH-CD` and `RAW-BT-LOW-CD` are mine-neutral. Demonstration t0: BG-RAW-1 = 50% BT + 50% High-Cd; BG-RAW-2 = 50% BT + 50% Low-Cd, 100,000 t per pile. | Louta BoD chemistry proxy + project-team scenario assumptions | Grade identities configured; composition and chemistry are ASSUMED, not live Benguerir assays | M | **BG-RAW-1/2, their stackers/shared reclaimer, inventories and jobs are solver-backed**; quality participates where a profile exists | replace with SQMS segments, dated assays and approved grade/receiving intervals |
| Louta 102MG mine storage | assumed demonstration baseline | `102MG-SP-01/-02`: **100,000 t assumed capacity each; 50,000 t t0 each (50%)**. SP-01 = 25,000 t BT + 25,000 t High-Cd; SP-02 = 25,000 t BT + 25,000 t Low-Cd. Components occupy schematic display ranges R0–R250 and R250–R500; R500–R1000 is empty. | Project-team demonstration-baseline instruction, 26-Jul-2026; chemistry vectors from `Q80130-LOU-L02-PR-BOD-0001` Rev B §6.1 | **S3-A25 · ASSUMED display values.** Actual capacity, live mass, grade boundaries/order and route/rate/reach remain TBC. The documented **494,274 t belongs exclusively to 110/120LG**, not 102MG. Neither Louta PDF contains `102MG` or a 102MG→110LG route. | M | **reference only**; absent from `Scenario.spaces`/machines and excluded from inventory, jobs, material balance and KPIs; `MODELLING_GAP[MISSING_ROUTE_RATE_REACH]` | obtain capacity schedule, timestamped SMS/SQMS segments, surveyed repères, complete route/rate and machine reach matrix |
| Louta 110/120LG homogenized ore storage | topology, machines, capacity + starting state | **2 homogenized stockpiles** formed by chevron stacking · **1 shared ore stacker `110LG-ST-01`** 2,100 TPH wet · **2 bridge reclaimers `120LG-RC-01/-02`** 3,000 TPH wet each · common RB4/RB5 to L1/L2 · total **494,274 t** → **2 × 247,137 t DERIVED** · starting and exact S0 terminal mass **123,568.5 t each ASSUMED** · displayed bands OL1 = 50% BT + 50% High-Cd, OL2 = 50% BT + 50% Low-Cd | Machines/topology: Louta Process description Rev B (IFA), 25-Apr-2023, §3.1 pp. 7–8; total: Louta BoD Rev B; profiles: BoD §6.1. **Document-control inconsistency:** cover `REP-00002` vs body `REP-00004`. | Counts/topology/rates CONFIRMED in the tagged description; equal capacity split, start/terminal stocks, bands and one-reclaimer-per-pile pairing ASSUMED/DERIVED. **Count conflict:** BoD p.20 says one ore reclaimer; tagged description says two; tagged configuration used. | M | `110LG-OL1/-OL2`, `110LG-ST-01` and `120LG-RC-01/-02` are **solver-backed**; bands remain display metadata, not selectable zones | obtain live masses/assays, pile capacity schedule, reach/GA, common-route capacity/concurrency and approved ore campaigns |
| Louta washing / beneficiation | line configuration, cases, recovery + residence | **2 parallel lines L1/L2, 3 Mt tsm/year each**; BT **584 t/h / 68.22%**, High-Cd **579 / 68.83%**, Low-Cd **588 / 67.71%**; scrubber **2.5–3 min CONFIRMED**; overall line **30 min ASSUMED** | Lines/cases: Louta Process description Rev B p.5; case feed/recovery: Louta BoD Rev B; scrubber: BoD §8.2.1 p.21; overall residence: project-team instruction 26-Jul-2026 | Case data and scrubber residence CONFIRMED design values; 30 min and S0 BT→L1→TBT / Low-Cd→L2→BT mapping **ASSUMED**. PDFs do not map cases to TBT/BT/MT. | M | **solver-backed process-transfer stages** with mass yield, no same-bucket passthrough, cumulative matured-mass availability and terminal WIP = 0 t; lines are not `Machine` objects | validate residence from timestamps; obtain availability, approved case/product campaign map and shared-tank/filter concurrency rules |
| Louta 130/140LG product handling | stack/reclaim topology + S0 modelling envelope | One `130LG-ST-01` on SL11; two `140LG-RC-01/-02` at 3,000 TPH; RL1/RL2 → flip gates → RL3/RL4 → outbound `BL`. S0 uses a **1,000 TPH ASSUMED stacker envelope** and conservative one-reclaimer-per-planning-line / one-at-confluence rules. | Louta Process description Rev B §3.1 p.8; S0 envelope and pairings: project-team model assumptions | Tagged machine configuration CONFIRMED; rate/pairing/confluence assumptions TBC. **Count conflict:** BoD p.20 says one product reclaimer; tagged description says two. Neither PDF labels the outbound as `C1`; that binding is project evidence. | M | `130LG-ST-01`, `140LG-RC-01/-02`, LT-P1/P2 and C1 flow are **solver-backed**; 140LG machine bars are labelled by their physical **reclaim** operation, while C1 remains the target/purpose. The former dispatch color is used for separate non-material positioning. | obtain product arrangement, stacker/SL11 rating, machine reach, confluence capacity and live SMS/SQMS state |
| Louta S0 stocked-route policy | explicit purpose targets and source boundary | Exact: C1 **8,000 t**; recovered L1/L2 **4,000 t each**; terminal OL1/OL2 **123,568.5 t each**; terminal LT-P1/P2 **95,756 t each**; WIP **0 t**. Two **10,000 t ASSUMED mine-handoff** sources feed 110LG and are not 102MG. `MACHINE_COVERAGE_REWARD=0`; flow-tonne penalty = 1. | Project-team demonstration policy, 28-Jul-2026; implementation configuration | **ASSUMED policy**, not a source-document operating requirement. It exists because opening product stock can otherwise satisfy demand with legitimate upstream idleness. Direct ore/product storage bypasses are documented. | M | every S0 job has a corridor, process-output or terminal-stock purpose; no coverage/token jobs; switch-off returns demand-only mode | replace with approved source allocation, end-stock, bypass and campaign policy |
| Louta machine disposition | selected solver machines versus evidence gaps | Structured result per `110LG-ST-01`, `120LG-RC-01/-02`, `130LG-ST-01`, `140LG-RC-01/-02`: scheduled, unavailable, no source inventory, no target/free capacity, missing route, shared-route peer selected, or target met without machine. PDFs do not call either pair duty/standby. | Solver result semantics + Louta source review | Model behaviour; disposition is explanatory, not evidence of operating duty | M | explains justified work or legitimate idleness without manufacturing activity | connect live availability, routes, inventories and operating duty rules |
| Louta L02 product piles | starting inventory + grade distribution | Capacity **191,512 t each**; starting mass **95,756 t each (50%)**. **LT-P1 = 60% TBT + 40% BT** → BPL 71.6% · Cd 25.2 ppm · MgO 0.92% · SiO₂ 2.32% · moisture 12%. **LT-P2 = 60% BT + 40% MT** → BPL 66.0% · Cd 32.0 ppm · MgO 1.22% · SiO₂ 3.08% · moisture 12%. Yard aggregate = 30% TBT / 50% BT / 20% MT. | Capacity derived as equal split of documented 383,024 t total; starting state and distribution: project-team demonstration instruction 24-Jul-2026 | **ASSUMED distribution.** TBT/BT/MT are washed demonstration vectors; Louta operating mapping/cut-offs TBC. Bands are mass shares, not spatial order. The shared raw `RAW-BT-*` grades stay a separate material state and are never applied to these washed-product lines. | M | solver uses each pile's mass-weighted aggregate `StockSpace.quality`; no profile-selective reclaim or zone allocation | replace with timestamped pile/segment masses, surveyed boundaries, active reclaim face and Grade/SQP mapping |
| Louta L02 | combined product specification | BPL 68.29% · MgO <0.6% · CO₂ 6.81% · SiO₂ <4% · Cd 17 ppm · moisture <22% · SG 3 | `Q80130-LOU-L02-PR-BOD-0001` Rev B, 12-Apr-2023, p. 18 | Documented design specification; retained under the source's own stage wording, **not relabelled as washed-only** | H | reference only; does not replace TBT/BT/MT demonstration vectors | confirm operating product grade/SQP IDs, acceptance limits and sampling basis |
| Benguerir Table 20 ↔ B02 | reclaimer capacity conflict | Table 20: 2,100/2,500 TPH humid vs B02 expansion interface: 2,600/3,120 TPH — whether B02 upgrades, replaces or supplements the Table 20 reclaimer is TBC | Table 20 vs Q80130‑BEN‑B02‑ME‑BOD‑0001 Rev B §2.3.2 | **Conflicting — not resolved by inference** | M | raw→C2 demonstration at 2,600 TPH assumes the B02 interface selected and the Table 20 bottleneck resolved | equipment list / PFD proving equivalence or supersession |
| Benguerir (cfg A) | stream rates | line feed 1300 · primary undersize 1208 · secondary feeds 2 × 604 · crusher 541 · oversize reject 89 · product handling 1055 [TPH] | REP‑0002 Rev B | Confirmed in doc | M | none yet (out of model) | — |
| Benguerir (cfg A) | reject total | 541 + 89 = **630** vs stated **628** TPH | REP‑0002 Rev B | **Conflicting** | — | preserved, not normalised | resolve with vendor |
| Benguerir (cfg A) | undersize vs handling | **1208** TPH screen undersize vs **1055** TPH product‑handling capacity | REP‑0002 Rev B | **Conflicting / unexplained** | — | possible surge / split logic unknown | resolve flow basis |
| Benguerir B01↔B02 | process relationship | raw/screened mine storage → washing/filtration/settling → washed‑product storage → C2/train (+conditional raw→C2 · raw/washed→train) | Q80130‑BEN‑B02‑ME‑BOD‑0001 Rev B, IFR, 05‑Feb‑2025 §2.6 | **Confirmed at stage level; exact phase/tag/PFD reconciliation TBC** | H | staged Benguerir model | obtain PFD‑0010/0011 + BFD DBD‑0001 (tag mapping) |
| Benguerir washing lines | planning lifecycle and rate controls | `BEN-WASH-L1/L2/L3` are executable recovery-bearing stages with scenario-configurable state and physical rate | Project-team model baseline through 8-Sep-2026; operating/as-built status not independently evidenced | Implemented planning resources; live status remains TBC | M | all three can create feed/recovery/stacking work and obey whole-run or timed outage/derate controls | obtain current commissioning status, canonical tags, capacities and effective dates |
| Benguerir washing / beneficiation | yield, moisture and residence | 3 configurable washing campaigns; scrubber 3 min CONFIRMED; overall line 30 min ASSUMED; editable grade yield/raw moisture/wet-product moisture | `Q80130-BEN-B02-PR-BOD-0001` Rev 0 + project-team conversion/residence assumptions | Mixed evidence; conversion defaults are visible scenario assumptions | M | **solver-backed recovery-bearing process transfers**; yield reduces dry product mass and residence delays product availability | validate campaign mapping, yield, moisture and overall residence from operating data |
| Louta | stockpiles | `MINE STOCKPILE‑01` / `‑02` (colored base arrangement) | cropped layout drawing (doc no./rev/title block TBC) | Confirmed visible | M | 2 piles exist; nothing quantitative derived | obtain full drawing + legend |
| Louta | equipment tags | `102MG‑ST‑01` · `102MG‑TR‑06` · `CP‑07` · `CP‑08` · `CP‑10` · `CP‑11` | cropped layout drawing | Confirmed visible; functions/connectivity TBC | M | no capacities/machine counts/routes derived from the crop | machine‑to‑pile / route matrix |
| Louta | `TO DIGUE (OPTION‑2)` grey layout | separate option incl. RAW/PRODUCT STOCKPILE BENEFICIATION‑01/‑02 | cropped layout drawing | Option — **excluded from Phase‑1 baseline** | — | not in model | confirm selected option |
| MPH SMS · ASP | route semantics | `AspRoute` = (Device, JobType, free‑text Location) only — no network model | FRS §3.1 | Confirmed | H | layer owns route graph (§1.2) | ICD |
| MPH SMS · ASP | rule severities | 5 ERROR · 2 WARNING · 1 INFO (§2.7) | FRS UC‑22…30 | Confirmed | H | validation‑gate design; warning promotion | governance decision |
| MPH SMS · ASP | quality model | 1 job = 1 grade; SQP outside ASP; Blending Simulation separate ("no role in ASP"); Sub‑Grade behaviour unresolved | FRS Q&A | Confirmed / **Unresolved (SQP/Sub‑Grade)** | M | blending owned by this layer | ICD / configuration question |
| MPH SMS · ASP | inventory cache | per‑pile 1‑m X segments (mass · prevalent grade · age), ~10‑min or on‑demand refresh | FRS | Confirmed | H | twin sync design | — |
| All stockpiles · SMS/ASP | longitudinal job location / repère | Operator scale **R0–R1000 = 1,000 equal intervals**; jobs use a range such as **R400–R600**. Physical X metres remain canonical and are calculated per pile. | Start/End positions and 1‑m inventory: Innomotics FRS Table 26, UC‑22/23/24/26/28/30 and §3.7.1; repère name/scale: project team 24‑Jul‑2026 | **FRS position model Confirmed; repère convention User-confirmed; physical calibration TBC** | H/M | job authoring, grade placement, machine display, inventory selection and spatial checks | obtain each pile's R0 endpoint/direction, surveyed origin/usable length and production ICD StartX/StopX units/scale |
| Simulator mimic | Initial / Final state and planning controls | **Initial** = scenario t0; **Final** = terminal weekly inventory. Physical fill is proportional; hover exposes component mass/quality. Louta 102MG remains reference-only; the six Benguerir piles and Louta 110LG/product spaces are solver-backed. | Project-team UI instructions and implementation through 8-Sep-2026 | Implemented engineering behaviour; scenario values are not live telemetry | M | shared Monitor/Routing diagram, state/rate controls and terminal-plan inspection | validate against live SMS/SQMS and surveyed geometry |
| Interfaces | RabbitMQ telegram schemas | `AspPlanItem` / `AspEvaluatePlanResponse` / `JobUpdated` / `SpaceValueRequest/Response` — ICD not yet received | FRS (hypothesis H1) | TBC | — | integration design | request ICD (vendor workshop) |
| Economics | GradeCostModel curves | PAP specific consumptions vs feed grade + rock values per quality | — | **Required, not received** | — | w₃/w₄ objective terms | process engineering / finance |

## 4. Simulation Readiness

What the allocation/simulation model needs, by readiness class:

| Class | Items |
|---|---|
| **Implemented engineering MVP** | deterministic weekly CP‑SAT planning; dated multi-campaign demand; physical inventory/rate constraints; washing yield/moisture conversion; editable grade catalogue and receiving intervals; timed equipment/conveyor/line controls; global status diagram; results/KPI and day-filtered job schedule; M16 fixed/optimized pile-construction blending. |
| **Configurable assumptions** (visible and editable) | travel/setup/minimum separation · weekly demand campaigns · initial inventory and pile distributions · yield/moisture profiles · chemistry vectors and receiving intervals · work calendars · equipment/routing states and physical rate caps · objective weights. Demonstration B02 defaults are MT 75.73%, BT 66.63%, TBT 62.64%, raw moisture 12% and wet-product moisture 17.5%; these are assumptions, not live measurements. |
| **Required before a realistic operational simulation** | timestamped SMS/SQMS stocks and assays · validated grade-class intervals and raw↔washed mappings · per-pile repère calibration and metric GA · route confluences/capacities, reach and transition data · current machine/conveyor/plant state and calendars · validated washing residence/yield/moisture by campaign · drying intake · approved operating contingencies and a real GradeCostModel. More detailed within-pile layer/zone reclaim composition and the two non-M16 blend points are not implemented. |
| **Required only before live SMS integration** | RabbitMQ ICD / telegram schemas + credentials (H1), including confirmed StartX/StopX units, axis origin/orientation and scale · site‑specific mine‑SMS configuration (H2 **resolved at solution level — identical Innomotics/YardMaster SMS at all three sites, user‑confirmed**; per‑site endpoints, queue/exchange names, Device/Pile IDs, Material/Grade/SubGrade mappings, AspRoutes and permissions remain TBC) · plan co‑authoring governance & write access (H3) · final configured Material/Grade/SQP/Sub‑Grade behaviour · LIMS/CMS/ERP endpoints · OT/IT & cybersecurity engineering |

**Rev AC verification history (8 September 2026).** The focused blending/integration suite reports **61 passing tests**. A real seven-day default solve returned FEASIBLE with exact 12,000 t blend construction and no reclaim before the 48-hour boundary. The broader repository run reported **755 passes and 55 legacy/baseline failures**; therefore this report does not claim that every historical UI/report assertion is green.

## 5. Acceptance Criteria (measurable)

Numeric targets marked TBC are to be fixed at Phase‑1 design review; the *measures* are fixed now so every criterion is testable in simulation before live integration.

| # | Criterion | Measure | Target |
|---|---|---|---|
| 1 | No impossible route allocations | jobs dispatched whose path is absent from the route graph's valid‑path set | **0** |
| 2 | No machine double booking | overlapping jobs per device (ASP Same‑Device‑Overlap ERRORs on AUTO items) | **0** |
| 3 | Inventory conservation | per‑space physical mass-balance closure over the full 168-hour week, including washing conversion and carried WIP | ≤ ε % drift (TBC) |
| 4 | Destination quality compliance | % of buckets with delivered mass‑weighted quality within spec (eq. 3), per destination | ≥ target % (TBC) |
| 5 | Demand service | `Σ delivered / Σ demand` per destination; `unmet` tracked per bucket | ≥ target % (TBC) |
| 6 | Safety‑stock compliance | % of horizon with `inventory ≥ safety level`, per space class | ≥ target % (TBC) |
| 7 | Solve time | tactical optimiser wall‑clock per rolling solve | ≤ limit (TBC, order of minutes) |
| 8 | Replanning latency | event (JobUpdated deviation / fault) → new dispatched plan | ≤ limit (TBC, order of seconds–minutes) |
| 9 | ASP validation success | AUTO plan items passing ASP with **zero ERRORs** at first submission | ≥ target % (TBC) |
| 10 | Operator override preservation | human‑authored/modified items never edited or reverted by the layer | **100 %** |
| 11 | Deterministic replay | re‑running a stored scenario (inputs + config + seed) reproduces the identical plan | **exact match** |
| 12 | Multi-campaign daily demand | all same-unit/day campaign quantities, windows and grades survive input→solve→result reconciliation | **exact conservation; no positive-window overlap** |
| 13 | Timed contingency response | outage/derate at a non-grid minute changes no work before the boundary and produces no use above availability/rate after it | **0 violations** |
| 14 | Blend construction integrity | scheduled grade tonnes equal selected matrix, total equals batch, all bounds/quality intervals hold and no reclaim starts before exact readiness | **exact integer-tonne closure; 0 early reclaim** |
| 15 | Stage mass traceability | physical raw, TSM and wet-product values reconcile with the selected yield/moisture profile | **within configured numeric tolerance** |

---
*Companion to the Job‑Allocation Scope note. Next functional‑design parts (proposed): closed‑loop workflow + job lifecycle; interface data contracts (job & feedback schemas).*
