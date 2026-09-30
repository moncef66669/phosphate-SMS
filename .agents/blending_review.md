# Blending and execution review — 8 September 2026

Reviewed the recovered current application in `Claude-Integrated-App/simulator/` against report Rev AC. The older `simulator/` tree lacks the report's blending and weekly planner; it was not changed.

## Implemented corrections

- Fixed-matrix chemistry uses actual integer-tonne batch allocation, not unrounded requested percentages. A second component rounded to zero is rejected as an invalid physical blend.
- Missing component assays remain unknown in planned and executed blends. Unknown constrained material is excluded from destination delivery, including soft contaminant limits; `UNKNOWN_ASSAY` explains withheld supply.
- The first actual stacking movement locks the matrix. Replay updates campaign progress immediately and holds reclaim until every component is physically complete and the configured readiness boundary has passed.
- Completed blends with unknown or out-of-range hard receiving assays are held. Carried measured pile assays survive replay initialization and are used in replanning instead of being overwritten by catalogue chemistry.
- Actual incoming moisture controls executed blend dry-solids weighting.
- Process-feed actuals credit physical input mass; recovered material remains output inventory.
- Exogenous source-to-target transfers clip against destination room before withdrawing source inventory. This removes an unreported mass loss at a full destination.
- Discarded corridor arrivals accumulate in `Simulator.unstacked_t`, including fractions below the alarm threshold.
- Explicitly unvalidated minute schedules are held during replay; ASP-rejected work cannot inflate actual delivered service by falling back to bucket-average execution.
- Sparse exact job profiles retain zero mass in unscheduled buckets.
- Destination priority multiplies unmet-demand cost. Variable constructed blends are not additionally charged stale catalogue giveaway; construction off-spec, giveaway and priority-weighted unmet totals are exposed in the plan breakdown.
- Dated demand priority overrides feed the unmet objective and daily process groups. Warm starts prefer higher-priority demand; weekly merged diagnostics preserve the new costs and preparation counts.
- Input checks reject nonfinite assays, invalid priority, inverted safety-margin intervals and malformed fixed matrices even when solving outside the UI.
- Tactical cross-pile transitions reserve a conservative preparation duration in the next bucket using its effective machine/conveyor/plant bottleneck. Possible work-face midpoints bound translation; exact minute timing retains authority. Carried `MACHINE_INITIAL_SPACE` positions receive the same first-bucket reservation. Same-pile continuation retains full capacity.

## Verification

`test_review_blend_integrity.py` adds 17 focused regressions. `test_positioning_reserve.py` adds 5 tests for bottleneck basis, initial positioning, same-pile continuation and 15-minute grids. Combined blending, execution, process-feed, weight, policy, weekly-demand and demand-integrity suites: **90 passed**.

Broader engine and minute-execution suites before the separate timing audit: **37 passed, 1 failed**. The failing assertion expected exactly two rejected default-S3 jobs; the current incumbent had six. That assertion was not weakened. A separate timing review is addressing propagation of unavailable upstream work.

After the preparation reservation and companion dependency-propagation fixes, a real seven-day S3 solve produced **147 jobs, 648 validated minute windows, zero unvalidated/fallback windows, zero timing errors**, with all 20 planning machines represented. The saved evidence is `generated_outputs/project_review/positioning_reserve_result.json` and the corresponding local plan pickle. This is executable schedule evidence, not proof of full demand service: that solve still had 305,560 t of total unmet feed/final demand. The default-S3 regression now requires zero rejects, strengthening its prior known-defect expectation.

## Remaining boundaries

- Ordinary non-campaign piles retain aggregate snapshot chemistry and physical-tonne replay averaging. There is no spatial assay/layer/reclaim-face model.
- Optimized blend construction assumes each incoming grade's catalogue chemistry represents source material; actual assays can disagree, and replay now holds a completed blend that breaches a hard receiving limit.
- Minute replay still attributes a job's link load to its dominant route; exact multi-path plan rows are available for planned route KPIs. A route disturbance on a nondominant path is not a complete physical conveyor simulation.
- Washing output chemistry is a configured grade transformation, not a calibrated beneficiation response model.
- A tactical FEASIBLE result can contain non-dispatchable minute instructions; business claims must use executable/actual service and explicit timing rejections.
