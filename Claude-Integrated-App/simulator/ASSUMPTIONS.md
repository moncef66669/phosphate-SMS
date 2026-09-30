# Assumptions Register — simulator MVP

Everything below is ALSO visible live in the app (page 12 — Assumptions &
Data Gaps) and stored inside each scenario snapshot. Status vocabulary
follows report **Rev D** §16: values marked **ASSUMED**/**TBC** are
placeholders for simulation only — never engineering facts.

Scenario roles (report Rev D): **S3 is the primary Domain-A scenario** —
the three-site chain Louta →C1→ M16 ←C2← Benguerir. **S2 is an M16-only
demonstration**: its corridor arrivals are simulated inputs and the mine
yards are not modelled. In S3, the Benguerir B01 mine/screening interfaces
remain distinct from the downstream B02 washed-product yard, but both are now
connected by the solver-backed six-pile planning chain described below.

## A. Dataset assumptions (per scenario)

### S1 — Sulphur minimal
| id | assumption | placeholder | action |
|---|---|---|---|
| S1-A1 | Melting demand 800 t/h constant | demand table | obtain melting feed plan |
| S1-A2 | Sulphur assay 99.5 % S / 0.5 % H₂O | grade S-STD | reception assay data |
| S1-A3 | Initial pile masses 60/40/55 kt | spaces table | SMS SpaceValue snapshot |
| S1-A4 | 8 start-stops/h is sub-bucket → only penalised (w₅) at Δt = 1 h | w₅ | sub-hourly dispatcher iteration |

### S2 — MPH M16 hub only (simulated corridor arrivals)
| id | assumption | placeholder | action |
|---|---|---|---|
| S2-A1 | Pile contents/grades & initial masses | HP-01…08 table | SMS SpaceValue snapshot |
| S2-A2 | PAP-1/PAP-2 demand G10 only. Project-team supplied profile values are implemented as hard intervals: BPL 62.5–100%, Cd 12–18 ppm, MgO 0–0.8%, SiO2 0–7.5%, CO2 6–6.5%, moisture 0–25%. They remain non-approved model inputs, and the neutral BPL ceiling of 100% only represents the absence of a supplied upper bound. | version-6 G10 receiving profile | approved contractual G10 feed specification from process |
| S2-A3 | Drying intake **1,299 t/h per conveyor CONFIRMED** (PRO-0001 §2.1: two parallel conveyors to the drying unit, each 1,299 t/h, both OUT OF SCOPE); DRY-FEED demand 800 t/h remains a placeholder | one 1,299 t/h link per gate bank; demand 800 t/h | drying-plant datasheet (OOS plant) + agreed intake profile; the 10-minute gate/route changeover stays documented-but-NOT-modelled |
| S2-A4 | **PAP / drying ROUTE SELECTION is DERIVED/TBC, not confirmed equipment behaviour.** `PFD-0001[3]` confirms the physical discharge chain per stream — `T11` → `T11S` separator → `T12` (belt scale) → hopper `M16A-A-R01` + air blasters → clamshell/rod gates `M16A-A-H01…H04` / `M16A-B-H01…H04` → `T13` 1,500 t/h — modelled as the **CONFIRMED** links `L-H-A` / `L-H-B`. It does **not** state that the H gates select PAP versus drying, nor where the routes converge, so the destination choice is a separate downstream route/diverter abstraction (`L-RTE-{A,B}-{PAP,PAP2,DRY}`, evidence **DERIVED**, drawn dashed amber). Confirmed belt RATINGS (PAP 6 × 1,500 t/h, 3 running + 3 future; drying 2 × 1,299 t/h, out of scope — PRO-0001 §2.1) do not confirm the ASSIGNMENT. Per-bank mutual exclusion is DERIVED from the single outbound `T13`. The documented 10-minute changeover is **NOT MODELLED** (`changeover_min = 0`; no inter-bucket switching loss) | `HUB:M16-RCL-{A,B}` → `L-H-{A,B}` → `HUB:M16-T13-{A,B}` → `L-RTE-*` + one conflict group per bank | downstream PFD/GA giving the gate→conveyor→destination assignment, the confluence point and the real changeover behaviour |
| S2-A5 | **ASSUMED positioning inputs:** schematic x-geometry 0–300 m, travel 30 m/min and setup 10 min. The minute schedule uses these values only for cross-space transitions: a green non-material bar means the estimate fits; a red positioning conflict means it does not. First-job position, surveyed rail-global coordinates and live travel telemetry are unavailable; same-space face progression remains embedded in the production job. | uniform local ranges + `Machine.travel_speed_m_min` / `setup_min` | plot plan / GA, surveyed common rail frame, operating travel/setup data and live machine position |
| S2-A6 | Corridor-arrival stand-in 2000 t/h × 6 h alternating TBT/BT | belt profile | use S3 for real corridors |
| S2-A7 | TBT/BT/MT vectors are demonstration quality profiles, not approved assays. Their BoD semantic order is corrected to **TBT < BT < MT**; the numeric vectors remain placeholders. Raw/screened phosphate uses the separate mine-neutral `RAW-TBC` fallback when its grade is pending and never inherits these vectors | `material_profiles` + grade vectors | approved raw and product assay tables + grade cut-offs + SMS mapping |
| S2-A8 | **M16 hangar numbering and left/right order inside the bays are INFERRED.** The PFD confirms eight 125,000 t hangars in FOUR two-hangar bays with the machine lines interleaved between them, but does not name the hangars; `HP-01…08` is the application's working convention (bay 1 = HP-01/HP-05 · bay 2 = HP-02/HP-06 · bay 3 = HP-03/HP-07 · bay 4 = HP-04/HP-08). Per project-team display ruling 26-Jul-2026, each bay is drawn as one side-by-side horizontal pair and every serving line spans both hangars; this is schematic geometry only. Machine reach applies the project-team bay-adjacency rule (25-Jul-2026) | `M16_BAYS` + `M16_MACHINE_LINES` → `Machine.reachable_spaces`; `_m16_section()` horizontal-pair geometry | plot plan + official SMS pile-ID register. With Future expansion the same adjacency rule extends ST01-C to bays 4-5 and adds `M16A-C-RC01` (bays 5-6) and `M16A-D-ST01` (bay 6); `HP-09*…HP-12*` are INFERRED display-only identifiers, never solver spaces |
| S2-A9 | **CURRENT M16 reception is ONE shared 2,600 t/h envelope, not 2 × 2,600 t/h.** PRO-0001 §2.1.1 puts auto sampler `M16VP01` on `M16A-A-T01` and `M16VP02` on *the adjacent **future** conveyor*, and `PFD-0001[3]` marks that line FUTURE — so C1 and C2 share one current reception path. Modelled as a shared-capacity group on every stacking leg: total M16 stacking ≤ 2,600 t/h per bucket whichever corridor the material came from. **Consequence:** the S3 initial in-transit profile now shows the corridors running alternately instead of both at full rate in the same bucket | `shared_group="M16A-A-T01"` on `L-STK-HP-01…08` | confirm the second reception line's phase + the reception buffer/interlock rules |
| S2-A10 | **PAP-2 is an ASSUMED planning option requested by the project team.** It retains the same 1,500 t/h, G10 and 36,000 t/24 h template as PAP-1. It defaults to `Future`, which keeps its routes unavailable and its active demand at zero without deleting the template. `Available` activates both bank routes. It does not add another T13 bank: PAP-1 + PAP-2 + DRY-FEED would request 91,200 t/day against the current 72,000 t/day outbound envelope. | `PAP-2`; `L-RTE-{A,B}-PAP2`; `DESTINATION_LINE_OPERATING_STATE` | confirm PAP-2 tag, commissioning phase, route assignment and the approved demand combination |

### S3 — Three-site phosphate value chain: Louta + Benguerir → M16
| id | assumption | placeholder | action |
|---|---|---|---|
| S3-A1 | Louta L02 product storage: total 383,024 t + reclaimers `140LG-RC-01/-02` @ 3,000 t/h nom = design-basis figures; equal two-lane capacity split **2 × 191,512 t** is DERIVED/TBC. Project-team demonstration instruction 24-Jul-2026 sets each starting mass to **95,756 t (50%)**. | LT-P1/P2, 140LG-RC-01/-02 | timestamped SMS masses + full drawing/legend/route matrix |
| S3-A8 | **L02 RECLAIMER COUNT CONFLICT applies to both storage stages.** BoD Rev B p.20 lists one ore and one product stockpile reclaimer; the later PFD-linked, equipment-tagged process description specifies two ore reclaimers (`120LG-RC-01/-02`) and two product reclaimers (`140LG-RC-01/-02`). S0 deliberately uses the later tagged configuration for planning, while retaining the conflict. One-reclaimer-per-planning-lane reach is conservative APPLIED geometry because neither physical pairing is stated. | solver: `110LG-ST-01`, `120LG-RC-01/-02`, `130LG-ST-01`, `140LG-RC-01/-02`; conservative lane pairings | final mechanical equipment list + GA/reach matrix; reconcile BoD and process-description counts |
| S3-A9 | C1 feeder selection: either `140LG-RC-01` or `-02` feeds C1 (3,000 vs C1 2,600 t/h); simultaneous partial-rate reclaim only if confluence permits — TBC → exclusive feeder group (config, not hard-coded) | Site.exclusive_machine_groups | confirm RL3/RL4 confluence policy |
| S3-A2 | Benguerir downstream '3 qualities on 2 piles' — from the EARLIER conveying-system datasheet only (NOT established by the B02 BoD); modelled downstream BG-P1/P2 still use one designated grade per pile in the MVP. S3-A20's non-spatial multi-profile display metadata on the separate, solver-backed upstream raw piles does not establish a reclaim-zone rule. | BG-P1=TBT, BG-P2=BT | confirm downstream zoning/campaign rule |
| S3-A16 | Mine/raw-screened and product profiles are separate model records. Per project-team clarification 28-Jul-2026, `RAW-BT-GLOBAL`, `RAW-BT-HIGH-CD` and `RAW-BT-LOW-CD` are a shared mine-neutral grade catalogue for Benguerir and Louta. The current chemistry values retain Louta L02 BoD provenance; site moisture/PSD and live pile assays remain profile/inventory data. The BoD does **not** map the raw cases to TBT/BT/MT product IDs. S0 uses Global on L1 and Low-Cd on L2 with documented recoveries, but the L1→TBT and L2→BT product mapping is explicitly **ASSUMED**. S3-A20/S3-A25 composition and spatial shares remain display assumptions; S3-A23's ore-band shares remain display metadata rather than solver zones. | `Scenario.grades` · `Scenario.material_profiles` · `LOU_WASH_LINE_CASE` · `LOU_WASH_OUTPUT_GRADE_MAPPING` | approved raw-case/product-grade campaign map, live assays, grade cut-offs and SMS Material/Grade/SQP IDs |
| S3-A18 | **CURRENT 28-Jul-2026 PLANNING BASELINE:** Benguerir Line 1 / Train A, Line 2 / Train B and Line 3 / Train C are all `OPERATING` in the scenario and each owns a complete executable mine→screened-ore→wash→product campaign. Numeric line labels remain provisional aliases for design-source Trains A/B/C; the available documents do **not** independently prove commissioning/as-built status. These washing trains remain distinct from the upstream B01 interfaces `TM1`/`TSM2`/`TSM3`. | three solver-backed `BEN-WASH-L1/L2/L3` campaigns | latest commissioning/as-built status, canonical tags and approved production campaign |
| S3-A26 | **ASSUMED overall washing-line residence = 0.5 h (30 min)** for every Louta and Benguerir line, from project-team instruction 26-Jul-2026. The documented scrubber components (Louta 2.5–3 min; Benguerir 3 min) are included within—not added to—the 30 min. Louta L1/L2 and Benguerir L1/L2/L3 are solver process-transfer stages with zero-start WIP buffers and a minute-level 30 min dependency; same-bucket and immature WIP consumption are prohibited. | `WASHING_LINE_RESIDENCE_TIME_MODELLED={LOUTA:true,BENGUERIR:true}` | validate line-by-line residence and transition/mixing behaviour against operating timestamps |
| S3-A27 | **ASSUMED purpose-backed S0 stocked-route policy; no coverage reward or token jobs.** The built-in weekly profile schedules one Louta stocked-route campaign on **Day 1 only**: C1 dispatch **8,000 t/week**; recovered output **4,000 t/week on L1 + 4,000 t/week on L2**; and **5,864 t/week Global raw feed on L1 + 5,908 t/week Low-Cd raw feed on L2**. Days 2–7 have no Louta wash campaign in the preset; therefore the exact weekly process/corridor targets equal these one-campaign quantities rather than seven times them. These are campaign inputs, not BoD design-case rates. Terminal `110LG-OL1/-OL2` is restored to **123,568.5 t each**; `LT-P1/-P2` to **95,756 t each**; both washing WIP buffers end at **0 t**. The M16 terminal supply-position ceiling remains at its t0 value. `MACHINE_COVERAGE_REWARD=0`; a small positive tonne cost removes arbitrary excess flow. | `LOU_S0_STOCKED_ROUTE_ENABLED=true` · daily wash `DemandEntry` rows · weekly `PROCESS_STAGE_OUTPUT_TARGET_T` / `CORRIDOR_DISPATCH_TARGET_T` totals · exact terminal inventories · `FLOW_TONNE_PENALTY=1` | replace demonstration targets with approved daily source allocation/end-stock policy; retain demand-only mode when disabled |
| S3-A29 | **ASSUMED seven-day demand profiles, not approved production or train orders.** S0 uses **168 hourly planning buckets** grouped into seven daily campaigns. Every receiving unit may request a different grade and quantity on each day; the selected daily quantity is distributed over that day's 24 hourly demand buckets, while executable jobs remain hourly. Built-in final-unit quantities and grades vary by day, with factors that preserve the documented preset totals: `BG_TRAIN` requests **84,000 t/week** of washed-product train demand, while `FULL_FLOW` and the default `BLEND_SHOWCASE` request **168,000 t/week** of train demand plus **74,200 t/week** of C2 allocation. Mine washing is deliberately sparse to respect finite demonstration inventory: Benguerir L1/L2/L3 run on **Days 1, 2 and 4 only**, using **2,695 / 2,535 / 2,695 t per active day** and producing **1,800 t per line per active day** (**8,085 / 7,605 / 8,085 t feed and 5,400 t output per line per week**); Louta runs on Day 1 only as stated in S3-A27. All presets remain demonstration templates only. | `PLANNING_DEMAND_PROFILES` · `DemandEntry.requested_grade` · default `BLEND_SHOWCASE` | replace with approved seven-day production, washing-campaign, corridor-allocation and train-order inputs |
| S3-A30 | **PAP-2 defaults to `Future`.** Its saved S0 template follows PAP-1 at **1,500 t/h, G10 and 36,000 t/day** on each of the seven days (**252,000 t/week** if unchanged), but Future contributes zero active demand and offers no route candidates. Selecting `Available` activates the two DERIVED/TBC routes without changing the confirmed two-bank M16 envelope, so simultaneous full PAP-1 + PAP-2 + DRY-FEED service is not claimed. | PAP-2 lifecycle selector in Configure | confirm PAP-2 tag/phase/route and approve a feasible weekly demand combination |
| S3-A34 | **ASSUMED held-running state and cost, NOT a measured idling figure.** The planner may keep a machine running through a bucket without moving material, instead of commanding a token parcel to bridge the gap. Added because the audited S0 week carried five 1 t bridge buckets mid-campaign and banning one simply moved the trick elsewhere (5 → 19 below-floor parcels): the model had no truthful way to say “stay on, move nothing”. `EQUIPMENT_HOLD_BUCKET_PENALTY = 8` is bounded by argument, not measurement — below a stop plus a later start (10 + 20) or holding is never chosen, above zero or the planner holds everywhere free. Held minutes are reported separately from active minutes and never as production. | `EQUIPMENT_HOLD_BUCKET_PENALTY` · `CAMPAIGN_POLICY.max_hold_min` | obtain the hold-versus-stop operating philosophy, idling energy draw and drive-thermal restart limit, then price the hold from those |
| S3-A32 | **ASSUMED campaign / parcel / minimum-run policy, NOT a plant-approved operating limit.** Without it the hourly group-activity rule degenerates to `tot ≥ 1 t` for any machine whose `rate_min_tph` is zero — every stacker here — so a campaign could be padded with meaningless 1 t hours at no cost and the minute overlay drew each as a 1-minute run. Installed defaults: **250 t** minimum parcel per bucket, **20 min** minimum run, **30 min** maximum held (running, empty-belt) gap, **no** dwell constraint (1 bucket), short-parcel penalty **4,000**. A parcel below the floor stays possible, is priced and is reported, so a genuinely necessary short job is never discarded. Historical tuning at penalty 40 retained a 1 t parcel; 4,000 removed it on that calibration and served 3,689 t more demand, while a hard floor (`allow_short_parcel: false`) also removed it but cost 16,226 t of service. This is not a guarantee: the version-6 washing/G10 S0 acceptance run again retained one 1 t allocation because service and earlier protected objective tiers outrank the parcel preference. Set a positive `MIN_JOB_VOLUME_T` or prohibit short parcels when elimination is mandatory, then evaluate the explicit service trade-off. | `CAMPAIGN_POLICY` · `SHORT_PARCEL_PENALTY` · `MIN_JOB_VOLUME_T` | obtain minimum stable belt/drive loading, permitted start frequency per hour, drive-thermal restart limits and the maximum period the plant accepts running an empty belt rather than stopping it; then approve per-machine values |
| S3-A33 | **ASSUMED continuous-feed service share, NOT an approved minimum stable feed rate.** The previous test called a demand-active hour served whenever at least ONE physical tonne arrived — which is also the integer activity floor of the mass model, so a single token tonne per hour bought a zero-gap continuity KPI while the rest of the order went unserved. Continuous-feed destinations now additionally require **50 %** of that hour's **own** requested tonnes. The threshold is demand-derived so it asserts no t/h figure, never exceeds the bucket's own integer model demand, and token deliveries are reported separately rather than hidden. | `CONTINUITY_POLICIES[*].minimum_service_fraction = 0.5` | obtain each receiving plant's approved minimum stable feed rate and move it to `CONTINUITY_POLICIES[*].minimum_tph` |
| S3-A31 | **ASSUMED blending showcase, not an approved plant recipe.** `BLEND_SHOWCASE` reuses the `FULL_FLOW` downstream-demand bundle and enables one **12,000 t** chevron campaign at **MPH M16 HP-07**: **10% TBT + 90% BT-LOW-CD → BT**, ready after 48 h. On the S0 assays the dry-solids calculation gives **BPL 68.85 and Cd 20.213**, within the modelled DRY-FEED BT intervals. It is intended to expose quality, rehandling, electricity, positioning and conflict impacts in a reproducible software test. Linear perfect mixing, stock assays and the chevron homogenisation response remain unvalidated plant assumptions. | `PLANNING_DEMAND_PROFILES[BLEND_SHOWCASE]` · `CHEVRON_STACKING` · HP-07 monitor badge | confirm blending point, approved recipe/parcel sizes, live component assays, homogenisation/segregation performance, motor/load curves and meter baseline |
| S3-A35 | **PROVISIONAL RAW-TBT / washing calibration, not a plant fit.** Project-team supplied RAW-TBT chemistry is BPL 54.10%, Cd 16 ppm, MgO 1.52%, SiO2 14.39% and CO2 8.32%. Per explicit instruction, the separately supplied 5% H2O value is excluded and the prior 12% moisture remains active. Relative response vectors `[BPL,Cd,MgO,SiO2,CO2,moisture]` are Simple `[+12,-5,-30,-28,-26,+20]%` and Full `[+23,-20,-64,-50,-27,+20]%`; yields remain 85% and 65%. This makes RAW-TBT fail G10 under Simple and pass under Full, while RAW-BT-LOW-CD can still pass under Simple. | washing model version 6 · grade catalogue RAW-TBT | replace coefficients with paired feed/product laboratory campaigns, including mass recovery and moisture on a common basis |
| S3-A3 | **USER-CONFIRMED proximity correction 22-Jul-2026:** Louta/C1 is nearer; Benguerir/C2 is farther. Baseline provisionally reassigns the existing 30/60 km pair to C1/C2, giving C1 ≈2 h 05→2 buckets and C2 ≈4 h 10→4; exact route length, effective speed and transfer dwell TBC. | C1 30 km/2 buckets; C2 60 km/4 buckets | corridor TBE/datasheet or surveyed route + effective speed/dwell |
| S3-A4 | Initial in-transit = full-rate belts using corrected provisional lags (C1 ≈5.2 kt vs ≈5.4 kt continuous; C2 ≈10.4 kt vs ≈10.8 kt) | 2600 t/bucket entries | mine SMS dispatch history at go-live |
| S3-A5 | Benguerir train interfaces DOCUMENTED (raw + washed 1,000/1,200 t/h, one station ASSUMED shared). Actual planning control remains SCOPE_TBC; the raw-train demand stays 0, while washed-product train demand is an explicitly ASSUMED scenario input governed by S3-A29. | BG-TRAIN-RAW / BG-TRAIN-PROD | confirm train planning scope and replace demonstrations with approved train orders |
| S3-A10 | **CONFIRMED:** B02 table is the current Benguerir configuration; ST3**, RC2** and RC3** are current assets and `**` is source notation only | BEN_B02_OPTION_ENABLED remains a legacy sensitivity switch, baseline `true` | none — project-team current-state instruction 21-Jul-2026 |
| S3-A11 | **CURRENT 28-Jul-2026:** the former continuous `BG-WASH-IN` assumption is retired. Three finite, material-conserving campaigns borrow the Louta Global/Low-Cd recoveries only as explicit simulation assumptions, produce **1,800 t per line**, and are stacked by ST3 into BG-P1/BG-P2. | `BEN-WASH-L1/L2/L3` process targets; no exogenous row | approved Benguerir feed rates, recoveries, grade mapping and daily campaign |
| S3-A17 | `BEN_ST3_STACKING_ENABLED` remains only for saved-scenario/API compatibility. The current six-pile baseline always routes planned washing output through ST3; the retired exogenous representation has no topology or mass-balance effect. | compatibility flag only | remove after legacy saved scenarios are no longer supported |
| S3-A12 | Raw ore-yard TOPOLOGY USER_CONFIRMED 20-Jul-2026 (Table 20): 2 piles, 2 dedicated stackers (one per pile), 1 shared reclaimer. **CURRENT 28-Jul-2026:** BG-RAW-1/-2 and machines `BG-RAW-ST-1/-2` + `BG-RAW-BWR` are solver-backed. Two larger upstream mine piles and `BG-MINE-ST-1/-2` + shared `BG-MINE-BWR` complete the six-pile chain. Physical tags and metric rail positions remain TBC. | six physical `StockSpace` rows + six upstream `Machine` rows | Table 20 doc number/revision + GA + tags |
| S3-A14 | Table 20 values: <15 mm screened phosphate; **400,000 t total live yard capacity**, allocated as **BG-RAW-1 = 200,000 t and BG-RAW-2 = 200,000 t** per project-team instruction 22-Jul-2026; open longitudinal, chevron; 2,100 nom / 2,500 design t/h humid for both stackers AND the shared reclaimer; autonomy 12 d nom / 14 d design as printed; reclaimer marked 'Out of scope' for the source package (`package_scope`) — application planning ownership is SEPARATELY TBC (`planning_scope`) | raw-storage Machine/PileGeo entries | timestamped SMS mass/grade snapshot + source document number/revision |
| S3-A19 | **CONFIRMED scenario starting state:** BG-RAW-1/-2 each retain **200,000 t capacity / 100,000 t start**. The 28-Jul-2026 six-pile simulation adds BG-MINE-1/-2 at the same **200,000 t / 100,000 t** values as explicit user-directed assumptions. All four are solver `StockSpace` inventory; the values remain a static scenario baseline, not timestamped SMS telemetry. BG-P1/P2 retain their existing 191,512 t capacities and current starting masses. | `BG-MINE-1/2`, `BG-RAW-1/2`, `BG-P1/2` | timestamped SMS mass/grade snapshot at operational handover |
| S3-A20 | **Shared raw-grade catalogue; ASSUMED Benguerir composition/campaign mapping:** Benguerir and Louta share the mine-neutral Global/High-Cd/Low-Cd identities, while the current chemistry retains Louta-BoD provenance and is not a live Benguerir assay. Any 50/50 visual component bands remain non-spatial display metadata, not solver zones. The solver-backed BG-MINE/BG-RAW piles use explicit designated campaign grades (Global on pile 1, Low-Cd on pile 2) until dated assays and approved zoning/campaign rules arrive. | shared `RAW-BT-*` grades + solver `StockSpace.grade` + non-spatial display distributions | replace with pile survey/SQMS segments, dated assays and approved Grade/SQP mapping |
| S3-A21 | **ASSUMED Louta product-grade distributions:** `LT-P1 = 60% TBT + 40% BT`; `LT-P2 = 60% BT + 40% MT`, by mass. With equal 95,756 t starting masses, yard aggregate = **30% TBT / 50% BT / 20% MT**. After correcting the grade order to TBT < BT < MT, mass-weighted demonstration composites are P1: BPL 65.0%, Cd 33.0 ppm, MgO 1.28%, SiO₂ 3.22%, moisture 12%; P2: BPL 70.4%, Cd 26.8 ppm, MgO 0.98%, SiO₂ 2.48%, moisture 12%. Numeric vectors and operating Louta mapping/cut-offs remain TBC. Rendered bands show mass shares only, not spatial order. The shared raw `RAW-BT-*` grades remain separate and are used only at raw stages. | `PILE_GRADE_DISTRIBUTIONS` + aggregate `StockSpace.quality` | replace with timestamped SMS/SQMS segments, surveyed boundaries, active reclaim face and Grade/SQP mapping |
| S3-A25 | **ASSUMED Louta 102MG display baseline explicitly adopted 26-Jul-2026:** stable internal keys `102MG-SP-01/-02` each use a round **100,000 t display capacity** and **50,000 t static t0 mass (50%)**. The canvas calls them **MINE STOCKPILE-01/-02**, because the L01 source assigns physical tags `102MG-SP-01/-02` to samplers. Internal SP-01 = 50% Global + 50% High-Cd; internal SP-02 = 50% Global + 50% Low-Cd. Each component is 25,000 t at schematic R0–R250 / R250–R500; R500–R1000 is empty. The BoD §6.1 chemistry is source-backed, but capacity, mass, shares, mapping and positions are not. These lanes remain display/reference only, static in Initial/Final, absent from solver inventory/jobs/KPIs. The confirmed 494,274 t belongs exclusively to 110LG. | `LOU_MINE_102MG_DISPLAY_*` configuration only | replace with approved pile-capacity schedule plus timestamped SMS/SQMS mass and profile segments |
| S3-A15 | Raw-storage reclaimer CAPACITY CONFLICT: Table 20 2,100/2,500 t/h vs the B02 expansion raw bucket-wheel reclaimer/RB interface 2,600/3,120 t/h — whether B02 upgrades, replaces or supplements it is TBC, NOT resolved by inference | `L-BG-RAW-C2` capacity_tph=2600 (assumes the B02 interface selected) | equipment list / PFD proving equivalence or supersession |
| S3-A13 | Reclaimer min stable 500 t/h ASSUMED (not in the B02 BoD) | 500 t/h | machine supplier data |
| S3-A6 | MPH placeholders as S2 | — | as S2 |
| S3-A7 | A **solver-modelled** pile may carry multi-profile distribution metadata, but the plan LP treats it as one dominant/designated grade plus one constant mass-weighted aggregate quality snapshot per solve; simulator applies mass-weighted updates and replans refresh the snapshot. The optimiser does not select distribution components independently unless explicit `Scenario.pile_zones` exist. S3-A20's upstream raw bands are display-only; S3-A21's Louta bands inform the aggregate `StockSpace.quality` but are not solver zones. | — | obtain surveyed zone boundaries, grade-compatibility/campaign rules and active reclaim-face logic before profile-selective reclaim |

## B. Engine/model simplifications (apply to all scenarios)

1. **One aggregate quality snapshot per pile/solve**
   (S3-A7) — plan/sim quality can diverge slightly on piles rebuilt within
   the horizon. EXPERIMENTAL multi-grade zoning exists behind
   `MULTI_GRADE_ZONES_ENABLED` (default OFF): explicit `Scenario.pile_zones`
   inputs (boundaries, grades, masses, capacities, active reclaim face) are
   expanded into per-zone sub-spaces sharing the parent's route node, so the
   full constraint stack (mass balance, capacity, anti-contamination,
   mass-weighted quality, concurrency) applies per zone. Zone/campaign rules
   are REQUIRED INPUTS — no surveyed boundaries and active reclaim face are
   available for a built-in real pile, so no built-in scenario defines
   solver zones. `LT-P1/P2` do carry assumed multi-profile distributions,
   but planning uses their mass-weighted aggregate `StockSpace.quality`.
   Face progression across zones within one horizon is NOT modelled (that
   sequencing rule is exactly the missing input). The two bands shown inside
   each upstream Benguerir raw pile under S3-A20 are non-spatial,
   mass-proportional display composition; they are not
   `Scenario.pile_zones`. The piles themselves are solver inventory, while
   those visual component boundaries do not drive face sequencing. The Louta product
   bands under S3-A21 are likewise mass-share graphics, not spatial order;
   they affect planning only through the precomputed aggregate pile quality.
2. **Spec averaging_h** recorded, enforced per bucket (conservative).
3. **w₃ process-cost term inactive** — GradeCostModel curves not received
   (report §16 “Required, not received”); economics limited to w₄ give-away
   with placeholder rock values.
4. **Timed resource events in replans:** machine failures, conveyor trips and
   receiving-plant stops are bounded exact-minute windows. Future work that
   overlaps the affected interval is cancelled before the remaining horizon is
   re-solved; unaffected committed work remains frozen. Overlapping rate
   derates expire independently and restore the still-active cap.
5. **Uniform-density x-cuts** for StartX/StopX, shared with the simulated
   ASP inventory rule (real ASP uses the 1-m 3-D cache).
6. **Route utilisation** attributes split streams to the dominant path.
7. **Anti-contamination** enforced against the plan-time snapshot state of
   each space (designated grade / current grade / empty).
8. **Machine positioning is a minute-level planned estimate, not material or
   telemetry.** Cross-space translation plus setup is inserted between
   validated production windows and carries no tonnes, grade or conveyor
   path. Green means an exact minute interval is reserved; the machine may
   reposition early and wait when a later calendar interval is blocked. Red
   means the bucket-level material plan needs targeted replanning, and that
   exact-profile job is withheld from the ASP plan-item export until its
   timing validates. Blank time is idle/unallocated. The first job is assumed
   pre-positioned because no position-space/global-rail identity exists, and
   same-space face progression stays within the reclaim/stack job.

## B1. Repère position convention (24-Jul-2026)

- **USER-CONFIRMED operating convention:** every stockpile is divided
  longitudinally into **1,000 equal intervals**, bounded by **R0** and
  **R1000**. A placement or reclaim instruction can therefore use a range
  such as `R400–R600`.
- **Document-confirmed foundation:** the Innomotics FRS requires numeric job
  Start/End positions (`StartX`/`StopX`), validates them against the
  device/pile work area, evaluates reclaim inventory inside that range and
  caches pile inventory in 1-m segments. The FRS does not use the word
  *repère* or define an R0–R1000 scale.
- **Canonical-unit rule:** metres remain authoritative for inventory,
  optimiser geometry, machine travel, spatial separation and ASP exchange.
  For approved pile bounds `[x₀_m, x₁_m]`,
  `x_m = x₀_m + (R/1000) × (x₁_m − x₀_m)` and
  `R = 1000 × (x_m − x₀_m)/(x₁_m − x₀_m)`.
- **Not a length claim:** R0–R1000 defines 1,000 normalized intervals; it
  does not assert a 1,000-m pile. Current metric demo geometry remains
  ASSUMED.
- **Still required:** the physical endpoint designated R0 and increasing
  direction for every pile, surveyed origin and usable physical length, and
  the production ICD's StartX/StopX units, origin/orientation and scale.

## C. Stockyard Monitor visual conventions

- Yard drawings are **schematic**: lane order/rails/positions carry no metric
  meaning; at M16 the eight hangars and their four-bay/machine-line sequence
  are confirmed (PFD-0001[3]), while the hangar names inside a bay are
  inferred. Louta/Benguerir are
  stamped "SCHEMATIC — NOT TO SCALE" with TBC banners in the drawing itself.
- Report equipment not in the model is listed in the monitor's coverage
  panel (Louta `102MG-ST-01/-02` + `110MG-RC-01`; future
  ST01-D*/RC01-C*) — it is never silently added to the optimiser.
