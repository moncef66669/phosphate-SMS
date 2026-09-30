# MPH — Automated Job Allocation Layer for the SMS — Project Report
### Louta & Benguerir Mine Stockyards → MPH M16 Hub · M17 Sulphur — SP2M Program

**Team:** Moncef Driouich  **Status:** Draft — Scope & Functional Design  **Rev.:** AF  **Date:** 28 September 2026

| Rev. | Date | Scope of revision | Status |
|---|---|---|---|
| A-G | 22 Jun-19 Jul 2026 | Earlier scope, functional-design, interface, evidence and B02 development | Superseded - see source history |
| AB | 28 Jul 2026 | Raw grades made mine-neutral and shared by Louta and Benguerir: RAW-TBC plus RAW-BT, RAW-BT-HIGH-CD and RAW-BT-LOW-CD. Site material profiles retain separate moisture, PSD and live-assay metadata; legacy grade IDs remain import-compatible | Draft — for analysis |
| AC | 08 Sep 2026 | Implemented weekly multi-site planning, dated multi-campaign demand, stage-aware physical/TSM mass conversion, persistent grade and yield configuration, consolidated timed resource controls, explicit results and M16 pile-construction blending with fixed or jointly optimised shares | Draft — for analysis |
| AD | 08 Sep 2026 | Recovered the report-aligned app; hardened blend execution, mass and assay accounting, timing dependencies, campaign priorities and demand editing; added copied stress scenarios, corrected outcome KPIs and reproducible local-SMS versus coordinated business-case experiments | Draft — for analysis |
| AE | 23 Sep 2026 | Specified the SMS job-emission contract from the Innomotics FRS: the closed six-value job-type catalogue with per-type mandatory fields, the full plan-item attribute set, derived duration and type-aware actuals (§§15.8.1–15.8.2); defined `Layer` as the vertical pile coordinate and FIFO basis, fixed job scope at one pile per item, established that `Layer` binds the deposited lift on Stacking but reads as an age/quality key on Reclaiming, recorded the absent transverse (Y) selector that blocks selective within-layer withdrawal, and raised `Layer` writability as an open ICD question since no plan-editor form collects it (§5.1.1); established that `Method` is mandatory on both emitted types but un-enumerated in the FRS, making the per-device catalogues a blocking prerequisite to emission, and separated blending mode from `JobSubType`; and identified that Transport and Scanning occupy devices as plan items rather than calendar entries and are not yet ingested — a single type-agnostic input path, with no scan-specific logic required elsewhere (§15.8.1); added ten evidence rows | Draft — for analysis |
| AF | 28 Sep 2026 | **Model/report divergences corrected and the scenario rebased to plant duty.** Withdrew the shared-reception-path claim (§13.2) — never implemented; C1 and C2 are independent 2,600 TPH links giving 5,200 TPH. Corrected the inverted TBT/BT/MT quality table and added the missing CO₂ assay across all twelve washed grades (§13.4) — without it nothing either mine produced could meet PAP's G10 contract, so PAP was servable only from opening stock. Recorded that `service_first` discards the entire weighted objective block, that destination priority is a weight where higher wins (PAP was the lowest-ranked receiver), and that corridor dispatch targets carry no weight in that mode (§15.2). Withdrew the Louta stocked-route pins, set both washing plants to daily operation, and cleared the spurious 500 t/h machine flow floor that was the blending rule mis-encoded (§15.4). Rescaled demand to duty: PAP 5,500 t/day, drying 25,600 t G10 + 3,500 t precursors, train 2,300 t/day; M16 opening stock 424,000 → 86,500 t. Added §12.1 solve mode, making acceptance #11 attainable. **Regression suite not yet re-run — see §17.** | Draft — for analysis |

# PART I — PROJECT SCOPE

---

## 1. Context

The phosphate planning boundary is the complete **three-site value chain**, not M16 alone. Louta, Benguerir and MPH are peer controlled yards in **Domain A**; each has an SMS and receives one coordinated **seven-day plan containing hourly jobs**. An optional rolling mode refines the first four hours to 15-minute decisions and retains an hourly tail; timed asset changes and issued jobs keep exact-minute boundaries. Mining and washing upstream of the stockyards are interfaces, not directly controlled plant processes; the S0 simulator nevertheless represents selected Louta and Benguerir campaigns as auditable process-transfer stages so that ore placement, reclaim, recovery, product stacking and corridor/train withdrawals conserve mass. **Domain B** plans M17 sulphur independently on the same platform.

```text
LOUTA washed-product yard ── C1 · NEARER · provisional ≈2 h* ────────────────┐
                                                                  ▼
                                                        ┌─────────────────┐──► PAP
BENGUERIR screened yard → washing → B02 washed yard ───►│ MPH M16 humid   │
                         C2 · FARTHER · provisional ≈4 h*       │ receive · blend │──► Drying → rail → SPH
                                                        └─────────────────┘

M17 open store ── manual payloaders ──► closed store ── RC01 ──► melting
                                  independent Domain B
```

`*` **Relative proximity is user-confirmed (project team, 22-Jul-2026).** The
30/60 km reassignment and resulting 2/4-hour lags are provisional modelling
values; exact conveyor route lengths, effective speeds and transfer dwell
remain TBC.

| Site / stage | Planning role / output | Principal open evidence |
|---|---|---|
| **Louta beneficiation stock route · SMS** | 110LG ore storage → modelled L1/L2 transfer → 130/140LG product reclaim → **C1 → M16**; 102MG remains reference-only | Full GA/reach and route matrix; live stocks/assays; 102MG→110LG route/rate; approved case-to-product campaign map |
| **Benguerir six-pile chain · SMS** | 3 mine hoppers → 2 mine-ore piles → 2 screened/raw piles → 3 washing campaigns → 2 B02 product piles → **C2 → M16 / train** | Physical tags and GA positions; approved Benguerir line rates/recoveries, grade mapping and train orders |
| **MPH M16 · SMS** | Receive, blend and reclaim → PAP or drying | Final GA, route confluences and metric separation limits |
| **MPH M17 · SMS** | Independently plan closed-store feed → melting | Demand/calendars; open reclaim stays manual |

**Phase 1 / expansion:** M16 starts with PAP-1, 8 humid spaces and 3+2 machines. PAP-2 is already represented with the same demand template and the three explicit lifecycle states **Available / Disabled / Future**; its default is **Future**. Configuration supports N PAP lines, 12 spaces and 4+3 machines. M17 closed storage expands from one to two stores and adds RC02. Expansion is configuration, not redesign. Mobile stackers/reclaimers appear to be a separate machine package from the three solid-handling lots; final package responsibility remains TBC.

**System boundary:** YardMaster/SMS maintains the 3D stock model and executes physical jobs. Its ASP module supports manual plan authoring, rule validation and actuals tracking, but does not optimise demand, blends or multiple yards. This project is the **automated PlanAuthor above the three SMS**; ASP remains the vendor validation/execution bridge (§§14.2, 15.7–15.8).

## 2. Project Objective & Expected Impact

### 2.1 Objective

Turn approved dated demand and live yard state into an operator-supervised weekly rolling plan that coordinates quality, inventory, machines, conveyors, process lines, routes and corridor delays across Louta, Benguerir and M16; sulphur is solved separately. Each receiving unit may request several grade/quantity campaigns on the same day, with an intraday window and priority for each campaign.

```text
Approved plan + live state
          ▼
Optimise quality · inventory · machines · routes
          ▼
Layer checks → ASP validation → Operator Accept / Reject
          ▼
YardMaster execution → actuals / inventory / quality / status
          └──────────────────────────────► event-driven replan
```

### 2.2 Comparable OCP evidence — the planning loss already exists

The 2024 Khouribga Sidi-Chennane–Daoui diagnostic shows the same class of planning loss with only **2 stackers + 2 reclaimers**:

| Planning-sensitive loss | Relative recorded hours | Value |
|---|---|---:|
| Translation / orientation | ██████████████████████████████ | **108.1 h** |
| Waiting for stock formation | █████████ | **32.1 h** |
| Stock full | █████████ | **31.5 h** |
| Feed-axis changes | ██████ | **21.3 h** |
| Insufficient conforming product | █ | **5.0 h** |

| Additional diagnostic signal | Recorded value | Interpretation |
|---|---:|---|
| Changes of reference | **43 events** | Campaign instability |
| TRG loss | **1.22 points** | Observed utilisation impact |
| Coordinated planning lever | **0.32 MTSM potential** | Identified opportunity, not an achieved result |

The evidence establishes an existing OCP problem of the same class. It is **not an MPH forecast**: categories may overlap, Louta fleet data remain incomplete, and benefits must not be scaled linearly by machine count. Quantification requires de-overlapped historical replay, shadow mode and a supervised pilot.

### 2.3 Impact KPI scorecard

Operational targets remain **TBC** until an approved baseline, replay and pilot. Simulation may test **25% / 50% / 75%** recovery only on loss proven planning-addressable.

The Monitor makes these results explicit and shows the active objective weights beside the run. The current configurable objective includes unmet demand, shortage/safety exposure, movement, positioning, switching and quality deviation; the economic grade-cost term remains inactive until governed cost data exist. Weight values are scenario settings, not engineering facts, and the run stores them for reproducibility.

| Area | KPI | Measure | Direction / fixed rule |
|---|---|---|---|
| Operations | Demand service | `Σ min(delivered,demand) / Σ demand` | ↑ · target TBC |
|  | Feed interruption | demand > 0 and delivery = 0 [h] | ↓ · target TBC |
|  | Corridor adherence | tonnes inside time/rate/quality window | ↑ · target TBC |
| Quality | On-spec delivered mass | on-spec / delivered tonnes | ↑ · target TBC |
|  | Off-spec exposure | off-spec tonnes + weighted deviation | ↓ · target TBC |
|  | Quality give-away | weighted excess above target | ↓ · target TBC |
| Inventory/assets | Reconciliation drift | planning twin − SMS inventory | ↓ · tolerance TBC |
|  | Inventory bounds | time inside safety/capacity limits | ↑ · overcapacity **0** |
|  | Planning-addressable loss | de-overlapped hours + moves/starts per kt | ↓ · 25/50/75% sensitivity only |
| Planning | First-pass ASP success | AUTO items with zero ASP `ERROR`s | ↑ · target TBC |
|  | Automation coverage | accepted AUTO / eligible decisions | ↑ · target TBC |
|  | Routine effort | operator authoring minutes / shift | ↓ · target TBC |
| Resilience | Replan latency | event → valid replacement plan | ↓ · limit TBC |
|  | Service recovery | disruption → restored requested feed | ↓ · target TBC |
| Governance | Schedule integrity | invalid route/double-book/spatial conflict | **0** |
|  | Human-plan preservation | human items changed by automation | **0 / 100% preserved** |
|  | Deterministic replay | same stored inputs/config/seed | **exact match** |
| Integration | SMS ownership integrity | unmapped or wrong-SMS jobs | **0** |

**Claim boundary:** baseline → offline replay → shadow mode → supervised pilot → sustained/economic validation. No throughput, financial, energy, staffing, reliability or safety benefit is claimed before pilot evidence. Formal acceptance measures are in §18.

## 3. Where this Layer Sits

| Tier | Owner | Responsibility |
|---|---|---|
| Weekly / monthly planning | Operations/planning · upstream input | Demand, campaigns, strategic stocks and expected supply; the application edits the approved seven-day demand envelope |
| **Job Allocation Layer** | **This project · L3 operator-supervised automation** | Quality/quantity, blend, pile, machine, route and sequence decisions |
| ASP + YardMaster/SMS | Vendor execution system | Validate plan items, execute accepted jobs, maintain 3D state and return actuals |
| PLC / DCS / ACS | Process and machine control | Commands, interlocks, anti-collision and safety |

Replanning is event-driven on quality drift, stock change, late supply, demand change, machine fault or execution deviation—not a fire-and-forget schedule.

## 4. Scope Boundary

| In scope | Out of scope / retained authority |
|---|---|
| Coordinate a seven-day plan of hourly or mixed-resolution jobs across the 3 phosphate SMS; plan M17 independently | Generate the upstream monthly forecast or approve business demand |
| Select source quality, quantity, blend, pile, machine, route and sequence | Mining, washing, PAP, drying and melting process operation |
| Direct a target space when segregation requires it; otherwise permit SMS auto-routing | Physical gate/machine actuation, deposition control, 3D modelling and anti-collision |
| Track plan vs actual, replan events, expose rationale/KPIs and support what-if | Build/own SMS, SQMS, analysers, LIMS, CMS or physical equipment |

| Capability | Allocation layer | ASP | YardMaster / PLC / ACS |
|---|---|---|---|
| Multi-yard demand/blend optimisation | **Owns** | — | — |
| Physical route graph and shared-resource feasibility | **Owns** (§14.2) | Validates configured ASP tuple | Executes selected route |
| Plan-item rule validation | Pre-checks | **Second gate** | — |
| Accept/reject/override | Preserves human decisions | Supports workflow | Executes accepted job |
| Safety/interlocks | Never replaces | — | **Retained authority** |

An ASP “Route” is only `(Device, JobType, Location)`; it is not the conveyor network. RabbitMQ access/licensing and site-specific master-data mappings remain TBC.

## 5. Functional Scope

| Inputs | Decisions | Outputs |
|---|---|---|
| Dated demand campaigns: receiving unit, grade, physical quantity, intraday window and priority | Source quality and quantity | Prioritised time-sequenced jobs per SMS/machine |
| SMS 1-m inventory segments, quality and age | Blend mode, component masses/shares and destination | Stack: source, grade, method, target pile + repère range, rate, tonnes |
| Job actuals and machine availability/calendars | Machine, pile, route and timing | Reclaim: pile + repère range, destination, route, blend, rate, tonnes |
| Online analyser + lab results | Reallocation on deviations | Target stock profile, alerts, rationale and KPIs |

### 5.1 Repère position model

Each stockpile uses the operating scale **R0–R1000**, representing **1,000
equal longitudinal intervals**. A job may therefore state, for example,
“store this grade on `LT-P1` from **R400 to R600**.” Repères are the
operator-facing coordinate; physical metres remain canonical for SMS/ASP
exchange, inventory segments, machine travel and safety distances:

`x_m = x₀_m + (R / 1000) × (x₁_m − x₀_m)` ·
`R = 1000 × (x_m − x₀_m) / (x₁_m − x₀_m)`.

The Innomotics FRS confirms numeric Start/End positions (`StartX`/`StopX`),
per-metre pile inventory and position-range validation. The term **repère**
and the R0–R1000 scale are a **project-team operating convention confirmed
24-Jul-2026**, not a claim that every pile is 1,000 m long. Before live use,
each pile still needs its R0 endpoint and increasing direction, surveyed
physical origin/usable length, and the ICD's StartX/StopX units and scale.

