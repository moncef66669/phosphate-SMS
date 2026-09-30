# Project Scope — Automated Job Allocation Layer for the Stockpile Management System (SMS)
### Louta & Benguerir Mine Stockyards → MPH M16 Hub · M17 Sulphur — SP2M Program

**Status:** Draft implementation baseline  **Rev.:** AC  **Date:** 8 September 2026 (first issue 22 June 2026)

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

**Reception envelope — one current path:** PRO-0001 §2.1.1 places auto sampler `M16VP01` on `M16A-A-T01` and `M16VP02` on "the adjacent **future** conveyor", and the PFD marks that line FUTURE. C1 and C2 therefore **share one current 2,600 TPH reception path**: aggregate reception is capped at 2,600 TPH, never 2 × 2,600 TPH, and the initial in-transit profile shows the corridors running alternately rather than both at full rate in the same hour.

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

| Demonstration grade | BPL | Cd ppm | MgO % | SiO₂ % | Moisture % | Status |
|---|---:|---:|---:|---:|---:|---|
| TBT | 74 | 22 | 0.8 | 2.0 | 12 | washed-product simulation placeholder |
| BT | 68 | 30 | 1.1 | 2.8 | 12 | washed-product simulation placeholder |
| MT | 63 | 35 | 1.4 | 3.5 | 12 | washed-product simulation placeholder |

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