- Segment slices are drawn only when Segment data covers the pile mass
  (±40 %); otherwise the dominant grade is shown and the pile is labelled
  "mixed" — boundaries are never fabricated.
- Plan-basis quality shows the planning snapshot (the plan LP treats space
  quality as constant, S3-A7); simulation basis shows the mass-weighted
  simulated quality history.

## D0. Same-pile concurrency & M16 machine corridors (15/16-Jul-2026)

- **Same-stockpile stacking and reclaiming are permitted at MPH M16,
  subject to spatial separation, work-zone, route, geometry and ACS/PLC
  constraints. Exact numerical safety limits remain TBC.** (USER-CONFIRMED
  operating rule, project team, 15-Jul-2026 — supersedes the earlier
  "configurable permission … TBC" statement. No fixed A/B double-buffering
  rule is retained or introduced.) Applicability at Louta/Benguerir stays
  configurable/TBC (`Site.same_pile_concurrency_allowed = None`).
- Scheduler implementation: reclaim/dispatch work the LOW-x face, stacking
  builds from the HIGH-x end (non-overlapping zones); during same-bucket
  concurrency the reclaim may only consume start-of-bucket inventory (no
  pass-through onto the face being stacked); a conservative capacity-share
  room rule rejects concurrency when two zones + separation cannot fit.
  Demonstration parameters `min_machine_separation_m = 50` (ASP UC-28
  default), `work_zone_buffer_m = 25`, envelopes/clearances — **all
  ASSUMED** until YardMaster/ASP configuration, ACS specification or
  machine supplier documents are on file.
