# S0 below-floor parcel audit — 14-Sep-2026

Every parcel below the configured minimum, on the reproducible seven-day S0 run
(S3 `FULL_FLOW`, seed 42, default budgets), traced to a cause and tested for
necessity. Result: **none of them was necessary.** The shipped configuration
now produces zero.

## Pass 1 — five 1-tonne parcels, all mid-campaign bridges

| job | machine | bucket | t | transfer / stage | its campaign |
|---|---|---|---|---|---|
| J000-067 | 130LG-ST-01 | 11 | 1.0 | `LOU-PRODUCT-STACK-L1` | 682 t over b11–b14, of which b14 carries 679 t |
| J000-067 | 130LG-ST-01 | 12 | 1.0 | same job | " |
| J000-067 | 130LG-ST-01 | 13 | 1.0 | same job | " |
| J072-006 | BG-RAW-ST-1 | 90 | 1.0 | `BG-L1-RAW-STACK` | b89 = 943 t, b90 = 1 t, b91 = 250 t |
| J072-015 | BG-MINE-ST-1 | 90 | 1.0 | `BG-L3-MINE-STACK` | b89 = 250 t, b90 = 1 t, … |

Every one sits **inside** an otherwise substantial contiguous campaign on a
machine whose `rate_min_tph` is **zero**. Their function was to keep the
activity group alive across a bucket, because a stop plus a later start costs
`(10 + 20) x w5` while a token tonne cost only the short-parcel penalty and one
tonne of flow. They were bridges, not deliveries.

Two things confirmed that reading:

* **Banning one moved the trick rather than removing it.** Forbidding
  `J000-067@b12` produced *nineteen* below-floor parcels instead of five (and,
  incidentally, 3,805 t better service).
* **The ban verdicts were incoherent as a necessity test.** Across the five,
  banning changed unmet demand by −3,805 t to +18,649 t — swings three to four
  orders of magnitude larger than one tonne of material. That measures CP-SAT
  search perturbation, not the parcel's physical necessity.

### The 20-minute window was not a fix

Pass 1 also widened such a parcel to the 20-minute minimum run, and
`prefer_continuous` widened these five to a **full 60-minute window at 1.0 t/h**.
That is not a credible operating rate on a machine with no declared minimum; it
dressed an implausible allocation as a normal run. Widening a below-floor parcel
is now refused outright: the window keeps its physical minimum duration and
carries `rate_credibility = BELOW_MIN_PARCEL`.

## The real remedy — an explicit held state

The model had no way to say *stay on, move nothing*. It now does
(`EQUIPMENT_HOLD_BUCKET_PENALTY`, `PlanResult.machine_hold_buckets`,
assumption S3-A34). With it, the same week went from five below-floor parcels
to **one**.

## Pass 2 — the last parcel, and why it is gone

| job | machine | bucket | t | run |
|---|---|---|---|---|
| J024-020 | ST3 | 35 | 1.0 | `ST3#R012` — 1,800 t over 292 min (53 active, 239 held), segments b35 = 1 t and b39 = 1,799 t |

It was the *opening* token of the L1 product-stacking campaign, not a bridge.
Three probes on identical inputs and seed settled whether it was needed:

| configuration | status | gap | unmet | service | below-floor |
|---|---|---|---|---|---|
| exception priced at 40 (pass-1 default) | FEASIBLE | 14.62 % | 235,498 t | 60.08 % | **1** |
| exception priced at 4,000 | FEASIBLE | 15.36 % | **231,809 t** | **60.71 %** | **0** |
| hard floor, no exception at all | FEASIBLE | 14.70 % | 251,724 t | 57.33 % | 0 |

It was **not necessary**: pricing the exception properly removes it *and*
serves 3,689 t more demand. A hard floor also removes it but costs 16,226 t of
service, so the relaxable-but-properly-priced floor dominates both.

It also disappears with more search alone: at a 14 s per-window deterministic
budget the shipped configuration already reports zero below-floor parcels with
better service. At the default budget the plan carries a **15.4 % optimality
gap**, so a single 1 t opening parcel was well inside the unexplored gap — an
artefact of an unproven incumbent, not a physical requirement.

`SHORT_PARCEL_PENALTY` therefore defaults to **4,000**, which is the one
measured number in the campaign policy. Everything else in it remains ASSUMED
(S3-A32, S3-A34).

## What is still not validated for plant dispatch

* `min_parcel_t = 250 t`, `min_run_min = 20 min`, `max_hold_min = 30 min` are
  ASSUMED demonstration figures. No minimum stable belt/drive loading, permitted
  start frequency, or drive-thermal restart limit exists in the workspace.
* Whether these plants prefer an idling drive to a stop/restart at all, and at
  what energy and wear cost, is unknown; `EQUIPMENT_HOLD_BUCKET_PENALTY = 8` is
  bounded by argument only.
* Every stacker here declares `rate_min_tph = 0`. Until real floors are
  supplied, nothing in the model validates how slowly a stacker may run, which
  is exactly why `NO_STABLE_MINIMUM` is reported on 89 of the 653 windows in the
  final plan rather than silently treated as validated.