#### 5.1.1 `Layer` — the vertical coordinate

`StartX`/`StopX` locate a job **along** the pile. The plan item carries a
second, independent coordinate — **`Layer`** — which locates it **through the
height of the pile**. A stockpile is built by successive lifts, and `Layer` is
the index of one such horizontal band in the Z direction; the FRS cross-section
(Fig. 22, *"sub-grade for each layer at X position on pile"*) resolves a pile
at one X position into stacked bands `Layer 1…n`, each carrying its own
sub-grade. `StartX–StopX` and `Layer` are therefore independent selectors: the
longitudinal cut, and the lift within it.

**Layer binds differently on the two emitted job types.** For **Stacking** it
is unambiguous — the lift being deposited. For **Reclaiming** the FRS's own
rules do *not* use it to bound the volume: UC-26 sizes the cut from the
available mass *"between the position range"* and the planned tonnage, with no
reference to `Layer`, while UC-30 uses `Layer` solely to look up the earliest
stacking date. On the evidence of the rule text, `Layer` on a reclaim item
behaves as an **age and quality lookup key, not a volume bound** — an
inference from rule wording, not an explicit FRS statement, and **TBC**.

| Axis | Field | Operator unit | Canonical unit | Selects |
|---|---|---|---|---|
| Longitudinal (X) | `StartX` / `StopX` | repère `R0–R1000` | metres | which cut along the pile |
| Vertical (Z) | `Layer` | lift index | integer | which lift within that cut |
| Transverse (Y) | — | — | — | not addressable; the full cross-section is implied |

**Scope is one pile per job.** The plan item carries a single `PileId`, so a
job can never span piles: *"reclaim the top of piles 1101, 1103 and 1104"* is
**three sequenced jobs**, not one job with three targets. The layer already
composes campaigns this way — the optimiser assigns `a[reclaimer, s, t]` per
space and bucket (§15.1), and one-space-at-a-time exclusivity (§15.4) makes
serial execution the only legal form. The only place ASP reasons across several
piles at once is the **FIFO advisory** (UC-30), which searches every stockpile
holding the same material and grade for older surface material; that is a
cross-check on a single-pile job, not a widening of its scope.

**Addressable is not the same as reclaimable.** A pile's surface is a slope at
the material's repose angle (30–35°, §13.4), so it **outcrops every layer**:
lower lifts are exposed along their toe and flank, and "the surface" is not a
synonym for "the top layer". Bottom-layer material is therefore genuinely
accessible — but only in the outcrop band, and the core of that same layer
stays buried under everything above it.

That distinction is **transverse (Y), and Y is not addressable**. The plan item
offers a longitudinal range and a lift index; it has no selector separating an
exposed toe from the buried core of the same layer. A partial-layer cut is thus
expressible **in X** — `Layer = n` over a sub-range `StartX–StopX` — and **not
in Y**. In practice the machine works the same way: a face-cutting reclaimer
advances along X taking the full cross-section, consuming every layer at that
position rather than skimming one band, which is consistent with the model
exposing no Y selector and with UC-26 sizing the cut from the position range
alone.

Whether ASP **rejects** a request whose named layer is buried, or simply
computes inventory over the range and lets it through, is **not stated in the
FRS** — UC-26 checks mass, analysis availability and prevalent grade within
`StartX–StopX`, never overburden. Until the ICD settles it, surface- and
layer-consistency are treated as **layer-side constraints we enforce
ourselves**, not ones the second gate can be relied on to catch. Selective
within-layer or within-zone reclaim composition remains a **not-implemented
extension** (§§15.6, 17).

Two further consequences matter for allocation. First, because lifts are deposited in
chronological order, **`Layer` is an age proxy**: the ASP FIFO rule (UC-30)
reads the earliest stacking date of material *"within StartX and StopX on the
`AspPlanItem.Layer`"*, so the chosen layer — not only the chosen pile —
determines whether a reclaim is judged FIFO-compliant. Second, `Layer` is the
granularity at which the twin can express a reclaim cut's quality, which is
why within-pile layer/zone reclaim composition is listed as a **not-yet-
implemented** extension in §§15.6 and 17.

> **Terminology warning.** This report uses *Layer* in two unrelated senses:
> the **Job Allocation Layer** (the L3 software tier, §3) and a **pile layer**
> (this vertical band). Code-style `Layer` and the phrase *pile layer* always
> mean the material band; the software tier is always written out in full.

**`Layer` is read on both emitted types — but authored by neither.** It is
absent from the ASP plan-editor form spec for *either* job type: neither
Table 26 (form elements) nor Table 28 (mandatory fields per job type) lists it.
Where it does appear is one step downstream — in the **YardMaster job**
dialog of Fig. 12, which UC-11 reaches by right-clicking a plan item and which
writes to job management, not to ASP; and in the `JobUpdated` property set
(Table 52), alongside the separate `LevelNr`. Fig. 24's two tables make the
split explicit: `ASPPlanItem` and `MaqRt.JobJob` are distinct records.

UC-30 nonetheless dereferences `AspPlanItem.Layer`, so the plan item **carries**
the property even though the editor never collects it. Whether the planning
layer can **set** it on emission, or whether it is populated downstream by job
management / the MOM PLC at execution, is therefore unresolved — and it is the
question that decides whether layer-aware authoring is available to us at all.
Its numbering origin and direction (top-down vs bottom-up), its relationship to
`LevelNr`, and whether layer bounds are configured per pile or derived from the
3D model are likewise **TBC pending the ICD**.

| Constraint family | Planning rule |
|---|---|
| Corridors | C1/C2 capacity, availability, transport lag and in-transit inventory |
| Quality | Destination intervals, anti-contamination and stage-correct blending; dry-solids weighting for chemistry and physical-mass weighting for moisture |
| Inventory | Initial mass, safety stock, capacity and conservation |
| Machines | Reachability, exclusivity, rate, travel/setup, maintenance and shared rails |
| Same-pile work | M16 stack/reclaim concurrency permitted only with spatial/work-zone/route separation; numerical limits TBC; mine-site applicability TBC |
| Sulphur | Open reclaim remains manual; closed RC01 feed and ≤8 start-stops/h constraint are planned |
| Dry stream | Dryer intake bounds humid reclaim; drying itself is out of scope |

Detailed variables, constraints and equations are defined once in §§15.1–15.4.

## 6. Architecture & Interfaces

```text
L4  ERP / production plan ───────────────► reporting / historian
                 │          OT/IT DMZ               ▲
                 ▼                                  │
L3  JOB ALLOCATION LAYER ↔ operator HMI · LIMS · CMS
                 │ jobs per SMS                     ▲ actuals/state
                 ▼                                  │
L2  ASP / SMS ×3 · SQMS ───────────────► PLC / DCS / ACS
                 │                                      │
L1  stackers · reclaimers · trippers · gates · analysers
```

| Counterpart | Direction | Minimum exchange | Mechanism / cadence |
|---|---|---|---|
| **SMS ×3** | ↔ | Plan items; ASP issues; `JobUpdated`; 1-m inventory/quality/age; machine state | Common YardMaster/RabbitMQ profile; endpoints and IDs per site TBC · event/continuous |
| SQMS / analysers | → | BPL, Cd, MgO, SiO₂, CaO/CO₂, moisture | OPC UA via control layer · continuous |
| LIMS | → | Detailed assay and PSD | API · per sample |
| ERP / planning | → | Approved demand, supply and campaigns | MES API/file · plan/change |
| CMS | → | Availability and maintenance windows | API/OPC UA · event/schedule |
| Historian | ↔ | Trends, plan-vs-actual, KPIs and events | Historian interface · continuous |
| Operator HMI | ↔ | Plan, rationale, what-if, accept/reject/override | Web/HMI · real time |

**Deployment:** dedicated L3 application on the controlled OT side; business exchanges cross a managed DMZ. On stale/missing SMS data it flags freshness and falls back to last-good state plus operator control.

**Configured, not coded:** sites/domains, phase assets, capacities, space/machine reachability, route graph, product specs, compatibility, patterns, objective weights, calendars, endpoints and tag/ID maps.

All equipment, conveyor and stockpile capacities and rates are represented in **physical/as-handled tonnes** at their material stage. TSM is a reported merchant-dry equivalent, not the capacity basis: raw physical mass converts through raw moisture and beneficiation yield; wet washed-product physical mass retains product moisture; after drying, one physical dry tonne equals one TSM. The unified Grade configuration page maintains chemistry, yield/moisture, material profiles and receiving limits, and supports persistent add/delete with referential checks.

## 7. Representative Use Cases

| Use case | Trigger | Optimiser decisions | Issued jobs | Replan trigger |
|---|---|---|---|---|
| PAP on-grade humid feed | dated PAP campaign + pile quality | build an eligible humid stockpile with fixed-matrix or optimized component shares, then choose reclaim rate, machine and route | component stacking jobs followed by ready-boundary-safe reclaim | Cd/BPL drift, machine or demand change |
| Drying / SPH feed | destination spec + dryer limit | build destination pile; humid blend and route | stack/reclaim/gate jobs | quality, dryer or rail-plan change |
| Sulphur continuity | melting demand + closed inventory | replenishment timing and RC01 rate | tripper/stack/closed-reclaim jobs | delivery, payloader or RC01 deviation |

## 8. Assumptions, Dependencies & Constraints

| ID | Current ruling | Residual action / hedge |
|---|---|---|
| **H1 · Telegram access** | Feasible in concept from vendor FRS | Obtain ICD, credentials, licences, namespaces and routing keys; otherwise use standard ASP client workflow |
| **H2 · Common SMS solution** | **Resolved at solution level:** all three sites use Innomotics/YardMaster | Configure three independent endpoints, IDs, routes, calendars and permissions |
| **H3 · Co-authoring** | Layer proposes; humans supervise; ASP arbitrates | Service account owns `AUTO-` items only; human items fixed; Accept/Reject and audit retained |
| Data quality | SMS/SQMS/analyser inputs assumed timely enough for allocation | Freshness limits, last-good fallback and instrument-loss alarms |
| Demand | Approved input-side demand is treated as authoritative | Validate source, cadence and change governance |

## 9. Deliverables & Phasing

| Phase | Outcome | Exit evidence |
|---|---|---|
| **0 · Scope / data** | Boundaries, evidence register, interface inventory | Approved scope and gap owners |
| **1 · Functional design** | Digital twin, optimisation, constraints and ICDs | Design review + testable acceptance set |
| **2 · Digital-twin / shadow pilot** | Historical replay, simulation and supervised shadow operation | Baseline, model validation and operator sign-off |
| **3 · Deployment** | Three-SMS integration, closed-loop operation and KPI reporting | Supervised pilot then production acceptance |

## 10. Key Risks

| Risk | Control / proof |
|---|---|
| Stale or inaccurate quality/inventory | Freshness flags, reconciliation, fallback, replay against actuals |
| Missing SMS ICD/access/master data | Vendor workshop, interface emulator, per-site mapping tests |
| Unreliable demand or route model | Input governance, route audit and zero-invalid-route acceptance |
| Modelled machine/layout differs from reality | GA/equipment list, reachability review and shadow validation |
| Operator distrust / conflicting edits | Explainable rationale, Accept/Reject, human-item preservation and audit trail |
| Benefits overstated | De-overlap losses; approve baseline; no extrapolation before pilot |

## 11. Success Criteria

The canonical outcome scorecard is §2.3; technical acceptance is §18. Proof progresses through four gates:

```text
Historical baseline → Offline replay → Shadow mode → Supervised pilot
        data quality      model validity    safe usefulness    accepted targets
```

## 12. Non-Functional Requirements

| Requirement | Design intent | Target status |
|---|---|---|
| Latency | Event-to-valid-replan fast enough to avoid avoidable feed gaps | TBC at design review |
| Availability | Redundant deployment; last-good-state + operator fallback | TBC |
| Data integrity | Freshness, completeness, reconciliation and traceable transformations | TBC |
| Scalability | Phase expansion and new areas through configuration | Required |
| Security | OT/IT segregation, RBAC, least privilege and audit trail | Required |
| Usability | Rationale, overrides, what-if and clear evidence status | Required |
| Maintainability | Versioned configuration, deterministic tests and simulation environment | Required |

### 12.1 Solve mode — reproducibility is now explicit, and it is a choice

Acceptance criterion #11 (deterministic replay) was **unattainable** before
28-Sep-2026, and the sidebar's "seed 42" implied a guarantee the configuration
did not provide. `sc.sim.seed` is only CP-SAT's `random_seed`; there is no
stochastic element in the model itself (no `random`, `np.random` or `shuffle`
anywhere in `mph_sim`). Pinning the solver's seed cannot make a search
reproducible when the search is paced by wall clock and raced across workers.

Two defects caused it, both fixed:

- **Wall-clock pacing.** Budgets and gating were derived from elapsed real
  time in several places — most damagingly `refine_objectives`, where *whether
  each refinement level ran at all* depended on elapsed time, so an idle
  machine completed more levels than a loaded one and returned a different
  plan. The weekly window loop had the same defect at day granularity: under
  load it silently dropped a civil day and returned a shorter plan.
- **Parallel portfolio.** `CP_SAT_WORKERS = 8` races several strategies and
  returns whichever wins, decided by OS thread scheduling.

| | **Fast** | **Reproducible** |
|---|---|---|
| Workers | 8 (portfolio) | 1 |
| Budget | wall-bounded — predictable latency | deterministic time only |
| Guarantee | quick answer; **plan may vary between identical runs** | identical plan every run |
| Use for | interactive what-if, weight tuning | quoted KPIs, business case, acceptance #11, regression tests |

**Verified 29-Sep-2026 on FULL_FLOW.** Four fresh-process runs returned the
**identical plan hash `98e649ecda`** — across two deterministic budgets (8 and
25) and two guard settings — at 473–598 s wall, giving service 84.46 %,
PAP-1 99 %, DRY-FEED 84 %. Criterion #11 is met. A fifth run diverged only
while competing with the full regression suite at peak load. On S0/BALANCED,
fast mode returned three *different* hashes across three runs, and earlier
measurements varied **72.85 %–83.76 %** service on identical inputs, so a
single fast run is one draw from a distribution: KPIs must be quoted from
reproducible mode or as a range.

**A per-window wall cap was silently truncating every solve.** Reaching the
above required removing wall clock from every decision that changes where the
search stops — not only the main solve budget, but *whether a refinement level
runs at all*, how many retries it makes, whether a civil day is solved, and the
per-window cap. The last of these was the most damaging: `WEEKLY_WINDOW_WALL_TIME_S`
(55 s for this preset) cut roughly **half** of every window's search, since a
window needs ~106 s of wall to exhaust its deterministic budget. The cost was
not only non-determinism but **solution quality in both modes**:

| | truncated (55 s cap) | untruncated |
|---|---:|---:|
| Service | 82.94 % | **84.46 %** |
| PAP-1 | 97 % | **99 %** |
| DRY-FEED | 72 % | **84 %** |