- **Signature re-baseline (15-Jul-2026)**: the previous pinned plan
  (`c4d33f8842bf0917`) reclaimed same-bucket pass-through material on the
  face being stacked (overlapping work zones). New pinned S3 budget-3 plan
  `3e4b827fabe58d70`: 90 jobs, objective 330,035 (was 300,300 — the delta
  is the honest cost of the face-separation rules), unmet 0 t.
  Reclaim-vs-dispatch pairs may still share the low face across time
  (staggering of same-kind zones not implemented; ASP gate flags them).
- **M16 bay topology CORRECTED to the PFD (25-Jul-2026)**: the earlier
  reading (two pile banks with one shared stacker line `T06` and one shared
  reclaimer line `T11` in a detached machine corridor, every machine
  reaching all eight hangars) is **superseded**.
  `Q80130-MZI-M16-ME-PFD-0001[3]` Rev 3 (IFU, 28-Oct-2025) shows FOUR
  two-hangar bays with the machine lines interleaved between them:
  `M16A-A-T06` (ST01-A) · bay 1 · `M16A-A-T11` (RC01-A) · bay 2 ·
  `M16A-B-T06` (ST01-B) · bay 3 · `M16A-B-T11` (RC01-B) · bay 4 ·
  `M16A-C-T06` (ST01-C); FUTURE continues bay 5 · `M16A-C-T11`
  (M16A-C-RC01) · bay 6 · `M16A-D-T06` (M16A-D-ST01) with 4 × 125,000 t
  future hangars. Stackers are 2,600 t/h, reclaimers 2,500 t/h. **Project-team
  physical rule (25-Jul-2026): a machine that translates along its line
  between adjacent bays serves those adjacent bays — and only those.**
  Applied reach: ST01-A→bay 1 · RC01-A→bays 1-2 · ST01-B→bays 2-3 ·
  RC01-B→bays 3-4 · ST01-C→bay 4. `reachable_spaces` is never all-to-all,
  and the ASP work-area rule rejects e.g. `ST01-A → HP-08`. The two reclaim
  streams stay separate to their own gate bank; the PAP/drying confluence is
  TBC. SEQUENCE CONFIRMED · METRIC POSITIONS SCHEMATIC (the PFD is a flow
  sheet, not to scale) · hangar names and left/right order inside a bay
  INFERRED. **Display geometry ruling (26-Jul-2026):** the two hangars in each
  bay are shown side-by-side on one horizontal row, and the flanking shared
  stacker/reclaimer line spans the complete pair. This makes physical access
  legible but changes no pile, route, reachability, inventory, job or KPI.
  Benguerir/Louta
  rail positions have no source layout → drawn between lanes as schematic
  assumptions, POSITION TBC.
