# Blending defaults and treatment-choice assessment

Study started 20 September and continued 21 September 2026. All results below
are planned simulator outputs, not plant observations. Evidence directories
are under `generated_outputs/validation/`; exact scenario, plan, validation,
objective-pass and model-context snapshots accompany each completed run.

## Implemented defaults

- Enable both source stockpiles at Louta and both at Benguerir, with optimized
  shares and both Simple/Full treatments eligible.
- Enable all eight MPH stockpile permissions and use optimized shares. An
  occupied, unstarted pile retains ordinary material identity and reclaim;
  permission becomes construction eligibility only once empty at a planning
  window boundary. No opening stock is erased or relabelled.
- Apply the requested baseline once to existing S0 sessions. Later checkbox
  and mode edits are not reset on page changes. Preserve recipes and physical
  campaign state when switching demand presets.
- Pre-drying remains an unimplemented physical-model placeholder; no false
  enablement or treatment jobs were added there.
- Retain the zero hard minimum-job-volume default. No grade intervals, raw
  assays, washing coefficients, yields, moisture values or prices were tuned.

The short-horizon washing reconciliation now compares against a preset with
the same time grid/horizon, preserving operator-edited feed commitments.

## Operational experiments

The shared setup uses actual saved grade configuration, seed 42, washing
enabled, all modeled blend permissions, optimized shares and both modes.
The feed and product masses below are wet/as-handled tonnes. These are not
matched economic comparisons across different horizons or demand profiles.

| Preset / experiment | Hours | Returned status | Washing result | Micro-job finding |
|---|---:|---|---|---|
| BALANCED, unchanged inventory policy | 24 | OPTIMAL | None | No 1 t jobs; short-horizon screen is too small for Louta blending |
| BG_TRAIN, unchanged inventory policy | 24 | OPTIMAL | None; 12,000 t train demand served from existing stock | No 1 t jobs |
| FULL_FLOW, unchanged inventory policy | 24 | PARTIAL, rejected minute schedule | 1 Benguerir daily blend; 1,152 t feed / 771 t product | 3 one-tonne upstream handling jobs; not an accepted plan |
| FULL_FLOW, relaxed terminal stock policy + consolidation | 24 | FEASIBLE | 2 Benguerir daily blends; 4 washing jobs; 2,301 t feed / 1,542 t product | All 109 jobs >=250 t; all washing jobs >=500 t; no 1 t jobs or treatment slices |
| BALANCED, relaxed terminal stock policy + consolidation | 48 | FEASIBLE | Louta L1 blend: 1,695 t feed / 1,141 t product; 3 washing jobs | Washing jobs/slices >=527 t; one 1 t downstream product-stacking job remains |
| BG_TRAIN, same relaxed policy | 48 | OPTIMAL | None; existing stock remains a valid alternative | No 1 t jobs |
| FULL_FLOW, unchanged terminal policy + consolidation | 168 | UNKNOWN in window 1 | No usable weekly plan | No claim of a successful micro-job-free week |

The accepted FULL_FLOW screen delivered 19,960 t final product, 25.20% of
its 79,200 t final-product demand. The accepted BALANCED 48 h screen delivered
7,541 t, 6.94% service. Reasonable washing-job sizes therefore do **not** mean
good overall service or a satisfactorily calibrated production plan.
The large preset results are bounded incumbents, not proofs of the best
treatment choice; several economic refinement passes timed out.

### What the inventory sensitivity actually changed

Only isolated study scenarios replace exact pre-washing terminal stock
equalities with lower bounds at 95% of opening stock. Opening quantities,
capacity and material assays remain unchanged. This permits both up to 5%
drawdown **and accumulation above opening stock**, up to physical capacity.
The accepted FULL_FLOW result did not use drawdown: BG-RAW-1 increased by
11,483 t and BG-RAW-2 by 2,851 t. It is evidence for a relaxed terminal policy,
not evidence that a 5% depletion itself removes micro-jobs. Budget and optional
consolidation also differed from the rejected screen; causality is not isolated.
This policy has **not** been installed as a production default.

### Why the one-day Louta screen has no blend

Shortening BALANCED scales each Louta line's output target to 572 t and its
raw feed ceiling to 849 t. Both source reclaimers have a hard 500 t/h stable
minimum on the hourly grid. Requiring a positive share from both sources
therefore requires at least 1,000 t feed, which cannot fit that ceiling.
At 48 h the target/ceiling become 1,143/1,697 t, and a substantial blend is
observed. The weekly values are 4,000/5,937 t per line. This is not a reason
to loosen chemistry, equipment limits or the job-volume floor.

## Treatment decision: controlled causal evidence

An isolated 5,000 t G10 order uses actual Benguerir RAW-TBT/RAW-BT-LOW-CD
assays, optimized 5-95% wet-feed shares, current washing transformations and
the production optimizer. Only the terminal product-inventory requirement
changes in the first comparison.

| Controlled case | Accepted outcome | Washing input jobs |
|---|---|---|
| Both modes, component pools may remain in stock | Simple, 5,000 t served; 95 t poorer treated component retained | 290, 1,992, 1,512, 1,992 t |
| Both modes, complete product buffer must clear | Full, 5,000 t served; zero residual product | 4,918 and 2,544 t |
| Full only, complete buffer clearing | Same Full result | 4,918 and 2,544 t |
| Simple only, complete buffer clearing | PARTIAL/minute-invalid; 380 t candidate delivery, not usable | Contains a 22 t job; rejected |

**Confirmed mechanism:** the pre-washing model constrains daily input shares
but retains separate treated-component pools that can be selectively
withdrawn. In the Simple case, the complete campaign exceeds G10's MgO limit
by 0.00512 percentage points and SiO2 by 0.01370 points. The delivered subset
passes because 95 t of the poorer component remains in stock. This is not an
inseparable physical pre-wash blend. Complete-output clearing is a diagnostic
counterfactual, not a proposed universal production policy.