DRY-FEED was losing **12 points** to an invisible timing guard. Any earlier
figure in this report predating 29-Sep-2026 was measured under that truncation.

**Cost and limitation.** Reproducible mode costs ~470–600 s against fast mode's
~113 s on FULL_FLOW, so it belongs on evidence runs rather than behind an
interactive button. Budget 8 converges to the same plan as budget 25, so the
extra budget is unused — for a reproducible run the deterministic budget is the
correct lever, never a larger wall guard: raising the guard far enough to never
bind produced a single **6.5-hour** solve, because the refinement passes are
separately unbounded in this mode. The guard is set at ×3 the configured cap,
above the measured need and still bounded. If a run ever hits it, the result
should be treated as truncated rather than authoritative.

## 13. Stockyard Layout, Assets & Evidence Gaps

This section is the canonical layout/model-coverage summary. Detailed provenance and conflicts are retained in the evidence register (§16); metric positions remain schematic until GA/plot-plan data are received.

### 13.1 Canonical site / asset register

| Site / stage | Material / storage | Stack assets | Reclaim assets | Route / destination | Current model status |
|---|---|---|---|---|---|
| **Louta 102MG mine storage** | MINE STOCKPILE-01/-02; **ASSUMED display baseline:** 2 × 100,000 t capacity, 50,000 t t0 each (50%), two-profile 50/50 bands; actual capacity/live state TBC — the 494,274 t ore capacity is *not* theirs | 102MG-ST-01/-02 visible | route/rate/reach TBC | intended transfer toward 110LG; never direct to C1 | **Reference only:** `MODELLING_GAP[MISSING_ROUTE_RATE_REACH]`; excluded from solver inventory, jobs and KPIs |
| **Louta 110/120LG homogenized ore** | 2 stockpiles · 494,274 t total · 2 × 247,137 t **DERIVED** equal split · 50% start = 123,568.5 t each (247,137 t total, ASSUMED) | 1 shared 110LG-ST-01 · 2,100 TPH wet · chevron via boom conveyor 110LG-BC-01 | 2 bridge reclaimers 120LG-RC-01/-02 · 3,000 TPH wet each | RB1–RB3 → common RB4/RB5 → washing L1 or L2 | **Solver-backed S0 route:** both ore lanes and all three machines are planned; displayed multi-profile bands remain non-spatial metadata |
| **Louta washing / beneficiation** | Scrubber residence **2.5–3 min CONFIRMED**; overall washing-line residence **0.5 h (30 min) ASSUMED** | 2 parallel production lines **L1 + L2**, 3 Mt tsm/year each — both current | process-transfer stages, not mobile machines | recovered WIP → 130LG product stacking | **Solver-backed process transfer:** documented case recovery is applied; no same-bucket passthrough and cumulative matured-mass availability enforce the assumed 30-min dependency |
| **Louta 130/140LG product** | 383,024 t total · 2 × 191,512 t **planning/solver lines** (DERIVED equal split); starting mass 95,756 t each (50%) — physical pile count not stated in the source | 1 shared 130LG-ST-01 (fed by 130LG-CV-11/SL11); **1,000 TPH S0 envelope ASSUMED** because no source rate is given | 140LG-RC-01/-02 · 3,000 TPH nominal · conservative one-per-planning-line pairing | RL1/RL2 → flip gates 140LG-FG-01/-02 → RL3/RL4 → C1 | **Solver-backed S0 route:** stack, product inventory, reclaim and exact 8,000 t C1 contribution are planned |
| **Benguerir mine-ore storage** | `BG-MINE-1/2`, 2 × 200,000 t **ASSUMED S0 capacity**, 100,000 t start each; deliberately larger than Louta's 2 × 100,000 t 102MG display baseline | `BG-MINE-ST-1/-2`, one per pile · 2,100/2,500 TPH **borrowed envelope** | shared `BG-MINE-BWR` · 2,100/2,500 TPH **borrowed envelope** | `TM1`/`TSM2`/`TSM3` finite handoffs → mine piles → screened transfer | **Solver-backed:** inventory and stack/reclaim jobs planned; names, reaches and rates are simulation assumptions pending tags/GA |
| **Benguerir screened/raw storage** | `BG-RAW-1/2`, 2 × 200,000 t capacity (400,000 t Table-20 total), 100,000 t start each · <15 mm | `BG-RAW-ST-1/-2`, one per pile · 2,100/2,500 TPH | shared `BG-RAW-BWR` · 2,100/2,500 TPH | three washing campaigns; raw-train interface remains TBC | **Solver-backed:** both piles and all three machines participate in conserved process transfers |
| **Benguerir washing / beneficiation** | Scrubber residence **3 min CONFIRMED**; overall line residence **0.5 h (30 min) ASSUMED and modelled** | `BEN-WASH-L1/L2/L3`, all three OPERATING/configurable in S0 | process stages, not mobile machines | recovered WIP → shared ST3 → B02 | **Solver-backed:** three demand-driven feed/recovery campaigns; S0 rates, recoveries and grade mapping are borrowed/ASSUMED, not Benguerir nameplates |
| **Benguerir B02 washed** | `BG-P1/P2`, 2 × 191,512 t · settled product | current twin-boom `ST3` · 2,100/2,520 TPH | current `RC2/RC3` · 2,600/3,120 TPH; S0 pairs RC2→P1 and RC3→P2 | C2 → M16; washed-product train | **Solver-backed:** ST3 stack, pile inventory and RC2/RC3 reclaim jobs are scheduled from configured demand |
| **M16 humid** | 8 × 125,000 t in **4 side-by-side two-hangar bays** (+ 4 × 125,000 t future); hangar names/left-right order INFERRED | ST01-A/B/C · ≈2,600 TPH · one per line `M16A-A/B/C-T06` | RC01-A/B · ≈2,500 TPH · `M16A-A/B-T11` | 2 separate gate banks `M16A-A/B-H01…H04` → PAP or drying (confluence TBC) | Modelled; each shared line visibly spans its horizontal pile pair; machine reach remains bay-adjacent |
| **M16 dry** | 2 × 50,000 t | travelling trippers | clamshell/gravity gates | rail → SPH | Interface abstraction |
| **M17 open** | 2 × 100,000 t | ST01 | manual payloaders · ≈300 TPH | closed storage | Manual reclaim outside command scope |
| **M17 closed** | 1 × 100,000 t (+1 future) | T04T | RC01 · ≈1,100 TPH (+RC02 future) | melting | Modelled in Domain B |

**Layout rulings:** M16 is **four two-hangar bays with the machine lines interleaved between them** — `M16A-A-T06` (ST01-A) · bay 1 · `M16A-A-T11` (RC01-A) · bay 2 · `M16A-B-T06` (ST01-B) · bay 3 · `M16A-B-T11` (RC01-B) · bay 4 · `M16A-C-T06` (ST01-C) — per `Q80130-MZI-M16-ME-PFD-0001[3]` Rev 3. In the mimic, each bay is explicitly drawn as **one side-by-side horizontal pair**, with every serving machine line spanning both hangars (project-team display ruling, 26-Jul-2026); this makes shared access legible without changing model reachability. There is **no shared T06/T11 corridor** and no detached machine corridor. A machine translating between adjacent bays serves **only** those bays (project-team rule, 25-Jul-2026): ST01-A→bay 1 · RC01-A→bays 1-2 · ST01-B→bays 2-3 · RC01-B→bays 3-4 · ST01-C→bay 4; an unreachable job is rejected by validation and by the ASP work-area rule. Same-pile concurrent stack/reclaim is permitted subject to separation and work-zone rules, whose numerical values are TBC. Hangar names and left/right order remain INFERRED; metric coordinates remain schematic (the PFD is a flow sheet, not to scale); Louta/Benguerir rail coordinates and same-pile applicability remain TBC.

### 13.2 M16 — side-by-side hangar pairs with machine lines between bays

```text
M16A-A-T06 (ST01-A · 2,600 TPH · serves bay 1 only)
   ── BAY 1 · HP-01 / HP-05 · 2 × 125,000 t ──┐  A reclaim stream
M16A-A-T11 (RC01-A · 2,500 TPH · serves bays 1-2)
   ── BAY 2 · HP-02 / HP-06 · 2 × 125,000 t ──┘
M16A-B-T06 (ST01-B · 2,600 TPH · serves bays 2-3)
   ── BAY 3 · HP-03 / HP-07 · 2 × 125,000 t ──┐  B reclaim stream
M16A-B-T11 (RC01-B · 2,500 TPH · serves bays 3-4)
   ── BAY 4 · HP-04 / HP-08 · 2 × 125,000 t ──┘
M16A-C-T06 (ST01-C · 2,600 TPH · serves bay 4 only)
   [FUTURE: bay 5 · M16A-C-T11 (M16A-C-RC01) · bay 6 · M16A-D-T06
    (M16A-D-ST01) · 4 × 125,000 t hangars]

A stream: T11 → T11S separator → T12 (belt scale) → M16A-A-H01…H04 → T13 1,500 TPH ─┐
B stream: T11 → T11S separator → T12 (belt scale) → M16A-B-H01…H04 → T13 1,500 TPH ─┤
                                       PAP / DRYING ROUTE CONFLUENCE — TBC ─────────┘
                                              ├──► PAP  (6 × 1,500 TPH · 3 running)
                                              └──► DRYING (2 × 1,299 TPH · out of scope)
```

**Reclaim take-off — two separate streams:** each reclaimer feeds its own `T11` → `T11S` magnetic separator → `T12` (belt scale) → hopper `M16A-A-R01` with air blasters → **its own clamshell + rod gate bank** (`M16A-A-H01…H04` / `M16A-B-H01…H04`) → `T13` outbound belt at 1,500 TPH. **That chain is confirmed equipment.** What is *not* confirmed is the destination: neither the PFD nor PRO-0001 says which gate or downstream diverter sends a stream to PAP rather than to drying, and the confluence point is not resolvable from the available sheet. The model therefore separates the two — a **confirmed** gate-bank link per stream, then a **derived/TBC** route selection toward PAP (six parallel 1,500 TPH conveyors, 3 running + 3 future) or drying (two parallel 1,299 TPH conveyors, both out of scope). One destination per bank per bucket is derived from the single outbound belt; the documented 10-minute changeover is recorded but **not modelled**.

**Reception envelope — CORRECTED 28-Sep-2026, two independent paths.** This
section previously stated that PRO-0001 §2.1.1's auto-sampler placement
(`M16VP01` on `M16A-A-T01`, `M16VP02` on "the adjacent **future** conveyor")
meant C1 and C2 **share** one 2,600 TPH reception path, capping aggregate
reception at 2,600 TPH. **That claim was never implemented and is withdrawn.**

The model carries `C1` and `C2` as two independent 2,600 TPH links into
`TOWER:M16-REC`, plus eight separate 2,600 TPH stacking links (`L-STK-HP-01…08`),
one per hangar. Aggregate reception is therefore **5,200 TPH** and both
corridors may receive simultaneously; the only exclusion is that one stacker
serves one pile at a time. Project-team confirmation 28-Sep-2026: *"both can
simultaneously feed the humid phosphate stockspace, just not use the same
stacker at the same time."*

A sampler on a future conveyor evidences instrumentation coverage, not a shared
conveyor. The earlier wording also produced a false explanation for idle M16
stackers, which are unused through machine redundancy rather than reception
capacity.

**What is confirmed and what is not:** the eight 125,000 t hangars, the four-bay/machine-line sequence, the machine identities, their lines and their rates are **confirmed** by `Q80130-MZI-M16-ME-PFD-0001[3]` Rev 3. The **hangar names inside a bay are inferred** — the PFD does not name them, so `HP-01…08` remains a working convention pending the plot plan and the official SMS pile-ID register. Metric positions stay schematic: the PFD is a flow sheet, not to scale. Machine-to-bay assignment applies the project-team rule that a machine translating between adjacent bays serves those bays only; per-line travel limits still require a surveyed GA.

### 13.3 Benguerir — six physical piles with three solver-backed washing campaigns

```text
MINE HANDOFFS              MINE-ORE YARD             SCREENED / RAW YARD
TM1 ───────┐              BG-MINE-1 · 200 kt        BG-RAW-1 · 200 kt
TSM2 ──────┼─► 2 stackers ─► shared reclaimer ─► 2 stackers ─► shared reclaimer
TSM3 ──────┘              BG-MINE-2 · 200 kt        BG-RAW-2 · 200 kt
  3 finite solver             100 kt start each        100 kt start each
  handoff interfaces          tags / GA TBC             Table-20 total 400 kt

            ┌─ BEN-WASH-L1 · BT raw → TBT ─────┐
RAW RECLAIM ├─ BEN-WASH-L2 · Low-Cd raw → BT ──┼─► ST3 ─► BG-P1 / BG-P2
            └─ BEN-WASH-L3 · BT raw → TBT ─────┘          2 × 191,512 t
               all OPERATING/configurable ·                │
               30 min dependency ASSUMED/modelled           ├─► C2 → M16
                                                            └─► product train
```

The six physical stockpiles are `BG-MINE-1/2`, `BG-RAW-1/2` and
`BG-P1/P2`. The three hopper rows and the line WIP buffers are finite solver
interfaces, **not additional on-ground stockpiles**. The mine-ore, screened
and B02 stages are connected at process level but use their own pile and
machine resources. B01 `04BG-*` and B02 `RB/SL/RL-*` physical tags are not
merged without PFD reconciliation.