- **Signature re-baseline (25-Jul-2026, bay/reachability pass — SUPERSEDED by the model-consistency pass below)**: S2
  `33140d5110a3ef0a` → `d56fb0f5a76310f1` (48 → 32 jobs, FEASIBLE → OPTIMAL,
  objective 541,820 → 540,220) and S3 `bba8081a6b6d1526` →
  `b06ace546a905020` (7 → 6 jobs, objective 540,220 → 540,180). Delivered
  tonnage (PAP-1 36,000 t / DRY-FEED 19,200 t), unmet 0 t, service 100 % and
  on-spec 100 % are unchanged; S1 `9150a04e26a9aa61` untouched.

- **M16/PAP model consistency (25-Jul-2026, report Rev V)** — three
  corrections on top of the bay/reachability pass:
  1. **Discharge vs route.** The gate banks stay CONFIRMED physical equipment
     (`L-H-A` / `L-H-B`, `T12` → hopper `M16A-A-R01` + air blasters →
     `H01…H04` → `T13` 1,500 t/h). The PAP-versus-drying CHOICE became a
     separate downstream abstraction (`HUB:M16-T13-{A,B}` →
     `L-RTE-{A,B}-{PAP,PAP2,DRY}`) classified **DERIVED**, drawn dashed amber, with
     the confluence still `TBC`. Confirmed belt ratings never upgrade the
     assignment's evidence class.
  2. **Reception envelope.** CURRENT reception is ONE 2,600 t/h path: PRO-0001
     §2.1.1 places auto sampler `M16VP02` on *the adjacent future conveyor*,
     so `M16A-B-T01` is future. All eight stacking legs share one capacity
     group — aggregate M16 reception can never reach 5,200 t/h — and the S3
     initial in-transit profile was corrected to alternate the corridors
     (C1 buckets 0-1, C2 buckets 2-3) instead of implying an impossible
     simultaneous 2 × 2,600 t/h arrival.
  3. **Changeover.** The documented 10-minute gate/route changeover is **not
     modelled**: `changeover_min = 0.0` on every link, no inter-bucket
     switching loss is enforced, and the register/UI say so instead of
     implying enforcement.
  Both built-ins now carry the same `BUILTIN_BASELINE_REVISION`, and
  `migrate_loaded_builtin` rebuilds the M16 machines/nodes/links/evidence of a
  loaded **S2 or S3** session (returning `solver`, which clears the stale
  plan). `ui.process_mimic.geometry_model_mismatch` refuses to present flows
  when a scenario graph is older than the drawing.
