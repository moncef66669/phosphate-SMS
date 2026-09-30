# Test-suite triage — 14-Sep-2026

Two full runs on identical inputs classified every failure: one with the
schedule-fragmentation work active, one with it force-disabled (the campaign
policy switched off and the continuous-feed threshold reverted to the historic
absolute one-tonne rule).

| run | failed | passed | introduced by this work |
|---|---|---|---|
| pre-change behaviour forced | 81 | 948 | — |
| pass 1, before rebaselining | 67 | 962 | 8 pinned hashes |
| pass 1, after rebaselining | 59 | 970 | 0 |
| **pass 2, final (14-Sep-2026)** | **58** | **987** | **0** |

The final run was classified the same way: every one of the 58 remaining
failures also fails with the pre-change behaviour forced, and 23 tests that
failed in that baseline now pass.

**Introduced by this work: 8** — all pinned material-allocation hashes or
tonnages. Each was rebaselined with the reason recorded next to the value, and
all 8 pass. No test was rebaselined to conceal a behaviour change: the two
tests whose *behaviour* this work deliberately changes
(`test_schedule_coalescing`, `test_continuity_policy_ui`) were rewritten to
assert the new contract and to explain why, not re-pinned.

**Known blind spot in the differential.** The force-disable switch reverts the
solver and timing policy but not the display-coalescing rule or the config
schema. Two failures therefore appeared in *both* runs even though they are
mine; both are fixed and are not in the 59.

## The 59 pre-existing failures, by cause

| # | cause | affects the S0 execution path? | blocks release? | tests |
|---|---|---|---|---|
| 1 | **Louta baseline grade drift.** The built-in Louta product distribution uses `BT-LOW-CD` / `MT-LOW-CD` while the tests expect plain `BT` / `MT`; composite quality and share assertions follow from that one mismatch. | No — dataset content, not scheduling | **Yes for Louta reporting.** Either the dataset or the tests state the wrong grade; until that is settled, Louta product chemistry in any export is unreliable | 14 (`test_louta_distribution_model` ×5, `test_louta_grade_distribution_ui` ×4, `test_louta_layout_correction` ×4, `test_zone_model::…102MG-ST-02`) |
| 2 | **Sidebar page register drift.** The app exposes 21 pages including `21 · Business Case`; the tests expect 20 ending at `20 · Blending`. | No | No — cosmetic/navigation, but the deep-link map is stale | 10 (`test_monitor_workspace` ×7, `test_ux` ×3) |
| 3 | **Missing document inputs.** `report_sources/` and `CLAUDE-CONTEXT.md` are not at the path these tests expect (`simulator/../`), so the report-text and mojibake sweeps cannot run. | No | No — but the mojibake sweep is a real guard that is currently inert. I ran its check manually over all 161 changed/other `.py` and `.md` files: 0 mojibake hits, all valid UTF-8 | 8 (`test_report_washing_residence` ×5, `test_benguerir_b02::test_no_mojibake_in_sources`, `test_benguerir_cleanup::…page_count`, `test_benguerir_cleanup::…stage_separation`) |
| 4 | **`migrate_loaded_builtin` always reports `"solver"`.** `weekly_planning_changed = True` is hard-coded, so a metadata-only migration is never reported as metadata-only. | No | No — but it needlessly invalidates a hot-reloaded plan on every migration | 2 (`test_equipment_state_timing`, `test_ux::…metadata_only_migration`) |
| 5 | **Mimic / geometry expectations.** Evidence-label, position-pending and relative-distance assertions against the current layout metadata. | No | No | 6 (`test_mimic` ×2, `test_global_concurrency`, `test_timeline_ux` ×2, `test_revd_alignment`) |
| 6 | **Exported sample data is stale.** `sample_data/` still carries revision `2026-09-08-…-showcase-v30` against the built-in `2026-09-10-…-objective-v31`. | Indirectly — the exported scenario is not the built-in one | **Yes.** Anyone loading the shipped sample data gets a different scenario from the built-in. Re-run `make_sample_data.py` | 2 (`test_m16_model_consistency` ×2) |
| 7 | **Benguerir washing-stage target drift.** The pinned per-stage dict expects `BEN-WASH-L3: 8085 t`; the scenario produces `8082 t`. | Yes — it is an S0 stage target | No — a 3 t rounding difference, but the pin should be corrected at source rather than left failing | 3 (`test_benguerir_six_pile_planning`) |
| 8 | **S2 demand not fully served at the test's budget.** `test_unmet_zero_when_capacity_sufficient` asserts zero unmet; the 8-second deterministic budget leaves 8,401 t (pre-change) / 8,500 t (with this work) unserved. At 90 s the same scenario solves to **OPTIMAL with zero unmet** in every configuration. | Yes — solver search quality | No — the scenario *is* fully servable; the test's budget is too small to prove it | 1 (`test_engine`) |
| 9 | **Zero-quality dilution guard.** `BEN-WASH-L1@3: BPL 56.1` — a washing-line delivery whose blended BPL falls below the guard. | **Yes** | **Yes.** A delivery blended down to 56.1 BPL is a quality-model defect, independent of scheduling. It reproduces identically with this work disabled and needs its own investigation | 1 (`test_polish`) |
| 10 | **Optimized-blend component selection under a timed outage.** Expected `{A:50, B:50}`, produced `{A:65, B:5, C:30}`. | Yes — blend design | **Yes for blending.** The recipe chosen under an outage differs from the pinned expectation; reproduces with this work disabled | 1 (`test_optimized_blend_schedule`) |
| 11 | **M16 destination servability after the bay correction.** | Yes | No — investigate with cause 1 | 1 (`test_m16_bays`) |
| 12 | Remaining single-assertion drift in signature-adjacent tests already covered above. | No | No | 10 |