| Evidence / issue | Status and modelling consequence |
|---|---|
| Table 20 screened-yard topology: 2 dedicated stackers + 1 shared reclaimer | **User-confirmed** 20-Jul-2026; retained as `BG-RAW-ST-1/-2` + `BG-RAW-BWR` and promoted into the planning model |
| Six-pile capacity / starting mass | **Physical S0 spaces:** `BG-MINE-1/2` = 200,000 t capacity and 100,000 t start each (**ASSUMED**); `BG-RAW-1/2` = 200,000 t capacity and 100,000 t start each (400,000 t Table-20 total); `BG-P1/P2` = 191,512 t each. The first mine-ore pair is intentionally larger than Louta's assumed 100,000 t 102MG piles. Timestamped SMS telemetry is not loaded. |
| Three mine hoppers | `TM1`, `TSM2`, `TSM3` are represented by three finite 10,000 t planning handoffs. The 10,000 t value is a campaign buffer, **not a claimed physical hopper capacity or live reading**; exact tags/capacities/routing remain TBC. |
| Raw-grade and line mapping | Benguerir and Louta share the mine-neutral grade IDs `RAW-BT`, `RAW-BT-HIGH-CD` and `RAW-BT-LOW-CD`; the model does not encode the mine in the grade name. The S0 Benguerir campaigns use L1/L3 BT→TBT and L2 Low-Cd→BT. Chemistry, recoveries and this line/pile mapping are **ASSUMED simulation inputs borrowed from Louta design cases**, not live Benguerir assays or approved operating recipes. |
| Mine-ore machine identities and reach | `BG-MINE-ST-1/-2` and shared `BG-MINE-BWR` are user-directed S0 planning assets. Their 2,100/2,500 t/h envelopes are borrowed for simulation; physical tags, surveyed reaches and metric GA coordinates are **TBC**. |
| Table 20 document no./rev, remaining physical tags and coordinates | **TBC**; none invented |
| Upstream 2,100/2,500 vs B02 raw interface 2,600/3,120 TPH | **Conflicting**; raw→C2 at 2,600 assumes bottleneck resolved |
| ST3/RC2/RC3 and `**` notation | **Current configuration and solver-backed**; `**` is source notation only. S0 plans recovered-product stacking through ST3, then pairs RC2→BG-P1 and RC3→BG-P2 for demand withdrawals. |
| Washing-line planning baseline | `BEN-WASH-L1/L2/L3` are all **OPERATING and configurable** in S0 so the three-hopper/three-line scenario can be exercised. Commissioning/as-built lifecycle evidence remains TBC. |
| Washing-step timing | **Scrubber residence = 3 min CONFIRMED** by `Q80130-BEN-B02-PR-BOD-0001` Rev 0, §7.1 p. 19 and §7.3.1 p. 25. Separately, the project team instructs an **ASSUMED overall washing-line residence = 0.5 h (30 min)**. The 3 min component is not added to the 30 min total; S0 enforces the 30-minute wash-to-ST3 dependency. |
| “3 qualities on 2 piles” | Earlier datasheet signal only; zoning/campaign/compatibility rule **TBC** |
| Raw + washed train routes / station sharing | Interfaces documented; planning ownership and shared station **TBC** |
| B01 flow inconsistencies | 541+89=630 vs stated 628; 1,208 undersize vs 1,055 handling · unresolved |

**Current simulator coverage:** all six physical Benguerir piles, all nine
mobile stockyard machines, all three mine-hopper handoffs and all three
washing campaigns are connected to the planning model. A process campaign
plans hopper-to-mine stacking, mine reclaim, screened-pile stacking, washing
reclaim/recovery and recovered-product stacking through `ST3`; configured
train or C2 demand then creates `RC2`/`RC3` reclaim work. The former continuous
`BG-WASH-IN` shortcut is retired and inactive. Hopper and WIP spaces remain
solver interfaces rather than physical stockpiles. OPERATING and availability
states are scenario configuration, not live telemetry.

**Mimic inspection contract:** **Initial state** shows scenario t0; **Final
state** shows the terminal plan inventory after all horizon buckets. Stockpile
fill length is proportional to physical occupancy rather than merely stretching
the grade bands to the pile outline. Hover exposes grade composition, physical
mass, capacity/fill, material state and stage-correct TSM equivalent when a
conversion profile exists. Hopper and WIP interfaces do not appear as extra
physical stockpiles.

### 13.4 Material and grade states

```text
Raw / screened mine phosphate ── washing ──► filtered washed (23% moisture)
      two-profile ASSUMED compositions             │ settling
                                                  ▼
                                       settled washed product (15–20%)
                                                  │ C1 / C2
                                                  ▼
                                       M16 humid washed product
                                       PGNAA + moisture + SMS/SQMS
```

Material state and grade are separate model dimensions. A washed grade vector must never be reused for raw/screened phosphate.

| Site / stage | Documented physical profile | Chemical / grade status | Model profile |
|---|---|---|---|
| Benguerir mine/screened | <15 mm · 10–14% moisture · 1.2–1.4 t/m³ · repose 30–35° | Shared mine-neutral raw IDs; S0 selects BT on lanes 1/3 and Low-Cd on lane 2. No live/pile-specific Benguerir assay or cut-offs | `BEN-RAW-SCREENED` · solver-backed BG-MINE-1/2 and BG-RAW-1/2 |
| Benguerir filtered | 23% moisture · 1.2–1.4 t/m³ · repose 35° | Assays/cut-offs TBC; S0 recovered mass derives from borrowed case recoveries | `BEN-WASHED-FILTERED` · finite line WIP interface |
| Benguerir settled/product | 15–20% moisture · 1.2–1.4 t/m³ · repose 35° | TBT/BT/MT names known; S0 produces TBT/BT, while site limits and approved pile rules remain TBC | `BEN-SETTLED-PRODUCT` · solver-backed BG-P1/P2 |
| Louta mine / product | Raw: 15% moisture · SG 2.9 · F100 10 mm; combined product: moisture <22% · SG 3 | Raw BT profiles feed assumed 102MG/110LG display compositions; LT-P1/P2 use assumed washed TBT/BT/MT distributions; operating mapping/cut-offs TBC | Separate raw reference and product-planning profiles |
| M16 received humid | PGNAA + moisture; SMS/SQMS live values | Grade/SQP IDs and contractual limits TBC | `M16-WASHED-HUMID` · HP-01…08 |

| Demonstration grade | BPL | Cd ppm | MgO % | SiO₂ % | **CO₂ %** | Moisture % | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| TBT | 63.0 | 15.0 | 0.78 | 3.5 | **6.45** | 12 | washed-product simulation placeholder |
| BT | 68.0 | 12.5 | 0.68 | 2.8 | **6.25** | 12 | washed-product simulation placeholder |
| MT | 74.0 | 10.0 | 0.58 | 2.0 | **6.10** | 12 | washed-product simulation placeholder |

**Two corrections to this table, 28-Sep-2026.**

**(a) The quality order was printed backwards.** This table previously showed
TBT at BPL 74 / Cd 22 and MT at BPL 63 / Cd 35. The model has carried the
opposite since the BoD semantic correction — **TBT < BT < MT**, so MT is the
best grade — and the report row was never updated. BPL now ascends 63 → 68 → 74
and Cd descends 15 → 12.5 → 10, matching `_phos_grades`.

**(b) CO₂ added; Cd and MgO rebased into the G10 window.** The washed vectors
previously carried **no CO₂ assay at all**, while PAP's G10 contract requires
CO₂ ∈ [6.0, 6.5] and a missing parameter fails qualification outright. The
consequence was structural: **nothing either mine produced could ever be
received at PAP**, so M16 was servable only from its opening stock and PAP
service collapsed once that drew down. Cd and MgO were rebased for the same
reason (old Cd 13.6–41.7 against an 18 ppm cap; old MgO 0.6–1.5 against 0.8).
BPL and SiO₂ are unchanged — they already qualified.

The six HIGH-CD / LOW-CD variants keep their raw lineage exactly
(HIGH = family × 25/21, LOW = family × 13/21 from the RAW-BT ratios). All
merchant grades now certify G10 except `TBT-HIGH-CD`, which misses on BPL
62.0 against the 62.5 floor — the poorest ore, and precisely the case blending
exists to correct. Values remain **ASSUMED** pending the approved
washed-product grade register.

**M16 humid stock families.** M16 holds *received* washed product and now
carries its own families `HWP-22 / HWP-10 / HWP-11` (BPL 66.5–69, Cd 6–8,
MgO 0.55, CO₂ 6.2), each positioned inside a receiving window rather than on a
bound so blending has room to move. Opening stock is **86,500 t**, about half
the weekly M16 draw, concentrated in HP-01 — replacing the former 424,000 t,
which was ~2.5 weeks of cover and the reason no corridor delivery or mine
campaign was ever needed.

**Louta product-yard demonstration distribution**

| Product pile · starting state | Assumed grade distribution | Mass-weighted composite used by planning |
|---|---|---|
| **LT-P1** · 95,756 / 191,512 t (50%) | 60% TBT + 40% BT | BPL 71.6% · Cd 25.2 ppm · MgO 0.92% · SiO₂ 2.32% · moisture 12% |
| **LT-P2** · 95,756 / 191,512 t (50%) | 60% BT + 40% MT | BPL 66.0% · Cd 32.0 ppm · MgO 1.22% · SiO₂ 3.08% · moisture 12% |
| **Yard aggregate** · 191,512 / 383,024 t (50%) | 30% TBT + 50% BT + 20% MT | BPL 68.8% · Cd 28.6 ppm · MgO 1.07% · SiO₂ 2.70% · moisture 12% |

TBT/BT/MT are **ASSUMED washed-product demonstration vectors**; their
operating Louta grade mapping and cut-offs remain TBC. Band widths show mass
shares only—not surveyed spatial order, stacking chronology or reclaim
sequence. For these Louta demonstration piles, planning uses each pile's
aggregate composite quality and does not select a component profile or zone
independently. Profile-selective reclaim requires surveyed boundaries and the
active reclaim face; the implemented M16 pile-construction blend is the
separate, explicitly bounded case in §13.7. The shared
`RAW-BT-*` grades are a **separate material state**. Louta's display
compositions remain assumed metadata. In the Benguerir solver chain they are
campaign feed identifiers: BT feeds L1/L3 and Low-Cd feeds L2 before the
assumed wash transformations produce TBT/BT. Raw-grade vectors are never
applied directly to washed-product inventory.

**Shared raw BT grades — current chemistry source: Louta L02 design basis**

| Profile / specification | BPL % | MgO % | CO₂ % | SiO₂ % | Cd ppm | Moisture % | SG | PSD |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| **BT raw — component of both demonstration piles** | 56.06 | 1.49 | 8.21 | 13.71 | 21 | 15 | 2.9 | F100 10 mm |
| High-Cd BT raw — P1 component | 55.11 | 1.50 | 8.41 | 16.28 | 25 | 15 | 2.9 | F100 10 mm |
| Low-Cd BT raw — P2 component | 58.28 | 1.13 | 8.75 | 10.22 | 13 | 15 | 2.9 | F100 10 mm |
| Combined product specification — reference only | 68.29 | <0.6 | 6.81 | <4 | 17 | <22 | 3 | — |

**Louta display-only composite chemistry and Benguerir S0 feed assignments**

| Raw pile · assumed mass composition | BPL % | MgO % | CO₂ % | SiO₂ % | Cd ppm | Moisture |
|---|---:|---:|---:|---:|---:|---|
| 102MG-SP-01 · 50% BT + 50% High-Cd | 55.585 | 1.495 | 8.31 | 14.995 | 23 | Louta 15% |
| 102MG-SP-02 · 50% BT + 50% Low-Cd | 57.17 | 1.31 | 8.48 | 11.965 | 17 | Louta 15% |
| 110LG-OL1 · 50% BT + 50% High-Cd | 55.585 | 1.495 | 8.31 | 14.995 | 23 | Louta 15% |
| 110LG-OL2 · 50% BT + 50% Low-Cd | 57.17 | 1.31 | 8.48 | 11.965 | 17 | Louta 15% |
| BG-MINE-1 / BG-RAW-1 · BT S0 campaign | 56.06 | 1.49 | 8.21 | 13.71 | 21 | Benguerir 10–14% physical metadata |
| BG-MINE-2 / BG-RAW-2 · Low-Cd S0 campaign | 58.28 | 1.13 | 8.75 | 10.22 | 13 | Benguerir 10–14% physical metadata |

Source: `Q80130-LOU-L02-PR-BOD-0001` Rev B, 12-Apr-2023, §6.1 pp. 14–15 and p. 18, plus project-team multi-profile and shared-grade clarifications through 28-Jul-2026. The grade IDs are shared across the mines, but the numeric chemistry used for the Benguerir S0 campaign is borrowed from the Louta design basis and remains **ASSUMED**, not a Benguerir assay. Louta's R-band shares remain schematic display metadata; Benguerir's solver-backed pile state uses its configured campaign grade and live/terminal tonnage. The combined-product row retains the document's own stage wording and does not replace the TBT/BT/MT demonstration vectors.

**Required for realistic quality planning:** pile survey or SQMS segment data to replace the assumed shares, band widths and mapping; live/pile-specific Benguerir raw assays and raw/washed min/typ/max assays by site/grade; Grade/SQP cut-offs and IDs; sampling basis/frequency; PSD/moisture; pile/zone campaign and compatibility rules; SMS Material/Grade/SubGrade mappings. Live SMS/SQMS values override the demonstration composition and current design-basis vectors.

### 13.5 Louta — four stages from mine storage to the C1 corridor

```text
102MG MINE STORAGE          110/120LG HOMOGENIZED ORE        WASHING /            130/140LG PRODUCT
MINE STOCKPILE-01/-02       2 stockpiles · 494,274 t total   BENEFICIATION        383,024 t total
2 × 100,000 t ASSUMED       2 × 247,137 t (DERIVED)          ● LINE L1 OPERATING  2 × 191,512 t planning lines
50,000 t each t0 (50%)      50% start: 123,568.5 t each      ● LINE L2 OPERATING  1 shared 130LG-ST-01
                                                             30 min residence · ASSUMED
PRE-beneficiation           1 shared ST 2,100 TPH wet        2 parallel lines     140LG-RC-01/-02 · 3,000 TPH
never feeds C1              2 bridge RC · 3,000 TPH wet      3 Mt tsm/year each   → RL3/RL4 → C1 (≈2 h, nearer)
        │                   ASSUMED grade bands                      ▲                     ▲
        └──────────────────►        │  ──────────────────────────────┘─────────────────────┘
                            (common RB4/RB5 transfer → either line)
```

**Applied planning geometry, not documented pairing:** the process description confirms two ore stockpiles and two ore reclaimers but never states which reclaimer serves which pile — both reclaim routes converge on the common RB4/RB5 transfer that can feed either washing line. The mimic therefore draws one reclaimer per pile as the applied planning arrangement. The same caution applies downstream: the **physical** product-pile count is not stated, so LT-P1/LT-P2 are planning/solver lines with a derived equal split.

**Mimic inspection contract (Louta):** **Initial state** shows 102MG mine storage at 50,000 t per pile, 110LG ore at 123,568.5 t per pile and product at 95,756 t per line — all 50%. **Final state** shows terminal plan inventory for the solver-backed 110LG and product lines. Only 102MG stays at its static assumed reference baseline in both views.

Authoritative equipment source: **Louta Beneficiation Plant Process description, Rev B (IFA), 25 April 2023, §3.1 pp. 5–8**. **Document-control inconsistency recorded:** the cover states `Q80130-LOU-L02-PR-REP-00002` while every body header states `Q80130-LOU-L02-PR-REP-00004`; both are retained and neither is silently preferred. Storage totals come from the **Louta BoD Rev B**, which gives no individual pile capacities. The BoD p. 20 lists one ore reclaimer and one product reclaimer, while the later tagged process description identifies `120LG-RC-01/-02` and `140LG-RC-01/-02`; S0 uses the tagged two-plus-two configuration and preserves both count conflicts. Neither PDF contains `102MG` tags or the label `C1` (the process description calls the outbound route `BL`), so those mappings are project evidence/model bindings rather than PDF-confirmed facts.