- **Signature re-baseline (25-Jul-2026, this pass)**: S2 `d56fb0f5a76310f1` →
  `8be7856593adb620` (32 → 28 jobs, OPTIMAL, objective 540,220 unchanged,
  DRY-FEED 19,200 → 20,198 t) and S3 `b06ace546a905020` → `6b7723b2e73c8834`
  (6 jobs, objective 540,180 → 540,220); experimental ST3 mode
  `36eecc23e05da50b` → `66d083fc558bf79c`. S1 `9150a04e26a9aa61` untouched.
  **Cross-budget stability no longer holds for the S3-derived scenarios**: the
  reception envelope, the extra route hop and the per-bank conflict groups
  keep CP-SAT improving past the small deterministic budget (S3 budget 3 =
  6 jobs / 540,220; budget 10 = 48 jobs / 464,131). Signatures are therefore
  pinned **per budget**, repeat-run determinism is asserted at that budget,
  and the surviving invariant — more budget never yields a worse objective —
  is tested separately.

## D. Process-mimic geometry & Louta evidence (15-Jul-2026)

- ALL mimic geometry is **schematic** — no GA/plot plan/metric layout is on
  file for any site. Every drawing carries `SCHEMATIC — NOT TO SCALE`
  stamps; machine positions are `POSITION MODELLED — GEOMETRY ASSUMED`
  (normalised `position_x` mapped onto schematic rails, never a physical
  coordinate).