## Release view

Blocking, in priority order, none of them caused by this work:

1. **Cause 9** — a 56.1 BPL washing-line delivery. Quality correctness.
2. **Cause 1** — Louta product grade identity (`BT` vs `BT-LOW-CD`).
3. **Cause 6** — regenerate `sample_data/` so the shipped scenario matches the
   built-in.
4. **Cause 10** — blend recipe under a timed outage.

Not blocking but should be corrected at source rather than left red: causes 7
(3 t stage-target pin), 8 (raise the test budget or pin the achievable value),
4 (`weekly_planning_changed`), 2 and 3 (test/asset paths).

## Addendum, 14-Sep-2026 (pass 2)

Pass 2 (held-running state, rate credibility, per-window run identity, measured
short-parcel penalty) changed the allocation again, so the same three pinned
values were rebaselined a second time with the reason recorded:
S2@2 -> `b498a9d55c2e6283`, S3 BG_TRAIN@3 -> `853415c7f5070d37`, S3 default@3
unmet -> 231,809 t, S3 BALANCED full-budget unmet -> 254,534 t.

### Cause 13 — the rolling decomposition is not monotone in search budget

`test_zone_model::test_more_budget_never_worsens_the_objective` asserts that a
larger deterministic budget never yields a worse objective. It does not hold,
**and it did not hold before this work**:

| configuration | budget 3 | budget 10 |
|---|---|---|
| pre-change behaviour forced | objective 4,741,033,336 · unmet 280,560 t | objective 4,755,036,936 · unmet 281,060 t |
| with this work | objective 5,553,861,767 · unmet 231,809 t | objective 5,578,465,857 · unmet 231,859 t |

The cause is structural. The weekly plan is seven *state-coupled* CP-SAT
windows, each given `min(max(budget/7, 1.0), 2.0)` deterministic units. A larger
budget changes each window's own incumbent, and a better day-1 sub-plan can hand
day 2 a worse initial state. The merged objective is therefore a sum of seven
independently truncated searches and is not monotone in the budget. The test's
own docstring predates the rolling decomposition.

**Not blocking**, but it must not be read as a reproducibility guarantee: within
one budget the solve is deterministic (single worker, fixed seed, deterministic
time), and *across* budgets it is not comparable. Any budget tuning has to be
evaluated on the metric of interest (unmet tonnes, runs), not on the objective.

Objective values are also not comparable **across configurations**: this work
adds held-bucket and short-parcel terms, so 4.74e9 and 5.55e9 above measure
different objectives.

### Cause 14 — a latent test estimator this work exposed (fixed)

`test_m16_model_consistency::test_planned_stacking_never_exceeds_the_reception
_envelope` began failing. It was **not** a physical violation. The test summed
each job's per-run AVERAGE `rate_tph` and attributed all of it to the job's
`start_bucket`, so it checked only start buckets and misattributed every
multi-bucket campaign: a job spanning buckets 34-36 at 2,100 / 2,600 / 2,600 t
contributed its 2,433 t/h average to bucket 34 alone, on top of anything else
starting there, reading 2,933 t/h. The physical per-bucket load, taken from
`bucket_flows`, never exceeded 2,600 t/h anywhere in the plan (maximum exactly
2,600.0 t/h, zero buckets over).

The estimator happened to agree while the plan was fragmented into mostly
single-bucket jobs. Merging campaigns into genuine multi-bucket runs broke it.
The test now measures the exact per-bucket allocation across every bucket of
every job, which is strictly stronger than what it replaced — the same
correction applied to `test_engine::test_mass_conservation_in_plan` in pass 1.

This is the second time the fragmentation work has revealed that a test was
measuring per-run averages where the constraint is per bucket. Any remaining
assertion built on `job.rate_tph` for a multi-bucket job should be treated as
suspect.
