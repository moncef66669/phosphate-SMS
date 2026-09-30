# MPH model code map

Package version: **1.15.0**. Stable concept IDs match `model_manifest.json` and
the result-analysis definition tools.

Revision 1.11: `datasets.apply_all_enabled_blending_defaults` installs all
modeled stockpile permissions, optimized shares and both treatment choices.
`blending.chevron_settings.active_spaces` separates those permissions from
physical construction readiness; occupied unstarted piles retain ordinary
material handling in `washing.prepare_materials`, the optimizer and simulator.
`washing.reconcile_derived_policy` compares preset feed forecasts at the actual
scenario horizon/grid before installing yield-aware ceilings.
Pre-washing output pools remain selectively reclaimable: daily feed-share
constraints do not establish inseparable product blending.

Revision 1.12: `optimizer._solve_uniform_plan` retains an original-hard-model
typed feasibility attempt when `washing_seed.physical_seed` returns no seed;
`WASHING_BOOTSTRAP` records the path. Normal minute acceptance is unchanged.
The washing-cost objective/planned KPI use a coefficient-rounded nominal-yield
proxy; flow-ledger and simulated costs instead use quantized physical output.

Revision 1.14: the washing bootstrap first verifies a no-command incumbent on
the unchanged hard model, then gives the authoritative objective search most
of the window budget. `washing_seed.physical_seed` now replays grade-typed
exogenous arrivals at the correct bucket boundary, preserves legitimate
external overflow, uses a conservative multi-material treatment envelope, and
reconciles aggregate/per-material integer yield only in process buffers left
free by the typed verifier. `WASHING_BOOTSTRAP` distinguishes mapped,
repairable-hint and verified no-command paths; the latter preserves explicit
unmet/shortfall/overflow evidence and is a feasibility guarantee, not a
service-quality certificate.