- **Louta boundary correction (user, 15-Jul-2026; L01 evidence update,
  29-Jul-2026)**: `102MG` is pre-beneficiation screened-product storage and
  must not connect directly to C1/M16. `Q80130-LOU-L01-PR-REP-0002[3].pdf`
  p.11 documents two stockpiles, dedicated stackers `102MG-ST-01/-02` at
  2,200 t/h nominal, one shared reclaimer `110MG-RC-01` and reclaim conveyor
  `110MG-CB-01` toward beneficiation. The older
  `Q80130-LOU-L01-PR-PSR-0001[A].pdf` p.10 confirms one stacker dedicated per
  stockyard but gives an older conflicting 1,058 t/h rate. The diagram uses
  the confirmed three-machine topology with schematic rail positions; all
  three remain reference-only until live stock, metric reach and the L01→L02
  solver interface are configured. Display flow: Mine → screening/crushing →
  102MG → beneficiation stages (L02: 110LG raw storage · 120/122LG
  raw reclaim · 121LG handling · 130/131/132LG product storage · 140/141LG
  product reclaim) → C1 take-off. **DATA CORRECTION 18-Jul-2026**: the
  LT-RC-01 2,100 t/h placeholder became the design-basis product reclaimers
  `140LG-RC-01/-02` (3,000 t/h nominal each) on the product-reclaim rail;
  LT-P1/P2 are the two schematic L02 PRODUCT lanes (383,024 t design-basis
  total, equal 191,512 t capacity split DERIVED/TBC; starting mass 95,756 t
  each = 50%, project-team demonstration instruction 24-Jul-2026). Assumed
  washed-product composition is LT-P1 60% TBT + 40% BT and LT-P2 60% BT +
  40% MT; planning uses the corresponding aggregate qualities, with no
  profile-selective reclaim. Documented reclaim topology drawn:
  `140LG-RC-01 → 140LG-CV-03/RL1`, `140LG-RC-02 → 140LG-CV-04/RL2`, each
  selecting RL3/RL4 through its flip gate → C1 corridor interface. C1
  feeder rule: EITHER reclaimer feeds C1 (≤ 2,600 t/h); simultaneous
  partial-rate reclaim TBC/configurable → exclusive feeder group in the
  solver until confluence is confirmed (deterministic signature
  re-baselined: `635b2d36f81fbcb2`).
