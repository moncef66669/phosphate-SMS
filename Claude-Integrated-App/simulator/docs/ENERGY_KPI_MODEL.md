# Energy and movement KPI model

## Purpose

The model estimates plan electricity before plant meter integration and makes
rehandling, empty positioning, and machine conflicts visible. It is a planning
estimate, not an electrical design calculation or a substitute for metered
energy.

## Calculation

The reported specific electricity is:

`SEC = (machine work kWh + conveyor traversal kWh + positioning kWh) / useful on-time external delivery t`

Using useful delivery as the denominator is deliberate. Moving the same tonne
several times for blending increases the numerator without increasing useful
output, so inefficient going-and-coming is visible in both SEC and the handling
factor.

- Machine work: job input tonnes multiplied by the configured intensity for
  stacking, reclaiming, or dispatch/other work.
- Conveyor traversal: physical tonnes multiplied by the number of distinct
  links traversed and the configured kWh/t/link coefficient.
- Empty positioning: scheduler-reserved positioning hours multiplied by the
  configured positioning power.
- Conflicts: unresolved positioning hours and count are reported separately.
  They are not counted as consumed energy because the schedule does not claim
  that conflicted movement was executed.

## Default assumptions

Until approved motor/load and meter data are supplied, the UI clearly marks
these defaults as `ASSUMED`:

| Parameter | Provisional value |
|---|---:|
| SEC reference | 1.00 kWh/useful delivered t |
| Conveyor traversal | 0.04 kWh/t/link |
| Stacking work | 0.12 kWh/t |
| Reclaiming work | 0.15 kWh/t |
| Dispatch/other work | 0.10 kWh/t |
| Empty-positioning power | 200 kW |

They are editable under **Energy KPI reference & model assumptions**. Scenario
configuration keys are `ENERGY_REFERENCE_KWH_PER_T`,
`ENERGY_LINK_KWH_PER_T`, `ENERGY_WORK_KWH_PER_T`, and
`ENERGY_POSITIONING_POWER_KW`.

## Planner objective

Productive work and route traversal can now be included in CP-SAT with
`ENERGY_OBJECTIVE_ENABLED=true`. The integer objective uses centi-kWh per
handled tonne (`ENERGY_OBJECTIVE_CENTIKWH_PENALTY` sets its priority), while
the result records `work_link_energy_kwh` in physical modelled kWh.

The objective is opt-in in every built-in baseline because the coefficients
above are provisional. It does not optimize idle electricity, start/stop
electricity, or positioning electricity. Those remain reported estimates.
Any work/link intensity above 100 kWh/t is rejected while the objective is
enabled as an integer-solver safety guard, not as a process-performance limit.

## Acceptance path

Replace the provisional reference with an agreed metered baseline for the same
system boundary and product-tonne basis. Replace the coefficients with equipment
power curves or interval-meter regression by activity state. Until then, use
the KPI for scenario comparison and inefficiency screening, not contractual
energy reporting.