Under whole-blend chemistry and current 5-95% source bounds, Simple does not
meet G10 at either configured site. Full can: Benguerir needs approximately
65.9-95% RAW-TBT wet feed; Louta approximately 25-95% RAW-BT. The accepted
Louta preset blend uses 31.09% RAW-BT and its daily composite is within G10,
but composite compliance alone does not prove final receipt compliance.
The controlled Full solution sits only 0.000352 ppm above minimum Cd; that
deterministic boundary result is not a robust plant recipe.

Where both options are hard-feasible, Simple has an advantage at two protected
objective tiers: 85% versus 65% dry recovery reduces feed required, followed
by $0.63 versus $1.04 per product-dry tonne. For 1,000 dry product tonnes,
ideal feed is 1,176.5 versus 1,538.5 dry tonnes and treatment cost $630 versus
$1,040. Changing lower-priority weights cannot override the protected recovery
tier. Historically Full-only pre-washing restrictions also precluded a genuine
option comparison; the new defaults remove that restriction.

## Independent numerical validation and limitations

The accepted FULL_FLOW sensitivity has zero unexplained residuals across 744
physical bucket balances, 2,904 material-pool balances and 725 pool/physical
inventory comparisons. All 145 serialized execution windows are validated;
spill is zero. Treatment transformations, integer yields and daily share/mode
checks pass. Sources feed at different buckets, so these checks do not prove
simultaneous premixing or spatial stockpile homogenization.

Planning treatment cost currently reports a rounded objective proxy. The
accepted sensitivity's serialized physical product costs $1,343.80896, whereas
the reported proxy is $1,346.115. The difference comes from theoretical yield
before output-tonne quantization and rounded cost coefficients. Use physical
output-flow cost for economics; do not treat proxy rounding as material loss.

Verdict: **CONDITIONAL** for the tested executable daily-feed approximation;
**not accepted** as a physically inseparable pre-washing blend model or as a
reliable, high-service, micro-job-free weekly planner.

Before recalibrating assays to manufacture a treatment split, couple the
blended-output composition to downstream withdrawal, distinguish objective
proxy cost from physical-product accounting, and resolve weekly incumbent
reliability plus residual downstream micro-jobs. Preserve the exact G10
contract and test quality uncertainty rather than exploiting tiny margins.

## Evidence and replay

- `validation/blending-presets-20260920/screen-v2/`: unchanged-policy screens,
  independent balance/assay audit script and rejected FULL_FLOW candidate.
- `validation/blend-dd5/`: accepted 24 h FULL_FLOW sensitivity and independent
  `audit-dd5.json`; both other demand presets are retained, including no-wash results.
- `validation/blend-48/`: 48 h Louta and train screens.
- `validation/blend-week/`: 168 h attempt with 630 s total / 90 s window caps;
  first window returned UNKNOWN after approximately 72 s.
- `validation/blending-presets-20260920/option-probe/`: executable one-factor
  treatment probe, all four exact scenarios/plans and regression assertions.

Canonical harness: `Claude-Integrated-App/simulator/scripts/validate_blending_presets.py`.
Run from `Claude-Integrated-App/simulator`, for example:

```powershell
python scripts/validate_blending_presets.py --presets BALANCED BG_TRAIN --hours 48 --wall 120 --deterministic 24 --consolidation --drawdown-pct 5 --output C:/Temp/blend-new-study
```

Use a new output directory; the harness does not overwrite existing cases.
`--wall` is the per-day/window allowance; the harness passes the corresponding
whole-horizon wall budget to the rolling solver. The earlier `screen-v1` and
`weekly-v1` folders retain rejected harness attempts (source-row summary error
and mistaken whole-week budget respectively); they are not acceptance evidence.

## Reusable skill improvements

The model-validation and model-results-interpreter skills were exercised,
independently reviewed and updated with narrow lessons: permission versus
physical readiness, exact UI/preset overlays, job versus slice/campaign scope,
and selective component withdrawal versus genuinely mixed output. Both skill
validators pass. Project-specific numbers remain in these artifacts, not in
the global skill instructions.

Product verification: **206 targeted tests passed** across the configuration,
persistence/UI and model suites (81 + 125). The outdated default-ceiling
assertion was corrected from 8015 t to the all-source 8082 t; reversibility and
custom-input protection still pass. The model package is version 1.12.0, its
source hashes are current, and its structural validator passes.

## Final fallback verification

A failed label-only physical seed previously disabled the separate typed
feasibility attempt. The corrected path retains a clone of the original hard
model with no extra seed restrictions. Focused tests confirm identical hard
constraints/domains, valid incumbent retention after a primary timeout,
necessary overflow still allowed, and continued rejection of invalid minute
execution. An independent static review found no constraint/acceptance regression.

The real 168 h FULL_FLOW rerun after this correction still returned UNKNOWN
in window 1 after about 64 s. Its new diagnostic is `WASHING_BOOTSTRAP mapped
physical seed: UNKNOWN`: a mapped seed existed, but the subsequent typed
bootstrap did not find an incumbent within its allocation. Thus fixing the
no-seed branch does **not** resolve this separate weekly failure. Final exact
artifacts are in `validation/blend-fallback/`, with unchanged grades, opening
stocks, zero job floor and 630 s total / 90 s window caps.

Remaining search-accounting limitations from review: failed physical-seed
searches do not preserve their deterministic metrics, and unmapped typed
bootstrap branch/conflict counters are omitted from totals. These limit
strict search-budget/effort comparisons; they do not justify accepting an
invalid physical schedule. They are recorded rather than claiming a fully
resolved weekly solver.