- **Louta process-description correction (25-Jul-2026)**: the
  equipment-tagged *Louta Beneficiation Plant Process description*, Rev B
  (IFA), 25-Apr-2023 (§3.1 pp. 5–8) is now the authoritative Louta
  equipment source. **Its own document number is inconsistent — cover
  `Q80130-LOU-L02-PR-REP-00002` vs body header
  `Q80130-LOU-L02-PR-REP-00004`; both are recorded, neither preferred.**
  Confirmed: two parallel washing lines **L1 + L2, 3 Mt tsm/year each**
  (both current — neither is a future expansion); **ONE** ore stacker
  `110LG-ST-01` (2,100 t/h wet, tripper `110LG-TC-01`, boom conveyor
  `110LG-BC-01`) forming **exactly TWO** homogenized stockpiles by chevron
  stacking; **TWO** bridge-type reclaimers `120LG-RC-01/-02` (3,000 t/h wet)
  whose routes converge on the common `120LG-CV-02/-03` (RB4/RB5) transfer
  feeding scrubber hopper `210L1-HP-01` **or** `210L2-HP-01`; **ONE** shared
  product stacker `130LG-ST-01` fed by `130LG-CV-11` (SL11). This supersedes
  the generic BoD table's single ore reclaimer for the S0 equipment count.
  The same source conflict also exists downstream: BoD p.20 lists **one**
  product reclaimer, whereas the process description p.8 explicitly tags
  `140LG-RC-01/-02`. The later tagged configuration is used, but both
  conflicts remain recorded under S3-A8. The
  removed placeholder blocks (`MINE / screening`, `MINE (extraction)`,
  `screening / crushing`) are replaced by one labelled mine-transport inlet.
  **102MG mine-storage display baseline (S3-A25, display only):** project-team
  instruction 26-Jul-2026 explicitly adopts 100,000 t assumed capacity and
  50,000 t static t0 mass per pile (50%). SP-01 displays 50% Global + 50%
  High-Cd and SP-02 displays 50% Global + 50% Low-Cd, giving two 25,000 t
  bands at schematic R0–R250/R250–R500. Actual capacity/live mass/spatial
  grade order remain TBC. These values never enter solver inventory, jobs,
  KPIs or material-balance propagation, and they are independent of the
  confirmed 494,274 t 110LG total.
  **Homogenized ore storage S0 baseline (S3-A23, solver-backed):**
  494,274 t total CONFIRMED (Louta BoD Rev B, no per-pile capacities), so
  2 × 247,137 t is **DERIVED**; each pile starts at 123,568.5 t (50%,
  **ASSUMED**) with contiguous mass-proportional bands `110LG-OL1` = 50%
  Global + 50% High-Cd and `110LG-OL2` = 50% Global + 50% Low-Cd (BoD §6.1
  chemistry verbatim, including Louta's 15% moisture). The two lanes are now
  solver `StockSpace` inventories and the stacker/reclaimers are solver
  machines. Their multi-profile bands remain **display metadata only**:
  planning assigns the Global S0 campaign to OL1 and the Low-Cd campaign to
  OL2; it does not selectively reclaim the displayed bands. **One reclaimer
  per pile (ore and product) is APPLIED/CONSERVATIVE PLANNING GEOMETRY** — the
  source confirms the counts but never states either pairing, and the
  physical product-pile count is not stated at all (`LT-P1/P2` are planning
  lines).
- **Louta washing-step timing evidence**: the Louta BoD
  `Q80130-LOU-L02-PR-BOD-0001` Rev B, §8.2.1 p. 21 confirms a
  **2.5–3 min scrubber residence time**. This is one equipment-step residence
  time within the line. Separately, the project team instructs an
  **ASSUMED overall washing-line residence of 0.5 h (30 min)** for both Louta
  lines. The scrubber component is included within—not added to—the 30 min
  total. L1 and L2 are process-transfer stages, not mobile `Machine` entities.
  Each writes recovered mass to a zero-start WIP buffer using the documented
  case recovery; the minute scheduler prevents `130LG-ST-01` output before
  the 30-minute dependency. The delay is not rounded into an extra hourly
  bucket.
- **Louta S0 stocked-route policy (28-Jul-2026; ASSUMED demonstration
  policy):** the demand-only baseline could satisfy downstream demand from
  opening stocks and therefore had no purpose for upstream Louta movements.
  S0 now exposes, in Configure, a purpose-backed stocked route rather than
  rewarding machine use:
  - exact C1 contribution: **8,000 t**;
  - exact recovered product: **4,000 t from L1** and **4,000 t from L2**;
  - L1 uses the documented Global-case feed/recovery
    (**584 t/h; 68.22%**) and L2 uses the documented Low-Cd case
    (**588 t/h; 67.71%**); High-Cd remains an available documented case
    (**579 t/h; 68.83%**) but is not selected by S0;
  - Global→L1→TBT and Low-Cd→L2→BT are **ASSUMED campaign/product mappings**;
    the BoD does not map raw cases to TBT/BT/MT;
  - exact terminal inventories restore `110LG-OL1/-OL2` to **123,568.5 t
    each** and `LT-P1/-P2` to **95,756 t each**; both WIP buffers end at zero;
  - `110LG-ST-01` draws from two finite **10,000 t ASSUMED mine-inlet handoff
    inventories**. They are not 102MG piles or live telemetry. L01 now
    documents `110MG-RC-01` / `110MG-CB-01` toward beneficiation, but the
    live 102MG state and exact L01→L02 solver-interface mapping are not loaded;
  - the shared product-stacking envelope is **1,000 t/h ASSUMED**, not a
    `130LG-ST-01` nameplate; the source gives no stacker or SL11 rating;
  - common RB4/RB5 is conservatively limited to the maximum documented
    single-line feed (**588 t/h**) and only one ore reclaimer may use it per
    bucket. Only one product reclaimer may feed the unresolved RL3/RL4/C1
    confluence per bucket.
  `MACHINE_COVERAGE_REWARD=0`; exact targets plus a small positive
  per-commanded-tonne cost prevent surplus/token jobs. Disabling the policy
  returns a legitimate demand-only plan.
- **Planning demand profiles (weekly S0 baseline; ASSUMED demonstration
  inputs):** S0 covers seven days using **168 one-hour planning buckets**.
  For every receiving plant or train interface, each day carries its own
  requested grade and daily quantity; the quantity is represented by 24
  hourly demand entries for that day. Built-in final-unit campaigns vary by
  day in both quantity and grade while preserving the documented weekly preset
  totals: `BG_TRAIN` totals **84,000 t/week** of washed train product; default
  `FULL_FLOW` totals **168,000 t/week** of train product plus **74,200 t/week**
  on C2. Washing campaigns are intentionally sparse because the demonstration
  hopper/interface inventory is finite and unreplenished: Benguerir L1/L2/L3
  run on **Days 1, 2 and 4 only** (weekly feed **8,085 / 7,605 / 8,085 t** and
  output **5,400 t per line**), while Louta L1/L2 run on **Day 1 only** (C1
  dispatch **8,000 t/week**, output **4,000 t per line**, and feed **5,864 /
  5,908 t/week**). These campaigns exercise connected routes but are **not
  approved production plans or train orders**.
- **Weekly acceptance basis:** the former one-day allocation signature is not
  an acceptance contract for the seven-day model. Weekly validation checks the
  168-hour grid, preservation of all seven daily grade/quantity campaigns and
  hourly, route- and inventory-backed job allocation.
- **Bounded weekly solve:** S0 is decomposed into seven civil-day CP-SAT
  windows under one total wall-time limit. Physical inventory, pile grade
  segments, equipment positions and delayed corridor parcels are handed from
  one day to the next. Demand is conserved independently for every
  destination/day/grade campaign (`delivered + unmet = requested`). The
  optional mixed-resolution mode replaces only the first four hours with
  15-minute buckets and retains the hourly tactical tail.
- **Machine disposition boundary:** `110LG-ST-01`, `120LG-RC-01/-02`,
  `130LG-ST-01` and `140LG-RC-01/-02` are solver-controlled. In the enabled
  S0 baseline, their work is justified by the exact line, terminal-inventory
  and C1 targets above. A machine still idle is reported structurally as
  unavailable, no source inventory, no target capacity, missing route data,
  a mutually exclusive peer using the shared route, or targets met without
  it. `102MG-ST-01/-02` and `110MG-RC-01` remain reference-only with
  `MODELLING_GAP[MISSING_ROUTE_RATE_REACH]`; the washing lines themselves are
  process stages, not machines. No source calls either Louta reclaimer pair
  duty/standby.
- Louta evidence: `102MG-ST-01/-02` (dedicated one per stockyard, 2,200 t/h
  nominal) and shared `110MG-RC-01` are CONFIRMED by the L01 process
  description; `120LG-RC-01/-02`, `140LG-RC-01/-02` tags are CONFIRMED by
  the L02 process description + user-reviewed handling philosophy.
  Reachability and metric positions remain TBC. Actual 102MG capacity, live
  mass and grade placement are also TBC; S3-A25 explicitly adopts a
  display-only baseline of 100,000 t capacity and 50,000 t t0 mass per pile,
  with assumed 50/50 raw-profile bands. It is not telemetry or solver
  inventory, and the confirmed 494,274 t ore capacity is never attributed to
  102MG. Mine SMS and Beneficiation SMS are separate logical control scopes
  (endpoint relationship TBC). Only the 102MG assets in this sentence remain
  display/reference-only; the selected 110/120/130/140LG assets above are now
  solver resources.
- The mimic is an engineering planning/monitoring view, **not a certified
  control/safety HMI**: no command elements, no PLC-interlock/ACS
  replacement; ACS/permissive status would be shown as an input when
  available (not on file).

## E. Integration approximations

- The **8 ASP validation rules** are re-implemented from the FRS *text* with
  vendor severities (5 ERROR / 2 WARNING / 1 INFO). Exact configured
  behaviour, telegram schemas and Material/Grade/SQP handling require the
  **RabbitMQ ICD** (hypothesis H1) — `RabbitMqAspAdapter` is a stub.
- **H2 is resolved at solution level (user-confirmed)**: Louta, Benguerir and
  MPH use identical Innomotics/YardMaster SMS solutions, deployed as three
  independent SMS instances. The MPH FRS defines the common interface
  profile; its application to the two identical mine-SMS deployments is
  confirmed by the user (the MPH document itself does not cover the mine
  sites). Still **TBC per site**: endpoint/host, virtual host + credentials,
  exchange/queue/routing-key names, Device/Pile IDs, Material/Grade/SubGrade
  mappings, configured AspRoutes, work calendars, minimum machine-distance
  parameters, telegram namespaces/version, permissions. Plan co-authoring
  governance (H3) remains unresolved.
- `mph_sim/integration.py` implements the three-SMS layer: ONE adapter
  implementation/profile (`INNOMOTICS_YARDMASTER_ASP`), three independently
  configured instances; job ownership = `Job.machine → Machine.site`
  cross-checked against the pile's site (mismatch/unknown → UNMAPPED error
  queue, never silently assigned to MPH); the eight-rule gate runs per SMS on
  a site projection so FIFO/spatial/inventory/calendar checks never cross
  sites. All submission/evaluation states are labelled *simulated*. A
  separate read-only `SpaceValue` connector is implemented with strict
  three-site validation and explicit operator apply, but remains
  uncommissioned until the per-site ICD/configuration and credentials exist;
  it does not make plan submission or equipment control live.
- Baseline S3 InventoryAvailability ERRORs on HP-07/08 reclaim items are the
  faithful snapshot semantics of the gate: the piles are filled by stacking
  planned *earlier in the same horizon*, so whole-horizon batch submission
  flags them; the closed loop dispatches per bucket.

## E. Benguerir B02 correction pass (19-Jul-2026)

Authoritative source: **Q80130-BEN-B02-ME-BOD-0001 Rev B, IFR, 05-Feb-2025, §§2.3.1–2.3.3 and §2.6** (documented design basis, not
as-built). Key rulings now encoded in scenario/geometry/tests:

- **Staged process** (B02 §2.6): raw/screened mine storage → washing/
  filtration/settling → NEW washed-product storage → C2 corridor / train;
  conditional raw-ore → C2 (phase-1 250 m³ hopper mod and/or RB9/RB10 —
  `BEN_RAW_TO_C2_ENABLED`, default false). The B01 2023 screening report is
  the connected upstream stage — exact `04BG-*` ↔ `RB/SL/RL-*` tag/PFD
  reconciliation TBC; tag registers not combined.
- **Washing-line display baseline** (project-team UI/layout instruction,
  22-Jul-2026): Line 1 / Train A is labelled `OPERATING`; Line 2 / Train B and
  Line 3 / Train C are labelled `FUTURE`. These are lifecycle labels on
  display/reference geometry, not live equipment-state telemetry or solver
  resources. Commissioning/as-built evidence, canonical tags and effective
  dates remain TBC; no external design document is cited as proof of those
  statuses. B01 `TM1`/`TSM2`/`TSM3` remain separate upstream screening/
  pre-beneficiation lines.