| Concept ID | Formulation role | Implementation anchor | Configuration/input | Run evidence/output | Status |
|---|---|---|---|---|---|
| `mine-arrivals` | daily external raw-grade physical supply, obsolete-grade mass-preserving migration and operating stock policy | `mph_sim/mine_arrivals.py:refresh`; `mph_sim/mine_arrivals.py:reconcile_grade_catalog`; `ui/pages_mine_arrivals.py:page_mine_arrivals`; `ui/common.py:init_state`; `optimizer.py:_solve_plan_impl`; `simulator.py:Simulator._execute_mine_arrivals_interval` | `MINE_ARRIVALS`; scenario-keyed `config/mine_arrivals.json` | generated exogenous flows, material pools, `actual_mine_arrivals` and rejection logs | code-verified |
| `flow-input` | `X[c,t]`, input physical tonnes | `mph_sim/optimizer.py`, variable construction and `X` | routes, machines, stock, demand | `decision:job_input_mass:*`, jobs/bucket flows | code-verified |
| `flow-output` | `Y[c,t]`, yield-adjusted output | `mph_sim/optimizer.py`, `Y` and yield linkage | process transfers, washing mode | `decision:job_output_mass:*` | code-verified |
| `inventory` | physical mass balance | `mph_sim/optimizer.py`, “inventory mass balance” | spaces, exogenous flows | `state:inventory:space:*` | code-verified |
| `material-inventory` | grade/material pool balance | `mph_sim/washing.py:prepare_materials`; `optimizer.py:material_inv` | initial material catalogue | plan material inventory snapshot | code-verified |
| `unmet-demand` | demand escape slack | `mph_sim/optimizer.py`, “demand with unmet slack” | demand rows and priority | `output:unmet_mass:destination:*`, KPI `unmet_t` | code-verified |
| `quality-slack` | soft assay violation | `mph_sim/optimizer.py`, quality constraints | destination specs | `diagnostic:offspec_slack:*` | code-verified |
| `campaign-receiving-spec` | demand grade selects a receiving envelope without becoming stock identity | `mph_sim/domain.py:Scenario.demand_spec`; `mph_sim/optimizer.py:demand_specs_at`; `mph_sim/datasets.py:_train_export_grades` | demand `requested_grade`, specification-grade limits and allowed destinations | grade-specific quality breakdown, delivery/unmet | code-verified |
| `machine-state` | start/stop/changeover/hold | `mph_sim/optimizer.py`, machine state variables | equipment penalties, campaign policy | objective breakdown and fragmentation KPIs | code-verified |
| `service-first-primary` | emergency then qualified service | `mph_sim/optimizer.py:primary_expression` | `OBJECTIVE_MODE` | objective mode, scope, passes | code-verified |
| `service-first-refinement` | protected secondary tiers | `mph_sim/scheduling_objective.py:refine_objectives`; optimizer `levels` | service degradation and secondary budgets | `objective.passes` | code-verified |
| `weighted-objective` | scaled weighted sum | `mph_sim/optimizer.py`, `terms` and `_objective_weight_coefficients` | scenario weights and penalties | objective weights/breakdown | code-verified |
| `washing-quality` | relative assay transform | `mph_sim/washing.py:treated_quality` | mode `changes_pct` | treatment job source/output qualities | code-verified |
| `washing-yield` | dry-solids conservation to wet output | `mph_sim/washing.py:output_factor` | mode dry yield and moisture | washing feed/product/loss KPIs | code-verified |
| `prewash-blend-policy` | one site-level catalogue/envelope; per-source-stockpile switches govern eligibility across physical stages; fixed recipes are excluded whole, never renormalized | `mph_sim/prewash_blending.py:enabled_source_spaces`; `eligible_matrix_catalog`; `candidate_components`; `effective_process_stages`; `mph_sim/optimizer.py`, “pre-washing feed blend campaigns” | `PRE_WASHING_BLENDING.points.*.enabled_source_spaces` | treatment jobs grouped by process stage/source/day and scenario config snapshot | code-verified |
| `prewash-route-gating` | dormant cross-feeds activate only for effective eligible sources; ordinary requested-grade washing remains available when blending is inactive | `mph_sim/optimizer.py:_build_base_candidates`; `_candidate_serves_destination`; `mph_sim/prewash_blending.py:source_spaces_for_stage` | source enablement and physical source/target paths | candidate list / selected process jobs | code-verified |
| `blend-enablement-persistence` | checkbox callbacks persist enablement before committing; page changes and fresh built-in sessions restore switches, without overwriting recipes or physical state | `ui/pages_data.py:_commit_blending_enablement`; `ui/common.py:_restore_blend_enablement_preferences`; `mph_sim/blend_preferences.py:save_blend_enablement`; `apply_saved_blend_enablement` | scenario-keyed `config/blending_preferences.json` | scenario configuration and save/error notice; prior plan invalidated only after accepted edit | code-verified |
| `mph-shared-blend-catalogue` | one global MPH mode and reusable catalogue; per-pile booleans permit optional construction and each active pile selects a matrix or optimized shares | `mph_sim/blending.py:chevron_matrix_catalog`; `mph_sim/dynamic_blending.py:add_dynamic_pile` | `CHEVRON_STACKING` v7, `quantity_mode=DEMAND_DRIVEN` | `blend_campaigns[*].selected_matrix`, composition, physical campaign state | code-verified |
| `mph-dynamic-input-tonnes` | variable pile total bounded by physical capacity; at least two selected grades, each contributing the adjustable aggregate floor, default 500 t | `mph_sim/dynamic_blending.py:add_dynamic_pile`; `mph_sim/blending.py:chevron_minimum_input_t` | shared `min_input_grade_t`, matrix fractions or optimized bounds | selected total and exact `composition[*].tonnage_t` | code-verified |
| `mph-physical-readiness` | reclaim follows complete grade-specific construction; later lots reopen only after depletion, and actual shortfalls keep future lots held | `mph_sim/dynamic_blending.py:add_dynamic_pile`; `mph_sim/blending.py:update_chevron_campaign_state`; `reopen_depleted_chevron_campaigns`; `mph_sim/simulator.py:Simulator._adopt_blend_plan`; `Simulator._prepare_dynamic_blend_stack`; `Simulator._blend_withdrawal_issue` | per-job selected lot metadata, frozen state, measured job tonnes and preserved accounting ledger | calculated readiness, ordered campaign history, physical completion, execution hold reason | code-verified |
| `mph-derived-chevron-passes` | equivalent traversals follow moved tonnes/rate, explicit stacking traverse speed and usable geometry; missing inputs leave estimate unavailable | `mph_sim/blending.py:chevron_job_pass_estimate`; `update_chevron_job_passes` | `stacking_traverse_speed_m_min`, stockpile/work-area coordinates, job execution mass/rate | derived job pass metadata | code-verified |
| `washing-seed-selection` | verify a same-hard-model no-command incumbent first; map grade-typed external arrivals and conservative multi-material treatment limits in the richer seed; keep PAP certification and chevron construction authoritative | `mph_sim/optimizer.py`, “finding a material-balanced washing incumbent”; `mph_sim/washing_seed.py:_g10_seed_variant`; `physical_seed` | source assay, treatment modes, G10 limits, exogenous material map and explicit slack/overflow contracts | `WASHING_BOOTSTRAP` path, seed diagnostics and first usable incumbent | code-verified |
| `blend-construction-quality` | v7 qualifies actual mixture chemistry only against receiving profiles it supplies; legacy fixed campaigns retain reachable-receiver checks; fewer components are a tie-break | `mph_sim/dynamic_blending.py:add_receiving_quality`; `mph_sim/optimizer.py`, legacy blend quality block; `mph_sim/washing.py:prepare_materials` | component assays, receiving specs and actual destination-use literals | mixture quality, receiving certification and campaign composition | code-verified |
| `g10-certification` | permanent PAP demand specification plus hard receiving intervals; treatment enablement does not remove the contract | `mph_sim/washing.py:g10_spec` and `synchronize_pap`; `mph_sim/datasets.py:apply_planning_demand_profile`; `mph_sim/validation.py:validate_scenario` | G10 limits, PAP destination and demand rows | certified grade, quality margins, or explicit unmet demand | code-verified |
| `washing-raw-feed` | secondary tier minimizing feed dry solids | `mph_sim/optimizer.py:raw_feed_expression` | treatment candidates | objective passes/breakdown | code-verified |
| `washing-treatment-cost` | product-dry-tonne treatment cost | `mph_sim/optimizer.py:washing_cost_expression`; `kpis.py` | `cost_per_tsm_t` | `washing_treatment_cost` | code-verified |
| `minimum-job-volume` | hard complete-job mass floor | `mph_sim/scheduling_objective.py:add_minimum_job_volume` | `MIN_JOB_VOLUME_T` | extracted jobs / validation reasons | code-verified |
| `parcel-policy` | per-bucket parcel floor with exception | `mph_sim/campaigns.py`; optimizer `short_parcel` | campaign policy, penalty | below-floor KPIs | code-verified |
| `minute-overlay` | physical placement of bucket decisions | `mph_sim/runs.py` | positioning and campaign settings | execution windows, physical runs | code-verified |
| `kpi-service` | demand-credit calculations | `mph_sim/kpis.py:compute_kpis` | run demand and deliveries | service/unmet KPIs | code-verified |
| `kpi-quality` | delivered quality compliance | `mph_sim/kpis.py:compute_kpis` | jobs, assays, specs | on-spec/off-spec/unknown KPIs | code-verified |
| `evidence-bundle` | immutable run context | `mph_sim/analysis_tools.py:build_run_bundle` | exact scenario and plan | stored run artifact | code-verified |
| `solve-entry-routing` | choose uniform, weekly, or mixed execution | `mph_sim/optimizer.py:_solve_plan_impl` | grid and rolling flags | solver identity, reasons, breakdown | code-verified |
| `uniform-cp-sat-solve` | build, solve, accept, refine, and extract one grid | `mph_sim/optimizer.py:_solve_uniform_plan` | scenario and `SolveOptions` | `PlanResult`, passes, terminal state | code-verified |
| `weekly-rolling-plan` | solve state-coupled civil-day windows | `mph_sim/optimizer.py:solve_weekly_rolling_plan`; `_merge_weekly_windows` | weekly budgets and target profiles | merged or explicit partial weekly result | code-verified |
| `mixed-resolution-plan` | tactical solve plus fine-prefix refinement | `mph_sim/optimizer.py:solve_mixed_resolution_plan`; `planning_time.py:merge_mixed_resolution_results` | near/far grid and commit settings | merged plan or tactical fallback | code-verified |
| `execution-repair` | validate and repair minute placement | `mph_sim/optimizer.py:accept_execution_candidate`; `repair_execution_constraints` | incumbent bucket flows | accepted incumbent and timing evidence | code-verified |
| `parcel-consolidation` | reduce short command buckets without changing protected totals | `mph_sim/parcel_consolidation.py:consolidate_parcels` | consolidation budgets/policy | accepted polish or retained incumbent | code-verified |

## Interpretation path

For a result question, resolve its stable concept ID, load the run-specific
model-context snapshot, then trace:

`scenario input -> candidate/variable -> constraint/objective tier -> selected
decision -> extraction/minute overlay -> KPI or chart`.

Current source files are useful for drift detection, but an older run's
embedded manifest and documents remain authoritative for what that run meant.