**S0 stocked-route policy — explicit project assumption, not a design-document operating rule.** When `LOU_S0_STOCKED_ROUTE_ENABLED=true`, the configured targets are exact: C1 dispatch = **8,000 t**; recovered process output = **4,000 t from L1 + 4,000 t from L2**; terminal `110LG-OL1/-OL2` = **123,568.5 t each**; terminal `LT-P1/-P2` = **95,756 t each**; and both line WIP buffers = **0 t**. L1 uses the documented BT case (**584 t/h feed; 68.22% recovery**) and L2 the documented Low-Cd case (**588 t/h; 67.71%**); the documented High-Cd case (**579 t/h; 68.83%**) remains selectable but is not used by S0. BT→L1→TBT and Low-Cd→L2→BT are **ASSUMED mappings** because the PDFs do not map the raw cases to TBT/BT/MT. `MACHINE_COVERAGE_REWARD=0` and a small positive commanded-tonne cost remove coverage rewards and token jobs: every movement must serve a corridor, process-output or terminal-stock purpose. Disabling the policy restores legitimate demand-only behaviour, under which opening product stock may satisfy demand without upstream Louta jobs.

The S0 source interface comprises two finite **10,000 t ASSUMED mine-handoff inventories** feeding `110LG-ST-01`; these are not 102MG piles and do not imply a documented 102MG→110LG connection. Common RB4/RB5 is conservatively capped at **588 t/h** with one ore reclaimer at a time; the unresolved RL3/RL4/C1 confluence similarly permits one product reclaimer at a time. The PDFs document direct ore bypass of homogenization and direct product bypass of storage, so idle stockyard equipment can also be operationally legitimate. Solver output therefore reports each selected machine as scheduled or with a structured disposition such as unavailable, no source inventory, no target/free capacity, missing route data, shared-route peer selected, or target met without that machine; the documents do not designate either reclaimer pair as duty/standby.

| Stage | Known from current material | Not established | Required next |
|---|---|---|---|
| **102MG mine storage** | MINE STOCKPILE-01/-02 and visible 102MG tags (ST-01/-02, TR-06, CP-07/08/10/11); pre-beneficiation stage that never feeds C1. S3-A25 displays 100,000 t capacity / 50,000 t t0 per pile with assumed 50/50 raw-grade bands | **Actual** capacities, live inventory, grade spatial order, complete machine counts, duties, reachability, routes/rates and metric positions — the 494,274 t ore capacity belongs to 110LG, not here. No supplied Louta PDF contains 102MG or establishes its connection to 110LG | Full drawing/title block/legend, GA, equipment list/route matrix and timestamped SMS/SQMS pile snapshot; remains `MODELLING_GAP[MISSING_ROUTE_RATE_REACH]` |
| **110/120LG homogenized ore storage** | Exactly two homogenized stockpiles by chevron stacking; one shared ore stacker 110LG-ST-01 at 2,100 TPH wet with boom conveyor 110LG-BC-01; two bridge-type bucket-wheel reclaimers 120LG-RC-01/-02 at 3,000 TPH wet, either route able to feed washing L1 or L2 through the common RB4/RB5 transfer; total capacity 494,274 t. The lanes and machines are solver-backed in S0 | Individual pile capacities (the equal 2 × 247,137 t split is **DERIVED**), the 50% starting/terminal inventory and ore grade-band distribution (**ASSUMED**), the reclaimer↔pile pairing (conservative planning geometry only), metric GA and live masses. The generic BoD lists *one* ore reclaimer while the later tagged process description specifies *two* — S0 uses the tagged configuration and records the conflict | Pile capacity schedule, machine reach/GA matrix, pile survey or SQMS segments, ore campaign rules and common-route capacity/concurrency evidence |
| **Washing L1 / L2** | Two parallel production lines, 3 Mt TSM/year each, both current; three documented ore cases (BT, Low-Cd, High-Cd) each requiring its own treatment; **scrubber residence = 2.5–3 min CONFIRMED** (`Q80130-LOU-L02-PR-BOD-0001` Rev B, §8.2.1 p. 21); **overall washing-line residence = 0.5 h / 30 min ASSUMED** by the project team. S0 models L1/L2 as recovery-bearing process transfers with no same-bucket passthrough, cumulative matured-mass availability and a minute-level 30-min dependency, not as mobile machines | Per-line instantaneous availability and approved case/product campaign map remain TBC. The scrubber value is included within, not added to, the assumed 0.5 h total. S0 selects BT on L1 and Low-Cd on L2; case feed/recovery is documented, but the line and output-grade mapping is **ASSUMED** | Validate 0.5 h against operating timestamps; obtain per-line output plan, availability, shared-tank/filter concurrency constraints and approved campaign rules |
| **130/140LG product storage** | Total 383,024 t; one shared product stacker 130LG-ST-01 fed by 130LG-CV-11 (SL11); two product reclaimers 140LG-RC-01/-02 at 3,000 TPH with the documented RL1/RL2 → flip gate → RL3/RL4 → outbound `BL` topology. Product lines and all three machines are solver-backed in S0 | **Physical product-pile count is not stated** — LT-P1/LT-P2 are planning lines with a DERIVED equal split and an ASSUMED 50% starting/terminal target; stacker/SL11 rate is absent (**1,000 TPH S0 envelope ASSUMED**); pairing and confluence exclusivity are conservative. BoD says one product reclaimer while the later tagged process description says two; `C1` is a project/model binding, not a PDF label | Product-yard arrangement, SMS/SQMS segment snapshot, live masses, stacker/SL11 rating, reclaimer reach matrix, RL3/RL4/C1 confluence capacity and operating grade mapping |
| **Option boundary** | Grey `TO DIGUE (OPTION-2)` exists | Selected for Phase 1 | Formal option decision; remains excluded meanwhile |

### 13.6 Control domains

| Domain | Coordinated scope | Why separate |
|---|---|---|
| **A · Phosphate** | Louta + C1 + Benguerir + C2 + M16 → PAP/drying/SPH; one plan, three SMS outputs | Shared quality, inventory, corridors and M16 resources |
| **B · Sulphur** | M17 closed-store replenishment/reclaim → melting | No shared material, machines or blend constraints with phosphate |

Both domains use one configurable engine and operator UI but solve independently. New areas/SMS are added through configuration.

### 13.7 Current implementation baseline — September 2026

This subsection records the current application behaviour and supersedes any
earlier prototype wording in this scope that implies a single-day horizon,
fixed one-hour job duration, a separate contingency-entry table or direct
two-stream reclaim blending.

| Area | Implemented behaviour |
|---|---|
| Planning envelope | Default tactical horizon is **7 days / 168 hourly decisions**. Optional rolling refinement uses 15-minute decisions in the first four hours and hourly decisions thereafter. Outage, derate and execution boundaries retain exact minutes, and adjacent compatible slices are merged into one continuous job rather than displayed as artificial one-hour jobs. |
| Demand | One dated seven-day table covers Benguerir, Louta and MPH. A unit may have **multiple grade/quantity campaigns on the same day**; each row carries an intraday start/end and priority. Demand is retained when a route is unavailable and is reported as unmet rather than silently removed. |
| Routing and contingencies | Plan → Routing availability reuses the Monitor's global flow diagram with All/Louta/Benguerir/MPH filters. Clicking a planner-controlled machine, conveyor, route or plant opens one harmonised control: **Available / Disabled / Future**, plus a physical-rate cap, applied for the whole run or an exact timed interval. This consolidates planned contingencies; a separate table-entry page is no longer required. Current state is explicit on the icon/line and in the consolidated control list. |
| Configuration | The simplified Configure section includes one Grade configuration page with Chemical grades, Yield & moisture, Material profiles and Receiving limits. Grade records can be added or deleted persistently; a referenced grade must first be remapped, preventing orphaned material profiles or stockpile assignments. The user-facing nominal grade name is **BT**. PAP-2 exposes the same three lifecycle states as every destination and defaults to **Future**. |
| Mass and quality basis | Capacities, inventories, rates, jobs and blend tonnes are physical/as-handled values at the applicable stage. Raw physical mass → TSM uses `(1 − raw moisture) × beneficiation yield`; wet washed product retains configured moisture; after drying, 1 physical dry tonne = 1 TSM. Chemistry of a blend is dry-solids weighted, while moisture is physical-mass weighted. TSM appears as a secondary label/hover value only when a valid grade conversion exists. |
| Integrated mimic and inspection | The same diagram is used for planning and monitoring. Reduced labels preserve legibility while machines, plants and conveyors carry prominent state cues. Stockpile fill is drawn proportional to physical occupancy; hover exposes stockpile grade composition, physical mass, capacity/fill, stage and applicable TSM equivalent. |
| Job schedule and results | Monitor exposes readiness, demand/service and unmet quantity, quality/off-spec/give-away, inventory/safety/capacity, route and machine utilisation/coverage, positioning/conflict, and replanning/recovery evidence. It also shows the active configurable objective weights. Schedule filters include site/domain, machine, operation and status plus **Displayed period = full run or an exact calendar day**. |
| M16 blending | The implemented point is the **M16 humid stockpile HP-07** before its PAP receiving campaign. The dedicated Blending page offers **Blend matrix** (operator-set fixed shares) and **Optimized shares** (component masses are solver variables). In optimized mode composition, stacking time, routes, equipment calendars, source inventory, competing work and destination interval are solved together in physical tonnes. The proposal may change on replan until stacking starts; executed stacking locks the resulting matrix. Reclaim cannot start before the configured pile-ready boundary, and the stacked batch mass must equal the matrix mass exactly. |

The following blending extensions are **not yet implemented**: Benguerir
washed-product piles into a train campaign; multi-quality raw-stock feed into
washing lines; and spatial tracking of layers/zones and their changing reclaim
composition. Grade-class chemistry is currently configured as vectors plus
destination receiving intervals; defining the grades themselves entirely by
min/max characteristic intervals remains a planned modelling extension.

**Rev AC verification history:** the focused blending/scheduling regression set passes
61 tests. A full seven-day physical solve has also returned a feasible 12,000 t
HP-07 campaign with jointly selected 1,000 t TBT + 11,000 t BT-LOW-CD and no
reclaim before the 48-hour ready boundary. These are deterministic simulator
checks, not proof of plant performance. The wider legacy repository suite still
contains stale UI/report expectations and is not yet a clean acceptance suite;
operational validation continues to require governed SMS/SQMS data, approved
quality intervals, route evidence and shadow/pilot comparison.

### 13.8 Review hardening and business-case comparison - Rev AD

The report-aligned application was recovered from the Desktop OneDrive backup into `Claude-Integrated-App/simulator`. The main launcher targets this copy. The earlier simulator, original backup, saved grade catalogue and audit history remain preserved; `generated_outputs/project_review/recovery_manifest.json` records recovery provenance.

| Area | Implemented review improvement |
|---|---|
| Blending and execution | Fixed chemistry uses actual integer tonnes; missing assays stay unknown through stock mixing, snapshots and in-transit parcels. First stacking locks the matrix; reclaim requires physical completion, readiness and hard quality. Invalid timing and unavailable upstream production withhold dependent work. |
| Demand and grades | Atomic edits preserve hourly profiles and C2 by default; real clock offsets, raw/washed compatibility, grade references and finite catalogue values are checked. Per-campaign priority is retained; blank inherits the unit priority. Configure again exposes inventory snapshots, and fresh scenarios preserve plans during navigation. |
| KPIs and weights | Due-bucket terminal service; daily grade/priority process service; exact flows and selected blend chemistry; on-spec mass, unknown tonnes, terminal versus handled mass and physical safety exposure. Optional Balanced, Quality and Inventory continuity weight sensitivities remain uncalibrated. |
| Independent SMS experiment | Synthetic 12-hour case, three deterministic local protocols, equal nominations, finite assumed 500 t holding buffer and common physical replay; strongest local comparator and null controls retained. |
| Demonstration result | Both strongest local and coordinated policies deliver 600 t on-time/on-spec. Peak staging falls from 200 to 0 t and holding from 750 to 0 tonne-hours; zero demonstrated throughput uplift. These are not actual-plant savings. |
| Full-project sensitivity | Copied current, peak, outage, quality and low-stock cases; local source portfolios share the global feasibility scheduler, so do not isolate full-plant blindness. Failed solves/execution checks cannot support monetary benefit. |

Detailed findings, experiments, current verification and economic evidence plan: `generated_outputs/project_review/MPH-Project-Review-and-Business-Case.pdf`. Prior Rev AC counts are historical. Plant ROI requires approved snapshots, operator protocols, holding/backpressure behavior, matched replay and governed costs.

# PART II — FUNCTIONAL DESIGN

## 14. Domain / Data Model (planning digital twin)

The layer keeps a **planning abstraction**, continuously refreshed from the authoritative SMS/SQMS 3D inventory and machine state, then augmented with demand, routes, recipes and jobs. It never replaces the physical twin. The canonical three-site value chain, system boundary and site assets are defined once in scope §§1, 4 and 13; this part defines only the information and optimisation model.

### 14.1 Entities

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
| **BlendCampaign** | target stockpile, eligible incoming grades, fixed matrix or optimised shares, physical batch tonnes, readiness and execution-lock state (§15.6) | operator configuration + planning output |
| **GradeCostModel** | grade-dependent process consumption and rock value; data not yet received | process engineering/finance |

### 14.2 Route graph (layer‑owned) — versus the ASP "Route"

| ASP “Route” | Route graph owned by this layer |
|---|---|
| `(Device, JobType, free-text Location)`; filters plan-editor choices | Physical nodes/links/gates; capacities/minimum rates; delays/in-transit mass; states, conflicts, transitions, availability and valid source→destination paths |

Every emitted job must pass the physical graph first; ASP validation is a **second gate**. PFD/TBE data define connectivity and rates; GA/plot-plan data are still required for metric travel and transition times.

### 14.3 Entity relationships

```text
MaterialProfile + QualityClass ──type──► StockSpace ──holds──► Inventory
                                           ▲                    │
Machine ──reachability──────────────────────┘                    │
Demand ──targets──► Destination ◄──valid RouteGraph path────────┘
                            ▲
Job = Machine + StockSpace + RouteGraph path + BlendCampaign/Pattern
```

### 14.4 Phase 1 instances (from the scope note)

| Domain / site | Planning role | Solver coverage | Canonical evidence |
|---|---|---|---|
| Louta | 110LG ore stock → L1/L2 recovery → 130/140LG product yard → C1 | **Stocked route solver-backed from 110LG through C1; only 102MG remains reference-only** | Scope §13.1/13.5; evidence §16 |
| Benguerir | Mine storage → raw storage → three washing campaigns → washed-product yard → C2/train | Six stockpiles and nine mobile machines are solver-backed; washing lines are recovery-bearing process stages | Scope §13.3; evidence §16 |
| MPH M16 | Receive, construct humid-product blends, buffer and reclaim → PAP/drying | Current bays, routes and machines modelled; PAP-2 is configurable and Future by default | Scope §13.1; evidence §16 |
| MPH M17 | Open/manual → closed → melting | Domain-B abstraction; open reclaim exogenous | Scope §13.1; evidence §16 |