- **Benguerir washing-step timing evidence**:
  `Q80130-BEN-B02-PR-BOD-0001` Rev 0, §7.1 p. 19 and §7.3.1 p. 25 confirms a
  **3 min scrubber residence time**. This is one equipment-step residence time
  within the line. Separately, the project team instructs an
  **ASSUMED overall washing-line residence of 0.5 h (30 min)** for all
  Benguerir washing trains. The 3 min component is included within—not added
  to—the 30 min total. Both values are display/config metadata; washing remains
  outside the planning model, so 0.5 h is not yet used as a solver delay and
  must not be rounded to one hourly bucket.
- **Washed yard**: 2 stockpiles × 191,512 t (total 383,024 t); ONE
  twin-boom stacker `ST3**` 2,100/2,520 t/h (SL5**/SL6**); reclaimers
  `RC2**`/`RC3**` 2,600/3,120 t/h; reclaim conveyors RL7/RL8
  (+RL9/RL9′/RL10/RL11) → RL12/RL13. Per the project-team current-state
  instruction dated 21-Jul-2026, the table is the authoritative current
  Benguerir configuration; `**` is source notation only. The legacy
  `BEN_B02_OPTION_ENABLED` switch remains solely for non-baseline sensitivity
  testing and defaults to `true`.
- **Autonomy**: combined-yard theoretical ≈6.1 d nominal / ≈5.1 d design;
  BoD table states 5/6 d. The earlier datasheet's ≈10-day claim is kept as
  a NAMED DOCUMENT CONFLICT (never assigned per pile).
- **Nominal vs design capacity** stored separately (planning uses nominal;
  design only via an explicit envelope setting). Corridor 2,600/3,120 ·
  train 1,000/1,200 (raw + washed; one loading station ASSUMED shared,
  simultaneous use TBC). Planning control remains SCOPE_TBC; S3-A29's
  washed-product demand profiles are demonstration inputs, not approved
  train orders.
- **Material states** (B02 §2.3.1, display metadata on the BENGUERIR
  site): raw 10–14 % H₂O · washed-after-filtration 23 % · settled product
  15–20 % · 1.2–1.4 t/m³ · 30–35°/35° · abrasive. The shared grade-vector
  12 % moisture is a GENERIC placeholder, flagged as such; the
  filtration→settling transition is NOT modelled (kinetics TBC).
- **Current six-pile model (28-Jul-2026):** the former `BG-WASH-IN`
  representation is **SUPERSEDED and removed**. Three finite hopper
  interfaces feed two BG-MINE piles, the shared mine reclaimer feeds three
  line-specific screening buffers, the raw stackers build BG-RAW-1/-2, the
  shared raw reclaimer feeds three wash WIPs, and current ST3 stacks recovered
  product into BG-P1/P2. All material moves are solver-backed.

## F. Benguerir six-pile planning chain (current 28-Jul-2026)

This section supersedes the 20–24-Jul display-only implementation while
preserving its Table-20 evidence. The current scenario contains six physical
Benguerir piles and a continuous, material-conserving planning chain.

- **Six physical piles:** `BG-MINE-1/-2` are explicit 28-Jul simulation
  assumptions at 200,000 t capacity / 100,000 t start each; `BG-RAW-1/-2`
  retain the same 200,000 t / 100,000 t baseline; `BG-P1/-P2` retain their
  191,512 t capacities and existing starting masses. The three
  `BG-HOP-TM1/TSM2/TSM3` rows and six line-specific screen/wash WIPs are
  finite or zero-start interfaces—not extra physical stockpiles.
- **Machines and reach:** `BG-MINE-ST-1/-2` are dedicated to mine piles 1/2;
  shared `BG-MINE-BWR` reaches both. `BG-RAW-ST-1/-2` are dedicated to raw
  piles 1/2; shared `BG-RAW-BWR` reaches both. ST3 is baseline-plannable;
  RC2 reaches BG-P1 only and RC3 reaches BG-P2 only. Physical tags and metric
  rail positions for upstream assets remain TBC.
- **Table 20 data**: material screened phosphate <15 mm; 400,000 t total
  live capacity, allocated as BG-RAW-1 = 200,000 t and BG-RAW-2 = 200,000 t
  per project-team instruction 22-Jul-2026; open longitudinal, chevron
  stacking; 2 stackers + 1 reclaimer, 2,100 nominal / 2,500 design t/h
  humid each; reclaimer marked "Out of scope" in the source package;
  autonomy 12 d nominal / 14 d design as printed (never reinterpreted or
  assigned per pile).
- **Starting mass is separate from Table-20 capacity:** 100,000 t per raw
  pile remains a static scenario baseline, not timestamped SMS telemetry.
  BG-MINE and BG-RAW are now `Scenario.spaces` entries and therefore
  contribute to inventory, jobs and KPIs.
- **Shared raw grades; multi-profile pile composition is replaceable (S3-A20)**:
  project-team clarification 28-Jul-2026 confirms the same mine-neutral raw
  grades at Benguerir and Louta. The 24-Jul-2026 clarification states that one
  stockpile may contain multiple grade profiles. The display demonstration uses
  BG-RAW-1 = 50% Global BT + 50% High-Cd and BG-RAW-2 = 50% Global BT + 50%
  Low-Cd, by mass. From `Q80130-LOU-L02-PR-BOD-0001` Rev B (12-Apr-2023,
  §6.1 pp. 14–15), the mass-weighted composite chemistry is P1:
  BPL 55.585%, MgO 1.495%, CO₂ 8.31%, SiO₂ 14.995%, Cd 23 ppm; P2:
  BPL 57.17%, MgO 1.31%, CO₂ 8.48%, SiO₂ 11.965%, Cd 17 ppm.
  At this 50% t0 fill, each 50,000 t component occupies display location
  R0–R250 or R250–R500, and R500–R1000 is empty. The shares, ranges and
  profile-to-pile mapping are **ASSUMED** until pile survey/SQMS segment data
  arrive. The locations are schematic, not surveyed spatial order, stacking
  chronology or reclaim sequence. The visual bands are not solver zones;
  the underlying pile mass and designated campaign grade are solver inputs.
  Benguerir raw moisture remains 10–14%;
  Louta moisture 15%, SG 2.9 and F100 10 mm remain source metadata. This is
  not evidence that the sites are chemically equivalent and not a
  live/pile-specific Benguerir assay.
  The p. 18 combined-product specification (BPL 68.29%, MgO <0.6%,
  CO₂ 6.81%, SiO₂ <4%, Cd 17 ppm, moisture <22%, SG 3) is retained as a
  separate reference: it is not relabelled as washed-only and does not replace
  the TBT/BT/MT demonstration vectors.
- **Capacity conflict preserved, not resolved**: Table 20's reclaimer
  (2,100/2,500 t/h humid) vs the B02 expansion interface's raw
  bucket-wheel reclaimer/RB rating (2,600/3,120 t/h). Whether B02
  upgrades, replaces or supplements the Table 20 reclaimer is TBC — a
  raw→C2 demonstration at 2,600 t/h assumes the B02 interface is
  selected and the Table 20 bottleneck has been resolved.
- **Scope semantics:** the source table's “Out of scope” marking remains the
  source package's scope statement, not a finding that the machine is outside
  this planner. The 28-Jul user instruction makes all six upstream machines
  solver-backed in the demonstration; live SMS ownership/integration is still
  TBC.
- **Validated default:** `BG_TRAIN` creates exact integer feed campaigns
  2,695 / 2,535 / 2,695 t and 1,800 t recovered output per line. The pinned
  result is FEASIBLE with 43 jobs, zero unmet demand, zero spill and zero
  unstacked surge; signature `5c5d9dc60ce40f12`.
- **B01 document conflict preserved**: B01's earlier "1 reclaimer
  `04BG-RC-01` + 1 stacker 2,200 t/h nominal" is NOT assumed identical
  to Table 20's "2 stackers + 1 reclaimer, 2,100/2,500 t/h each" — both
  kept as distinct, non-identical sources for the same process stage.
