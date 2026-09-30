# Mine arrivals: implementation and validation

Date: 2026-09-21. Canonical app: `Claude-Integrated-App/simulator`.
Model package: 1.13.0. Verdict: **CONDITIONAL** for the complete weekly planner;
the new configuration, conservation and execution contracts pass focused tests.

## Why daily restoration existed

The old stocked-route demonstration imposed exact closing inventory so that
downstream demand could not be served entirely from opening stock while
upstream equipment stayed idle. `ASSUMPTIONS.md` documents that purpose.
Weekly interpolation of opening-equals-final targets made those targets hard
at every daily boundary. This is a demonstration policy, not a physical rule.

## Implemented

- Configure > Mine arrivals (page 24), enabled by default in the operator S0.
- Separate Benguerir/Louta tables, one civil day per row and raw grades across
  columns; whole physical tonnes, dates, totals, enablement and stock policy.
- Atomic scenario-keyed persistence in `config/mine_arrivals.json`; imports
  retain their own configuration, while built-in/hot sessions migrate once.
- The seven-day assumed supply is BEN 24,000 / 26,400 / 21,600 / 25,200 /
  22,800 / 24,000 / 25,200 t and LOU 12,000 / 10,800 / 13,200 / 12,000 /
  11,400 / 12,600 / 12,000 t. MT is 4--6% daily; lower raw grades dominate.
  Weekly totals are 169,200 and 84,000 physical tonnes respectively.
- Actual grade assays, moisture, washing yields/costs and opening stocks remain
  unchanged. Supply enters existing finite source buffers; no stacker or
  upstream transfer is bypassed. Volumes/mixes are editable assumptions, not
  approved mine-production figures or evidence of sustainable plant operation.
- Default OPERATING stock policy removes recognized legacy physical-pile
  closing-stock defaults; preserves custom values, minima, capacities, soft
  safety stock and zero-WIP closure. Legacy EXACT_DAILY remains selectable.
- Whole-tonne cumulative rounding conserves each complete civil day's input
  on native hourly/subhourly grids. Absolute offsets prevent rolling resets.
- Simulation shares free buffer capacity proportionally across simultaneous
  arrival grades and logs scheduled/accepted/rejected tonnes. A full buffer
  cannot silently lose incoming mass or favor the first grade row.

The validation skill shaped conservation, malformed-input, offset, persistence
and capacity tests. The formulation skill required the supply boundary,
assumptions, stock-policy semantics and execution limitations to be added to
the canonical model package and verified against source fingerprints.

## Checks

118 tests pass together: mine-arrival input/profile, UI, execution, washing UI,
weekly solver hardening, washing fallback, and analysis-bundle tests. Four
navigation-contract tests additionally pass. Model-package validation and
source-fingerprint checks pass.

Two broader existing UI assertions still fail: exact no-plan wording and a
post-solve expectation of Plan rather than the current Monitor page. Both were
reproduced with mine-arrival installation disabled, isolating them from this
feature. They were not hidden by changing unrelated application behavior.

## Seven-day trial and remaining limitations

Evidence: `generated_outputs/validation/mine-arrivals-20260921/`
`FULL_FLOW-168h-both-consolidated-mine-arrivals/`.

FULL_FLOW, 168 hourly buckets, both treatment choices, optimized blending,
minimum complete-job volume 0, 60 s/window wall allowance and 24 requested
deterministic units: **UNKNOWN in window 1**, approximately 42.4 solver seconds.
The mapped physical seed cannot accept exogenous supply; the unfixed full
typed fallback also returned UNKNOWN. No feasible weekly schedule was obtained.
Zero jobs/KPIs and balance residuals on this failed/empty result are not accepted
operating results and do not prove physical infeasibility or supply shortage.

Prewash blending still constrains source-stockpile shares, not each raw-grade
share inside a mixed source. Actual material identities/assays are retained,
but a designated source label must not be interpreted as a pure-grade pool.
All five built-in inlet buffers start full. Without withdrawals, the first
hour's 1,500 t is explicitly rejected in execution; this is covered by tests.
Configured supply therefore differs from guaranteed accepted supply. Opening
buffer headroom was not silently changed to manufacture an accepted result.

The canonical Streamlit process serves this app on port 8505 with polling and
run-on-save enabled; its health endpoint returned HTTP 200. No other project
server was restarted.