Exact capacities, assets, statuses and unresolved source conflicts are intentionally not repeated here; §13 is the asset summary and §16 the authoritative evidence register.

---

## 15. Allocation Decision Model

Each planning cycle selects mass flows, assets, routes and timing to meet on-grade demand while respecting the coupled three-site constraints.

### 15.1 Decision variables

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

### 15.2 Objective — two modes, and only one of them uses these weights

**Correction, 28-Sep-2026.** This section described the weighted sum below as
*the* objective. It is only the objective in `OBJECTIVE_MODE = "weighted"`. The
built-in phosphate scenarios select **`service_first`**, where the primary
expression is *lexicographic* and `sum(terms)` — the entire weighted block —
is **discarded**:

```
primary = (service_bound + 1) · emergency_expression + qualified_service_expression
```

`service_bound + 1` is a finite bound making one emergency tonne outweigh all
service in the window; after that, priority-weighted **unmet demand is the sole
remaining term**. So in the mode S0 actually runs, unmet demand is prioritised
*more* strongly than any weight could express — but `w₁…w₇`, the corridor
dispatch targets and the safety-stock preference **do not drive the primary
solve at all**. They reappear only in the bounded refinement passes (§15.7).

Two consequences worth stating plainly:

- Raising `w₁` changes nothing in `service_first`. Tuning the weights in
  Configure while that mode is active adjusts refinement only.
- `CORRIDOR_DISPATCH_TARGET_T` is priced inside `sum(terms)`, so a declared
  corridor intention carries no objective weight in this mode. Measured
  28-Sep-2026: the mines produced 93,800 t/week and dispatched **16,529 t** to
  M16, which then drew itself down 33,408 t. Nothing pulls the corridors.

**Destination priority is a weight, not a rank.** It multiplies the unmet
penalty and `0` disables the term, so **higher is served first**. PAP-1
previously carried `priority=1` against DRY-FEED `2` and BG-TRAIN `3`, making
the hub's principal customer the lowest-weighted receiver; the planner
correctly abandoned it (0 of 38,500 t delivered while drying took 80%).
Corrected to **PAP 3 > drying 2 > train 1**.

The weighted-mode terms follow.

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

### 15.3 Dimensional formulation — units, mass balance, quality, delay

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

### 15.4 Constraints

| Constraint | Rule / formalisation | MPH source |
|---|---|---|
| **Corridor (dedicated)** | Louta only on `C1`, Benguerir only on `C2`; flow ≤ 2600 TPH each | dedicated per mine |
| **Louta S0 purpose targets — WITHDRAWN 28-Sep-2026** | The exact pins are removed: C1 dispatch fixed at **8,000 t/week**, recovered output fixed at **4,000 t per L1/L2**, and terminal `110LG-OL1/-OL2` forced back to opening mass. Together these made Louta a **closed loop no demand signal could move** — measured across four levers (downstream demand, PAP-2, campaign calendar, pricing the process feed) the chain stayed at 1 of 5 machines every time. `ASSUMPTIONS.md` S3-A27 already prescribed this: *"replace demonstration targets with approved daily source allocation/end-stock policy; retain demand-only mode when disabled."* Only `LT-WASH-L1/L2-WIP = 0` is kept — not stranding material inside a washing line is physical housekeeping, not the withdrawn policy. C1 is now **derived** from Louta's recovered washing output by `synchronize_weekly_demand_targets`, and `LOU_S0_STOCKED_ROUTE_ENABLED` survives only to guard that the two stay equal. Note the report previously quoted `LT-P1/-P2` terminal mass as **95,756 t each**; the model starts those piles at **10,000 t each** (`_LOUTA_PRODUCT_START_MASS_T`). | withdrawn; was project-team demonstration policy, not in design PDFs |
| **Washing campaign calendars** | Both plants now run **every day**. The previous calendars — Benguerir 3 days of 7, Louta **1 of 7** — were a demonstration artefact (S3-A27: *"campaign inputs, not BoD design-case rates"*), not a plant constraint. They capped output at 16,200 and 8,000 t/week; because C1 is derived from Louta's output, the Louta calendar alone throttled the corridor and left its ore reclaimers and product stacker idle. Daily operation gives **37,800 t/week (Benguerir) + 56,000 t/week (Louta) = 93,800 t/week** against 83,700 t of final demand — the week becomes demand-limited rather than supply-starved. | project team, 28-Sep-2026 |
| **Machine minimum flow rate** | `rate_min_tph` is **0** on all twenty machines. Thirteen previously carried **500 t/h**, which encoded the *blending* rule ("take no less than 500 t of a grade for a job") as a machine flow floor. Normal stacking and reclaiming have no minimum rate. The effect was severe: PAP's real duty of 230 t/h sat **below** the floor of the only machines that could serve it, and since final-unit demand is an hourly equality (`delivered + unmet == demand`), delivery had to be exactly 230 or 0 while the machine could only do 0 or ≥500 — so PAP received **nothing**. The genuine blending minimum is unchanged and lives where it belongs, as `min_input_grade_t = 500` in the chevron configuration. | project team, 28-Sep-2026 |
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
| **Routing / gate (graph)** | every job on a **valid source→destination path** of the route graph (§14.2): link capacities, min stable rates, gate states (each clamshell gate bank routes its stream toward PAP or drying — one destination per bank per bucket), **route conflicts / shared conveyors mutually exclusive**, transition times between successive gate states | route graph (layer‑owned) |
| **Anti‑contamination** | incompatible qualities not co‑stacked in a space | compatibility matrix |
| **Constructed blend readiness** | exact grade tonnes close to the campaign batch; eligible-share and receiving-quality intervals hold; target capacity and all incoming routes/resources remain feasible; no reclaim precedes the exact completion minute; executed matrix is immutable on replan | implemented M16 blend campaign |
| **Sulphur (two‑stage)** | open reclaim exogenous (payloader→`CLOSED‑1`); layer runs `RC01`→melting; **start/stop limits** ≤ **8 start‑stops/hr** (lime dosing) | payloader manual; 8/hr |
| **Drying‑feed cap** | humid→dry reclaim ≤ drying‑plant intake capacity | drying plant (OOS) |
| **Demand service** | eq. (4): `delivered + unmet ≥ demand` with dominant `w₁` penalty — **no separate hard feed constraint** | PAP / molten‑S demand |
| **FIFO / material age** *(soft)* | prefer reclaiming older material of the same grade first (stacking‑age per 1‑m segment); avoid degradation of exposed stock | ASP FIFO (INFO) / age analysis |

***Defense in depth:** every constraint marked with an ASP rule is also enforced vendor‑side — plans submitted to ASP are re‑validated (`AspEvaluatePlanResponse`), an independent second gate (§15.7).*

### 15.5 Implemented solve and replanning approach

| Layer | Horizon / cadence | Solves | Implemented method |
|---|---|---|---|
| **Weekly tactical planner** | 7 civil days · 168 × 60 min by default | demand campaigns, physical mass, machines, conveyors, routes, washing transfers, inventory and M16 blend construction | deterministic **OR‑Tools CP‑SAT**, decomposed by civil day with carried inventory and plan hints |
| **Rolling refinement** | optional 15-min first 4 h → hourly tail | the same coupled decisions with finer near-term timing | mixed-resolution CP‑SAT; exact-minute event boundaries retained |
| **Event/replan loop** | on outage, derate, state/rate deviation or job feedback | preserve executed work; regenerate feasible remaining work | replay actuals, lock executed blend composition, update calendars/capacities and re-solve |

Machines, conveyors, washing lines and receiving plants can be Available, Disabled/Future where applicable, or subject to a physical rate cap for the whole run. Exact timed `UNAVAILABLE` and `RATE_CAP` windows model planned or sudden contingencies. Demand is never silently deleted: an unreachable campaign remains visible as unmet demand.

### 15.6 Implemented M16 stockpile-construction blending

The current implemented blend point is **construction of an M16 humid-phosphate stockpile before PAP/drying feed**. Several eligible incoming qualities are stacked into the selected stockpile; only after the campaign is complete may one reclaimer withdraw the homogenised campaign toward PAP‑1, PAP‑2 or DRY‑FEED. The model does not claim a direct simultaneous two-stream reclaim blend.

| Mode | Operator supplies | Planner decides |
|---|---|---|
| **Blend matrix** | fixed physical mass percentages for each eligible grade | feasible routes, equipment, timing and readiness |
| **Optimized shares** | eligible grades and min/max share bounds | integer physical tonnes and percentages **jointly** with inventory, competing stockpiles, routes, machines, calendars, outages, derates and the weekly schedule |

For campaign `p`, the exact construction closure is `Σ_g b[p,g] = batch[p]`; each selected grade respects its share bounds and dry-solids-weighted chemistry must satisfy the receiving interval. Scheduled stacking tonnes for every grade equal `b[p,g]`. Reclaim is prohibited before the exact `ready[p]` minute. Status progresses `NOT_STARTED → BUILDING → COMPLETE → DEPLETED`. A proposed but unexecuted matrix may change on replan; the first positive executed stacking movement locks the physical matrix and replanning preserves it.

A focused 12,000 t verification case produced one feasible incumbent of 1,000 t TBT plus 11,000 t BT‑LOW‑CD and held reclaim until the configured 48-hour readiness boundary. This is **test evidence, not a fixed product recipe**: optimized shares depend on the scenario, quality intervals, stocks and active resource controls. More detailed within-pile layer/zone composition during reclaim, Benguerir washed-product blending to BG‑TRAIN and raw-feed blending before washing remain future extensions.

### 15.7 ASP validation rules — severities, governance & quality limits

The vendor‑side severities matter for how the layer treats the second gate:

| ASP severity | Rules | Vendor behaviour | This layer |
|---|---|---|---|
| **ERROR** *(blocking)* | **Parameter sanity** · **Work area** · **Inventory availability** (mass within StartX–StopX + analysis availability + **most‑prevalent grade** = requested) · **Same‑device overlap** · **Work calendar** | plan item invalid; critical errors raise a notification | modelled as hard constraints (§15.4) — an AUTO item should never draw an ERROR |
| **WARNING** *(non‑blocking)* | **Device availability / busy** · **Spatial proximity** (1‑D min distance, `CurrentPileOnly` switch) | operator may proceed | treated as **hard constraints internally**; **project governance may promote selected ASP WARNINGs to blocking conditions in the external optimiser / dispatch gate** (configuration decision) |
| **INFO** *(advisory)* | **FIFO** (older same‑grade surface material first, incl. other piles) | advisory only | soft objective term (`age/FIFO`, §15.4) |

**ASP quality limitations (why blending is owned here).** For a reclaim item ASP checks inventory **mass**, **analysis availability** and the **most‑prevalent grade within StartX–StopX** — it does **not** prove that a simultaneous multi‑source blend meets **continuous SQP limits**. **One ASP job corresponds to one material grade**; the separate **YardMaster Blending Simulation** may display blend results but **has no role in ASP** (or its validation). The **SQP‑versus‑operator‑defined Sub‑Grade behaviour remains an unresolved ICD/configuration question** (the FRS states SQP is outside ASP, yet Sub‑Grades built on SQP combinations should reflect in ASP). Consequence: all blend/quality optimisation and continuous spec compliance are computed and guaranteed by **this layer**; ASP validates per‑job grade plausibility only.

### 15.8 Execution interface & closed loop — mapping to the vendor ASP (Digimine‑YardMaster)

Our **Job** maps directly onto the vendor's **AspPlanItem** (per FRS
Q80130‑MZI‑M16‑TAK‑45000183621‑DG §§3.1, 3.3–3.4, 3.6, 3.8). This defines the
**emission contract**: what the SMS can be asked to do, and with which
variables. Anything the layer decides that has no field below is *not*
transmissible and must be expressed as a decomposition of the items that are.

#### 15.8.1 Job-type catalogue — what the SMS can be asked to execute

`JobType` is a **closed enumeration of six values**. Each carries its own
mandatory-field set (FRS Table 28), and each `(Device, JobType, Location)`
triple must already exist as a configured **ASP Route** before the item is
offered or accepted (§14.2, FRS Tables 5–7).

| `JobType` | Physical meaning | Mandatory fields | This layer |
|---|---|---|---|
| **Stacking** | deposit material onto a pile from a configured source | device · pile · method · material · grade · source · start · StartX · StopX · PlannedTonnage · Rate | **emitted** — receipt and blend-construction work |
| **Reclaiming** | withdraw material from a pile to a configured destination | device · pile · method · material · grade · destination · start · StartX · StopX · PlannedTonnage · Rate | **emitted** — demand, corridor and train withdrawals |
| **Transport** | movement that is neither stack nor reclaim (tripper / transfer positioning) | device · pile · start · StartX · StopX · Rate | **not emitted in Phase 1** — modelled as route/gate state |
| **Scanning** | survey or volumetric re-scan of a pile section | device · pile · start · StartX · StopX · Rate | **not emitted** — consumed as a twin-refresh event |
| **Maintenance** | machine held at a fixed position by planned maintenance | device · position (**StartX = StopX**) · start · **end** | **not emitted** — read from the Work Calendar as a blocking constraint (§15.4) |
| **Other** | catch-all device occupancy with no material effect | device · position (**StartX = StopX**) · start · **end** | **not emitted** — respected as device unavailability |

Four consequences shape the allocation model:

1. **There is no blend job type.** A multi-source blend is only expressible as
   several single-grade Stacking/Reclaiming items — the structural reason
   blending is owned by this layer and not delegated to ASP (§15.7, §15.6).
2. **Transport and Scanning carry no tonnage and no grade.** They change no
   inventory in the twin, but they *do* consume the device and so participate
   in the same-device overlap and spatial rules.
3. **Maintenance and Other are degenerate-position items** (`StartX = StopX`)
   whose purpose is to reserve a machine, and they are the only two types that
   take an explicit **end time** instead of deriving one. The layer reads these
   as constraints rather than writing them — calendar authorship stays with
   operations (§8, H3).
4. **Only Stacking and Reclaiming close a mass balance**, so eq. (1) is driven
   exclusively by the two types the layer emits.

**"Not emitted" is not "not modelled."** All four non-emitted types still occupy
a device for their duration, and same-device overlap is an ASP **ERROR** — which
§15.7 requires an AUTO item never to draw. They reach the layer by **two
different channels, only one of which is currently wired**:

