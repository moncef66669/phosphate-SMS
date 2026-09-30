# Business-case experiments

There are two separate experiments. Neither represents measured OCP operator
performance, and neither automatically extrapolates a favourable example to
annual savings.

## 1. Independent SMS coordination experiment

`mph_sim.coordination_benchmark.run_coordination_benchmark()` runs a controlled
12-hour reduction of the three-site chain. All capacities, inventories, grades,
nomination quantities, calendar times and buffer capacities are explicit
simulation assumptions. It uses two finite mine product buffers, delayed C1/C2
interfaces, one shared M16 receiving path, segregated local stocks and a 50/50
on-spec reclaim blend. Washing, pile construction, detailed rail motion and
live integration are outside this experiment.

Each mine receives the same fixed 250 t nomination in every arm. Local dispatch
functions receive only their own stock, capacity, calendar, nomination and
static release protocol. They never call the optimizer and cannot read the
other sites' inventories or receiving maintenance. The M16 operator sees its
own demand, its own calendar, and material currently at reception. It shares
reception fairly and makes a known on-spec blend. These policies make no random
mistakes and do not deliberately deliver off-spec product.

Three local protocols are evaluated: earliest release, even pacing over five
hours, and pre-agreed alternating corridor slots. The strongest valid protocol
is selected by on-time terminal product, then known on-spec product, then less
holding exposure. The coordinated arm uses the existing CP-SAT planner with
all site state visible. All arms retain identical physical plant, initial
inventory, demand, grades, calendars and fixed corridor quantities.

Missed arrivals are retained in an explicitly assumed, finite **500 t holding
buffer**. They never disappear or become an invented disposal loss. An overflow
invalidates a plan and calls for backpressure; it is never monetized as lost
product. The actual project's ability to stop/store corridor material is an
engineering input still requiring confirmation. The CP-SAT formulation does
not optimize this extra holding buffer: its plan is a conservative feasible
member of the wider buffer-enabled experiment.

The same independent physical replay audits exact job masses, finite inventory,
machine/routing capacities and calendars, machine exclusivity, route ownership,
delays, grade segregation, hard quality, and whole-experiment mass conservation
for every arm. It reconstructs metrics rather than trusting the optimizer's or
the operator's claimed totals. Holding tonne-hours use end-of-bucket inventory
on the common hourly grid.

For the default assumed receiving outage in hours 2–3, the validated result is:

| Arm | On-time, on-spec final product | Peak held material | Holding exposure |
|---|---:|---:|---:|
| Earliest local release | 600 t | 400 t | 1,400 t·h |
| Even local pacing | 600 t | 200 t | 800 t·h |
| Alternating local slots | 600 t | 200 t | 750 t·h |
| Coordinated plan | 600 t | 0 t | 0 t·h |

The demonstration therefore supports **reduced staging requirements and
holding exposure**, with **zero verified increase in terminal product**.
Removing the receiver outage is a useful control: capable local pacing also
serves all demand without extra holding. This null result is retained.

## 2. Current-scenario source-policy sensitivity

`mph_sim.benchmark.run_benchmark(scenario)` works on a deep copy of the current
Rev-AC model/configuration. FIFO/nearest-source and quality-aware local rules
commit source portfolios for terminal reclaim demand. They use only the
receiving SMS's own demand, inventory, quality, source ages and machine positions.
The same global feasibility scheduler still chooses timings, process flows,
cross-site dispatch and blending for every arm. This is explicitly an optimistic
local source-policy comparison; it does **not** isolate information blindness or
simulate all human-authored jobs in the full plant.

Existing operator-fixed sources remain allowed. All alternatives stay available
when initial local stock cannot cover demand, when complementary blending is
needed, or when a dated grade change makes a single snapshot portfolio unsuitable.
All arms receive the same solver budget and objective weights. Failed solves and
plans with unresolved minute execution timing cannot support a headline benefit
or financial valuation. Feasible incumbents do not establish an optimality claim.

## Financial sensitivity and evidence needed

`value_sensitivity` requires incremental **verified on-spec and on-time terminal
tonnes**. It excludes internal process feeds, corridor moves, material handled
multiple times, unknown-quality tonnes and grade-value placeholders. Annual
periods default to zero. Contribution per tonne must be supplied explicitly and
already exclude marginal production and transport costs. Negative improvements
remain negative. The function provides transparent arithmetic, not a plant ROI.

An investment case needs matched initial SMS snapshots, common dated demand and
assays, the actual operator nomination/communication/override protocol, verified
holding/backpressure rules, execution-feasible plans, and matched disruption
scenarios. Capture operator-authored jobs or timed operator trials as a separate
empirical baseline. Confirm bottleneck recovery and inventory constraints before
turning simulated product differences into contribution. Finance should provide
unit margins, holding/rehandling costs, implementation and recurring costs.

## Python use

```python
from mph_sim.benchmark import BenchmarkOptions, run_benchmark, value_sensitivity
from mph_sim.coordination_benchmark import (
    make_coordination_scenario, run_coordination_benchmark,
)

options = BenchmarkOptions(max_solve_s=3)
experiment = run_coordination_benchmark(options=options)
control = run_coordination_benchmark(
    make_coordination_scenario(receiver_outage=False), options)
current_model = run_benchmark(current_scenario, options)
exportable = experiment.to_dict(include_plans=False)
financial_inputs_only = value_sensitivity(experiment, contribution_per_t=0)
```

Results contain all arms, assumptions, warnings, policy decisions, signed deltas,
physical audit errors, and a hash of the complete input scenario for reproducible
paired comparisons. `tests/test_business_case_benchmark.py` covers information
blindness, independent authorship, common physics, retained material, invalid
execution gating, the no-outage control, best-baseline selection and valuation.