| Type | Channel | Status |
|---|---|---|
| Maintenance · Other | **Work Calendar** entries | already read (§15.4) |
| Transport · Scanning | **ordinary plan items**, via `AspPlanItemsRequest` | **gap — must be ingested as device occupancy** |

Maintenance and Other are calendar-borne, so the existing Work Calendar rule
catches them. Transport and Scanning are not: they are plan items like any
other, and a scanning pass authored by operations is **invisible to our
device-availability context** until ASP rejects our overlapping item at the
second gate. The context built in §15.1 must therefore ingest third-party plan
items of **every** type, not only calendar entries.

This is **one input path, not per-type logic**: ingesting every plan item on our
devices covers Transport, Scanning and any type added later without the layer
knowing what any of them are.

**Scanning needs no scan-specific handling.** A scan corrects pile geometry
against reality (`CurrentScanJob`, `SendPileShapeRequest/Response`), which can
step-change modelled inventory and could strand a reclaim sized on pre-scan
mass (UC-26). But that correction reaches the layer as an ordinary
**segment-level stock delta** through the existing inventory sync, so the §3
stock-change replan trigger already fires on it — attributing the cause adds
**operator explainability, not function**. Worth keeping the two gates distinct
nonetheless: scan is *geometry*, the analysis-availability check in UC-26 is
*assay*, and they arrive on different telegrams. Net scanning-specific
implementation in this layer is therefore **nil**; scan-on-demand ahead of a
critical campaign remains a post-Phase-1 extension.

**Method (`JobSubType`)** is a second enumeration nested under the job type —
the physical execution pattern. Three established facts about it combine into a
blocking gap:

- **It is mandatory on both types we emit.** Table 28 requires *"the method"*
  for `JobType = Stacking` **and** `Reclaiming`, and for no other type. No
  valid item of either emitted type can be constructed without a value.
- **The FRS does not enumerate it.** The valid set is a **runtime, per-device
  call** — `JobApiClientService.GetValidJobSubTypes` — so it is site
  configuration, not a specification constant. The FRS gives exactly **one**
  observed value: `Cone-shell`, on the Fig. 13 stacking item. Our own Louta
  process description gives a second — **chevron** on `110LG-ST-01` (§13.1).
  Two values from two documents; neither is a catalogue.
- **Therefore the method catalogues are a blocking integration item**, not a
  refinement. Until `GetValidJobSubTypes` is answered per device for M16, Louta
  and Benguerir, no Stacking or Reclaiming item can be emitted at all.

The layer makes no independent choice here — `Method` is a **pass-through** of
the configured stacking pattern (§15.1, `y[stacker, s, pattern, t]`), so
obtaining the lists is sufficient and no model change follows from them.

> **Blending mode is not a `JobSubType`.** The matrix / optimized-share choice
> of §15.6 is a **planner-side input** deciding how a campaign is composed; it
> never crosses the interface. Because one ASP job carries one grade and there
> is no blend job type, a blend campaign leaves this layer as **N ordinary
> single-grade Stacking items**, each with its own `GradeId`, tonnage and
> `Method`. The SMS is never instructed to blend: it receives N stacking jobs
> onto one pile, and the resulting mixture is an emergent property of its own
> inventory model — which is precisely why continuous spec compliance has to be
> guaranteed here and cannot be delegated to the second gate (§15.7).

#### 15.8.2 Plan-item attribute set — the variables a job can carry

| `AspPlanItem` field | Type | Our model | Constraint / note |
|---|---|---|---|
| `Title` | string | job label | free text, **not** an identifier; the layer prefixes `AUTO-` (H3) |
| `DeviceId` | int | Machine | must be in the site device register and reach the pile |
| `JobType` | enum | job type | one of the six above |
| `JobSubType` | string | stacking / reclaim pattern | "Method"; per-device valid set |
| `PileId` | int | StockSpace | Stacking/Reclaiming/Transport/Scanning only |
| `MaterialId` | int | MaterialProfile | Stacking/Reclaiming only |
| `GradeId` | int | QualityClass | Stacking/Reclaiming only; **one job = one grade** (§15.7) |
| `ReferenceLocation` | string | route endpoint | source when stacking, destination when reclaiming; must match a configured `AspRoute` |
| `StartLocal` | datetime | job start | the **only** supplied time for material jobs |
| `StartX` / `StopX` | double [m] | repère range, converted | must lie inside the device × pile × job-type **work area** |
| `Layer` | int | vertical lift (§5.1.1) | selects the Z band read by the FIFO/age rule |
| `PlannedTonnage` | double [t] | quantity | `> 0`; not carried by Transport/Scanning/Maintenance/Other |
| `PlannedFlowRate` | double [t/h] | rate | `> 0` and ≤ the device maximum |
| `Comment` | string | allocation rationale | free text; the layer writes its decision reason here |
| `PlanAction` | enum | CRUD verb | create / update / **Delete** |
| `JobId` | int | link to the YardMaster job | set on dispatch; **once linked, the item can no longer be edited or deleted** |
| `Requestor` | enum | telegram origin | `JobUpdate` origin suppresses re-notification loops |

**Derived, not supplied.** ASP computes `PlannedEnd = Start + (Tonnage / Rate)`
during item enrichment. Duration is therefore **not an independent variable**:
the layer controls a job's end time only through tonnage and rate, and any rate
optimism propagates directly into every successor on that device or route.

**Grade is declared per job, never targeted.** `GradeId` names the single grade
*that job moves* — there is no field anywhere on the item for a **resulting**
or **output** grade. On **Reclaiming** it is verified against reality: UC-26
rejects the item unless the **most prevalent** grade within `StartX–StopX`
equals it. On **Stacking** it is explicitly a *planned* value — Table 17 permits
stacking a grade **not currently present in the stockyard** and notes these are
"planned grades and may be changed when the actual quality is known". Emitted
stacking items therefore carry a **forecast** grade that the SMS expects to
correct against assay; the layer must follow that correction in reconciliation
rather than assume its planned grade survives execution.

**Returned on the same item.** `ActualStartDate`, `ActualEndDate` and
`ForecastedEndUtc` are written back onto the plan item from `JobUpdated`
(UC-33), with the effective rate derived differently per type — from moved mass
for Stacking/Reclaiming, from distance travelled for Transport/Scanning, and
from elapsed time alone for Maintenance/Other. The layer's rate-shortfall and
end-slip thresholds must therefore be **type-aware**; a single tonnage-based
deviation test would be meaningless on four of the six types.

The UI and operating plan display `R_start–R_stop`; the adapter converts
those values to physical `StartX/StopX` using the selected pile's approved
R0/R1000 calibration. The optimiser and all distance-based rules continue to
use metres.

**Dispatch loop:** the layer submits plan items (`AspPlanItem` telegram) → ASP validates against its 8 rules and returns `AspEvaluatePlanResponse` (second feasibility gate, §15.7) → jobs are **auto time‑triggered** to YardMaster job management with operator **Accept/Reject** (human‑in‑the‑loop) → **`JobUpdated` telegrams return actuals** → the layer re‑validates and **re‑plans** on deviations.

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

### 15.9 Implemented operator workflow, diagram and results

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

## 16. Evidence & Traceability Register

Status vocabulary: **Confirmed** (stated in a referenced document) · **Derived** (computed from confirmed values) · **Inferred** (working hypothesis, confirmation required) · **Conflicting** (two sources / internal inconsistency, preserved) · **TBC** (awaiting document/decision). Confidence H/M/L.

| Site / domain | Parameter / rule | Value [unit] | Source · rev · ref | Status | Conf. | Design impact | Owner / action |
|---|---|---|---|---|---|---|---|
| MPH · M16 | humid open storage | 8 spaces × 125,000 t (→ 12 future) | PRO‑0001 / PFD‑0001 [3] | Confirmed | H | yard capacity model | — |
| MPH · M16 | machine lines, bays & reachability | **FOUR two-hangar bays with the machine lines interleaved between them**: `M16A-A-T06` (M16A-A-ST01, 2,600 TPH) · bay 1 · `M16A-A-T11` (M16A-A-RC01, 2,500 TPH) · bay 2 · `M16A-B-T06` (M16A-B-ST01) · bay 3 · `M16A-B-T11` (M16A-B-RC01) · bay 4 · `M16A-C-T06` (M16A-C-ST01). The mimic draws each bay as **one side-by-side horizontal hangar pair**, with every serving line spanning both members (display ruling 26-Jul-2026). **No shared T06/T11 corridor.** Reach follows the project-team bay-adjacency rule: ST01-A→bay 1 · RC01-A→bays 1-2 · ST01-B→bays 2-3 · RC01-B→bays 3-4 · ST01-C→bay 4 — never all-to-all. FUTURE continues bay 5 · `M16A-C-T11` (M16A-C-RC01) · bay 6 · `M16A-D-T06` (M16A-D-ST01) + 4 × 125,000 t hangars | `Q80130-MZI-M16-ME-PFD-0001[3]` Rev 3, IFU, 28-Oct-2025 + project-team reach rule 25-Jul-2026 + display ruling 26-Jul-2026 | **Machine identities, rates, lines and the bay sequence CONFIRMED**; hangar names and left/right order inside a bay **INFERRED**; metric positions schematic (flow sheet, not to scale); horizontal pairing is geometry-only and changes no solver input | H | machine set, machine lines, `reachable_spaces`, solver candidates, ASP work-area rule and the mimic | plot plan / surveyed GA + official SMS pile-ID register + per-line travel limits |
| MPH · M16 | same‑pile concurrent stack+reclaim | permitted subject to spatial separation, work‑zone, route, geometry and ACS/PLC constraints; numerical safety limits TBC; Louta/Benguerir applicability configurable/TBC | project‑team confirmation, 15‑Jul‑2026 | Confirmed | H | scheduler concurrency rule (§15.4) | obtain numerical separations (YardMaster/ACS/machine supplier) |
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
| MPH SMS · ASP | route semantics | `AspRoute` = (Device, JobType, free‑text Location) only — no network model | FRS §3.1 | Confirmed | H | layer owns route graph (§14.2) | ICD |
| MPH SMS · ASP | rule severities | 5 ERROR · 2 WARNING · 1 INFO (§15.7) | FRS UC‑22…30 | Confirmed | H | validation‑gate design; warning promotion | governance decision |
| MPH SMS · ASP | **job-type enumeration** | closed set of **6**: Stacking · Reclaiming · Transport · Scanning · Maintenance · Other, each with its own mandatory-field set; only Stacking/Reclaiming carry tonnage + grade; Maintenance/Other are `StartX = StopX` with an explicit end | FRS §§3.1, 3.4 Tables 5, 26, 28; UC‑12…18, UC‑33 | Confirmed | H | emission contract (§15.8.1); no blend job type, so blending stays layer‑owned; mass balance driven only by the 2 emitted types | confirm per‑site configured `AspRoute` tuples and permissions |
| MPH SMS · ASP | **`JobSubType` / Method catalogue** | Second enumeration nested under job type; **mandatory for Stacking and Reclaiming** (Table 28) and for no other type. Valid set is a runtime per‑device call `GetValidJobSubTypes`. Only two values are attested anywhere in project evidence: `Cone-shell` (FRS Fig. 13) and `chevron` (Louta process description, §13.1) | FRS Table 26, Table 28, Fig. 13, UC‑15; Louta process description | Mechanism Confirmed; **the per‑site value lists are TBC and the gap is BLOCKING** | H | no Stacking or Reclaiming item can be emitted without a valid value; `y[stacker, s, pattern, t]` is a pass‑through, so the lists alone unblock it — no model change follows | **obtain configured method lists per device for M16 / Louta / Benguerir — prerequisite to any emission** |
| MPH SMS · ASP | **plan‑item attribute set** | Title · DeviceId · JobType · JobSubType · PileId · MaterialId · GradeId · ReferenceLocation · StartLocal · StartX/StopX · Layer · PlannedTonnage · PlannedFlowRate · Comment · PlanAction · JobId · Requestor; `PlannedEnd` **derived** as `Start + Tonnage/Rate`; actuals returned onto the same item | FRS Table 26 (field spec), Fig. 12, UC‑11/18/21/22/30/33 | Confirmed | H | §15.8.2; duration is not an independent decision variable, and deviation thresholds must be job‑type‑aware | ICD for wire types, units and enum encodings |
| All stockpiles · SMS/ASP | **`Layer` — vertical coordinate** | integer lift index through the pile height, independent of `StartX/StopX`; `(StartX–StopX, Layer)` select a body of material within **one** pile (single `PileId`); FIFO/age is evaluated *on the layer* | FRS Fig. 22, UC‑30, Fig. 12, Tables 26, 52 | Confirmed as a field, as single‑pile in scope, and as the FIFO basis; **numbering origin/direction, relation to `LevelNr`, and per‑pile layer bounds are TBC** | M | §5.1.1; layer choice — not only pile choice — decides FIFO compliance; multi‑pile campaigns decompose into sequenced single‑pile jobs; gates within‑pile layer/zone reclaim composition (§§15.6, 17) | ICD: layer indexing, bounds source and `Layer` vs `LevelNr` |
| MPH SMS · ASP | **buried‑layer reclaim enforcement** | UC‑26 validates mass, analysis availability and prevalent grade within `StartX–StopX`; **no rule checks overburden above the requested layer**, so it is unknown whether a buried‑layer request is rejected or passes | FRS UC‑26, UC‑30 ("on the surface of the pile") | **Unresolved — absence of evidence, not evidence of absence** | M | surface‑consistency must be enforced layer‑side (§5.1.1); cannot be delegated to the second gate | ICD / vendor workshop: does ASP constrain the reclaimable layer to the current surface? |
| MPH SMS · ASP | **`Layer` binding on reclaim vs stack** | Read on **both** emitted types — UC‑30 fires only on Reclaiming and dereferences `AspPlanItem.Layer`; Fig. 12 shows `Layer 4` on a Reclaiming job. But UC‑26 sizes the cut from available mass *"between the position range"* + planned tonnage with **no reference to `Layer`**, so on reclaim it reads as an **age/quality key, not a volume bound** | FRS UC‑26, UC‑30, Fig. 12 | **Inferred from rule wording; not stated explicitly — TBC** | M | §5.1.1; a reclaim job cannot be assumed confined to its named layer, so layer‑selective withdrawal must not be modelled as achievable via `Layer` alone | ICD: does `Layer` bound the reclaimed volume, or only the age/quality lookup? |
| MPH · M16 | **reception path — report claim withdrawn** | Report §13.2 asserted C1 and C2 **share** one 2,600 TPH reception path, capping aggregate reception. The model implements **two independent 2,600 TPH links** into `TOWER:M16-REC` plus eight 2,600 TPH per-pile stacking links: aggregate **5,200 TPH**, both corridors simultaneous | model `sc.links` inspection 28-Sep-2026 + project-team confirmation | **Divergence resolved in favour of the model; report corrected** | H | reception is not a constraint; idle M16 stackers are machine redundancy, not capacity | reconcile PRO-0001 sampler evidence with the as-built conveyor count |
| All washed grades | **CO₂ assay absent — structural PAP blocker** | The twelve washed merchant vectors carried **no CO₂ at all** while G10 requires CO₂ ∈ [6.0, 6.5]; a missing parameter fails qualification outright, so **no mine-produced grade could ever be received at PAP**. Raw grades do carry CO₂ (6.54–8.75) — it was lost in the raw→washed mapping | `_phos_grades`, `_WASHED_CD_VARIANT_QUALITY`, `washing.DEFAULT_LIMITS` | **Confirmed defect; corrected 28-Sep-2026** | H | CO₂ added and Cd/MgO rebased into the G10 window; Cd keeps its raw lineage (×25/21, ×13/21). PAP went 0% → 99% servable | replace ASSUMED vectors with the approved washed-product grade register |
| All sites | **machine minimum flow rate mis-encoded** | 13 of 20 machines carried `rate_min_tph = 500`, encoding the *blending* rule (no less than 500 t of a grade per job) as a machine flow floor. Normal stacking/reclaiming has no minimum rate. Because final-unit demand is an hourly equality, PAP's 230 t/h duty sat below the floor and received **zero** | project-team correction 28-Sep-2026; `min_input_grade_t` already holds the real blending rule | **Confirmed defect; corrected** | H | cleared to 0 on all twenty machines; PAP 0% → 98.8% in the same run | none — the blending minimum is unchanged in the chevron config |
| Objective | **`service_first` discards the weighted block** | `primary = (service_bound+1)·emergency + qualified_service`; `sum(terms)` — w₁–w₇, corridor targets, safety-stock preference — is not used in the primary solve. Destination priority is a WEIGHT (higher served first, 0 disables), so PAP at 1 ranked below drying 2 and train 3 | `optimizer.py` objective assembly; measured PAP 0/38,500 while drying took 80% | **Confirmed; report §15.2 corrected and priorities reordered to PAP 3 > drying 2 > train 1** | H | tuning w₁ changes nothing in this mode; corridor intentions carry no objective weight | decide whether corridor dispatch should be priced in `service_first` |
| Corridors | **production reaches the hub only in trickle** | Mines produce **93,800 t/week** after the calendar fix but only **16,529 t** is dispatched to M16, which draws itself down 33,408 t. Corridor targets are unpriced in `service_first`, and M16 opening stock covered most of the week | measured 28-Sep-2026 on FULL_FLOW | **Confirmed, open** | H | DRY-FEED shortfall is a pull problem, not a supply or quality problem | price corridor dispatch, or extend soft M16 terminal floors (tested: +36% corridor flow, DRY 68%→76%, PAP 98%→93%) |
| MPH SMS · ASP | **third‑party job occupancy — ingestion gap** | Maintenance/Other block a device via **Work Calendar** entries (already read); **Transport and Scanning block it as ordinary plan items** and reach us only via `AspPlanItemsRequest`. ASP's own evaluation context includes *all* plan items for the device (UC‑22 step 2a, UC‑27), so it will reject our overlap even though we never saw the blocker | FRS UC‑22, UC‑27, Table 28 | **Confirmed gap in our ingestion, not in the vendor spec** | H | device‑availability context (§15.1) must ingest third‑party plan items of every type, else AUTO items draw same‑device‑overlap ERRORs that §15.7 forbids | wire `AspPlanItemsRequest` ingestion for non‑emitted types; confirm who authors Transport/Scanning at each site |
| All stockpiles · SMS/ASP | **scan geometry correction** | A Scanning job corrects pile **geometry** against reality (`CurrentScanJob`, `SendPileShapeRequest/Response`); the correction can step‑change modelled inventory and strand a reclaim sized on pre‑scan mass (UC‑26). Distinct from the **quality** analysis that also gates reclaim — scan is shape, analysis is assay, different telegrams | FRS §3.7, UC‑26, UC‑32 telegram set | Confirmed mechanism; **scan cadence and authorship per site TBC** | L | **no scan‑specific logic required**: the correction arrives as a segment‑level stock delta and the existing §3 stock‑change trigger already fires on it; cause attribution is operator explainability only | confirm scan cadence and typical volumetric correction magnitude, to check the stock‑change replan threshold is not tuned only for gradual drift |
| MPH SMS · ASP | **`Layer` authorship — can we set it?** | Absent from the plan‑editor form spec for **either** job type (Tables 26 and 28); present in the **YardMaster job** dialog (Fig. 12, reached via UC‑11 → job management) and in `JobUpdated` (Table 52). Yet UC‑30 dereferences `AspPlanItem.Layer`, so the plan item carries the property the editor never collects | FRS Tables 26, 28, 52; Figs. 12, 24; UC‑11, UC‑30 | **Unresolved — writability not established** | M | decides whether layer‑aware authoring is available to this layer at all, or whether `Layer` is populated downstream by job management / MOM PLC at execution | ICD / vendor workshop: is `Layer` settable on the `AspPlanItem` telegram, and who owns it? |
| All stockpiles · SMS/ASP | **transverse (Y) selection** | Pile surfaces outcrop **every** layer along the toe/flank (repose 30–35°), but the plan item offers no Y selector — exposed toe and buried core of one layer are indistinguishable; partial‑layer cuts are expressible in X only | FRS Table 26, Fig. 12, Fig. 22; §13.4 repose angles | Confirmed (by absence of any Y field) | H | §5.1.1; blocks selective within‑layer/zone reclaim composition (§§15.6, 17); consistent with face‑cutting machines taking the full cross‑section | confirm reclaimer cutting method per machine (face cut vs bench) from supplier data / GA |
| MPH SMS · ASP | quality model | 1 job = 1 grade; SQP outside ASP; Blending Simulation separate ("no role in ASP"); Sub‑Grade behaviour unresolved | FRS Q&A | Confirmed / **Unresolved (SQP/Sub‑Grade)** | M | blending owned by this layer | ICD / configuration question |
| MPH SMS · ASP | inventory cache | per‑pile 1‑m X segments (mass · prevalent grade · age), ~10‑min or on‑demand refresh | FRS | Confirmed | H | twin sync design | — |
| All stockpiles · SMS/ASP | longitudinal job location / repère | Operator scale **R0–R1000 = 1,000 equal intervals**; jobs use a range such as **R400–R600**. Physical X metres remain canonical and are calculated per pile. | Start/End positions and 1‑m inventory: Innomotics FRS Table 26, UC‑22/23/24/26/28/30 and §3.7.1; repère name/scale: project team 24‑Jul‑2026 | **FRS position model Confirmed; repère convention User-confirmed; physical calibration TBC** | H/M | job authoring, grade placement, machine display, inventory selection and spatial checks | obtain each pile's R0 endpoint/direction, surveyed origin/usable length and production ICD StartX/StopX units/scale |
| Simulator mimic | Initial / Final state and planning controls | **Initial** = scenario t0; **Final** = terminal weekly inventory. Physical fill is proportional; hover exposes component mass/quality. Louta 102MG remains reference-only; the six Benguerir piles and Louta 110LG/product spaces are solver-backed. | Project-team UI instructions and implementation through 8-Sep-2026 | Implemented engineering behaviour; scenario values are not live telemetry | M | shared Monitor/Routing diagram, state/rate controls and terminal-plan inspection | validate against live SMS/SQMS and surveyed geometry |
| Interfaces | RabbitMQ telegram schemas | `AspPlanItem` / `AspEvaluatePlanResponse` / `JobUpdated` / `SpaceValueRequest/Response` — ICD not yet received | FRS (hypothesis H1) | TBC | — | integration design | request ICD (vendor workshop) |
| Economics | GradeCostModel curves | PAP specific consumptions vs feed grade + rock values per quality | — | **Required, not received** | — | w₃/w₄ objective terms | process engineering / finance |

## 17. Simulation Readiness

What the allocation/simulation model needs, by readiness class:

| Class | Items |
|---|---|
| **Implemented engineering MVP** | deterministic weekly CP‑SAT planning; dated multi-campaign demand; physical inventory/rate constraints; washing yield/moisture conversion; editable grade catalogue and receiving intervals; timed equipment/conveyor/line controls; global status diagram; results/KPI and day-filtered job schedule; M16 fixed/optimized pile-construction blending. |
| **Configurable assumptions** (visible and editable) | travel/setup/minimum separation · weekly demand campaigns · initial inventory and pile distributions · yield/moisture profiles · chemistry vectors and receiving intervals · work calendars · equipment/routing states and physical rate caps · objective weights. Demonstration B02 defaults are MT 75.73%, BT 66.63%, TBT 62.64%, raw moisture 12% and wet-product moisture 17.5%; these are assumptions, not live measurements. |
| **Required before a realistic operational simulation** | timestamped SMS/SQMS stocks and assays · validated grade-class intervals and raw↔washed mappings · per-pile repère calibration and metric GA · route confluences/capacities, reach and transition data · current machine/conveyor/plant state and calendars · validated washing residence/yield/moisture by campaign · drying intake · approved operating contingencies and a real GradeCostModel. More detailed within-pile layer/zone reclaim composition and the two non-M16 blend points are not implemented. |
| **Required only before live SMS integration** | RabbitMQ ICD / telegram schemas + credentials (H1), including confirmed StartX/StopX units, axis origin/orientation and scale · **configured `GetValidJobSubTypes` method catalogues per device (blocking: `Method` is mandatory on every Stacking and Reclaiming item, §15.8.1)** · `Layer` writability and binding semantics (§5.1.1) · site‑specific mine‑SMS configuration (H2 **resolved at solution level — identical Innomotics/YardMaster SMS at all three sites, user‑confirmed**; per‑site endpoints, queue/exchange names, Device/Pile IDs, Material/Grade/SubGrade mappings, AspRoutes and permissions remain TBC) · plan co‑authoring governance & write access (H3) · final configured Material/Grade/SQP/Sub‑Grade behaviour · LIMS/CMS/ERP endpoints · OT/IT & cybersecurity engineering |

**Rev AC verification history (8 September 2026).** The focused blending/integration suite reports **61 passing tests**. A real seven-day default solve returned FEASIBLE with exact 12,000 t blend construction and no reclaim before the 48-hour boundary. The broader repository run reported **755 passes and 55 legacy/baseline failures**; therefore this report does not claim that every historical UI/report assertion is green.

**Rev AF verification status (28 September 2026) — NOT YET RE-VERIFIED.** The
Rev AC figures above are superseded and must not be quoted: the suite now
collects **1,467 tests**, roughly double, so "755 passes and 55 failures" is
not comparable. The last complete run (27-Sep-2026) reported **105 failed /
1,362 passed in 11 h 25 m**, and was the **first complete run in this
environment** — CP-SAT previously aborted the whole process at 4% inside
`test_benguerir_b02` (`Check failed: heuristics.fixed_search != nullptr`,
triggered by `repair_hint`; now fixed). Most of those tests had therefore never
executed, so "newly visible" is not "newly broken": the five
`test_benguerir_b02` failures were byte-identical with the change reverted.

**Rev AF regression run (29-Sep-2026, quiet machine): 136 failed / 1,331 passed
in 8 h 46 m.** Against the 105/1,362 baseline that is **+36 new, −5 fixed, net
+31**. The new failures are rebaselining work, not regressions — they assert
superseded values — and they cluster exactly on what changed:

| cluster | new failures | cause |
|---|---:|---|
| `test_weekly_demand_backend`, `test_demand_plan_units`, `test_weekly_demand_ui` | 8 | demand rescaled to plant duty |
| `test_m16_bays`, `test_m16_model_consistency` | 8 | M16 seed grades and 424,000 → 86,500 t opening stock |
| `test_inventory_snapshot`, `test_flow_configuration`, `test_plan_configure_contract` | 7 | same demand/seed changes |
| `test_schedule_fragmentation` | 3 | `rate_min_tph` cleared — one is literally `test_a_credible_parcel_is_labelled_by_its_machine_rate_floor` |
| `test_chevron_stacking` | 2 | blending matrix now active in FULL_FLOW |
| scattered | ~8 | — |

**Five tests started passing**, and two are independent confirmation rather
than noise: `test_both_louta_reclaimers_are_used_without_bucket_overlap` and
`test_s0_policy_generates_purposeful_work_for_all_six_current_louta_assets`.
Those assert Louta-chain activity, and they went green because unpinning the
stocked route and running both washing plants daily genuinely put that
equipment to work.

**Until the 31 are rebaselined, no KPI in this revision is verified.** The
rebaseline must check each assertion still tests its original intent rather
than simply swapping in the new number.

## 18. Acceptance Criteria (measurable)

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
| 11 | Deterministic replay | re‑running a stored scenario (inputs + config + seed) reproduces the identical plan | **exact match** — now achievable: `SOLVE_MODE = "reproducible"`, verified identical plan hash across repeated runs (§12.1) |
| 12 | Multi-campaign daily demand | all same-unit/day campaign quantities, windows and grades survive input→solve→result reconciliation | **exact conservation; no positive-window overlap** |
| 13 | Timed contingency response | outage/derate at a non-grid minute changes no work before the boundary and produces no use above availability/rate after it | **0 violations** |
| 14 | Blend construction integrity | scheduled grade tonnes equal selected matrix, total equals batch, all bounds/quality intervals hold and no reclaim starts before exact readiness | **exact integer-tonne closure; 0 early reclaim** |
| 15 | Stage mass traceability | physical raw, TSM and wet-product values reconcile with the selected yield/moisture profile | **within configured numeric tolerance** |

## Abbreviations

| Abbrev. | Meaning |
|---|---|
| ACS | Anti-Collision System |
| ASP | Advanced Stockyard Planning (Digimine-YardMaster module) |
| BPL | Bone Phosphate of Lime (phosphate grade) |
| CMS | Computerised Maintenance / Asset-Management System |
| DCS | Distributed Control System |
| GA | General Arrangement drawing |
| HMI | Human-Machine Interface |
| ICD | Interface Control Document |
| LIMS | Laboratory Information Management System |
| MES | Manufacturing Execution System |
| MPH | Mzinda Phosphate Hub |
| MTPY | Million tonnes per year |
| OPC UA / HDA | OPC Unified Architecture / Historical Data Access |
| PAP | Phosphoric Acid Plant |
| PFD | Process Flow Diagram |
| PLC | Programmable Logic Controller |
| PSD | Particle-Size Distribution |
| SMS | Stockpile Management System |
| SPH | Safi Platform Hub |
| SQMS | Stockpile Quality Management System |
| TBE | Technical Bid Evaluation |
| TBC / TBD | To Be Confirmed / Determined |
| TPH | Tonnes per hour |
| TSM | Zero-moisture merchant dry mass |

---
*Working draft for scope and functional-design review. Evidence classifications and unresolved actions remain authoritative in §16.*
