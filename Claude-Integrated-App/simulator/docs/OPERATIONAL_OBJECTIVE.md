# Operational planner objective

The planner now includes three explicit operational decision layers in
addition to demand, quality, safety stock and emergency slack.

## Continuous-feed protection

`CONTINUITY_POLICIES` classifies each destination as `continuous_feed`,
`batch_delivery`, `flexible_process_campaign`, or `none`. Only continuous
feeds are scored. The objective prices both gap minutes and new episodes;
results also record maximum episode duration and per-destination detail.

One function, `continuity.continuity_gap_threshold_t`, defines the served
threshold for the CP-SAT `feed_gap` variable, the KPI layer and every export,
so the planner cannot be rewarded for a delivery the reports would later call
an interruption. For a demand-active bucket it is the largest of

- 1 physical tonne (the historic evidence-neutral rule);
- `minimum_tph × Δt`, when an approved minimum stable feed rate exists;
- `minimum_service_fraction ×` the bucket's **own** requested mass,

capped at that same requested mass, so a fully served bucket is never a gap.

**Why the service fraction exists.** One tonne is also the integer activity
floor of the mass model. Under the absolute rule the cheapest way to earn a
perfect continuity KPI was to deliver a single token tonne every hour and
leave the rest of the order unserved — and the S0 week did exactly that: it
reported **zero** continuous-feed gap minutes while hundreds of thousands of
tonnes went unserved. The fraction is applied to the request itself, so it
corrects the incentive without asserting an unapproved t/h figure
(ASSUMED, S3-A33; built-in S0 value 0.5). Deliveries that are positive but
below the threshold are reported separately as
`continuous_feed_token_delivery_buckets` / `_t` rather than hidden inside
either "served" or "complete outage".

`max_gap_min = 0` means no approved hard limit. A positive value is a hard
constraint and may make a plan infeasible even though unmet-tonne slack exists.

Operators can edit all destination policies from the shared **Continuous-feed
policy** panel on both the Plan and Advanced planning pages. Destination rows
are fixed to the scenario register. Changes are validated and committed as one
unit, record their policy/approval basis, and clear any stale plan and
simulation. Maximum-gap enforcement follows the active planning time grid, so
approved limits should be selected with that resolution in mind.

## Equipment-cycle proxy

The old per-space difference proxy has been replaced by exact scheduled event
counts:

- idle to work: one machine start;
- activity group A to B: one group changeover;
- work to idle: one machine stop.

The three relative event penalties are multiplied by the existing `w5_moves`
priority. Scheduled active machine-hours and handled tonnes are also recorded.
State is carried across rolling windows and replans so solve boundaries do not
create artificial events.

The exact event formulation uses one compact machine-state variable per time
slice. This preserves idle, same-group, start, stop and A-to-B changeover cases
without the former per-group transition-variable expansion.

These are wear-risk proxies, not measured component wear or remaining useful
life. Condition, load, vibration, temperature, motor-current and maintenance
history inputs are still required for an actual wear model.

## Campaign, parcel and minimum-run policy

`CAMPAIGN_POLICY` (see `mph_sim/campaigns.py`) is the fragmentation control,
and it acts on the schedule itself rather than on the chart. Without it the
per-group activity constraint degenerates to `tot >= 1 t` for any machine whose
`rate_min_tph` is zero — every stacker in these scenarios — so a campaign could
be padded with meaningless one-tonne hours at no cost, and the minute overlay
drew each one as a one-minute run.

- `min_parcel_t` is a floor on the tonnes an activity group moves in an active
  bucket, capped at what the machine can actually move in that bucket. A
  below-floor bucket remains possible through a `short_parcel` boolean priced
  by `SHORT_PARCEL_PENALTY × w5_moves`, so a genuinely necessary short job is
  produced, priced and reported — never discarded and never made infeasible.
  The audited count is `below_parcel_floor_slices`, recomputed from the
  extracted tonnes.
- `min_run_min` and `prefer_continuous` steer the **minute overlay**, which
  previously compressed every bucket to the shortest duration at the maximum
  feasible rate. A 500 t hourly allocation on a 2,500 t/h reclaimer therefore
  became a 12-minute burst plus 48 blank minutes, hour after hour. The overlay
  now lengthens each window towards a continuous stable-rate run, bounded by
  the machine's `rate_min_tph`, its own solver bucket, occupied minutes and
  reserved positioning. It changes no mass, bucket or route, and any expansion
  that fails physical re-validation is reverted.
- `min_dwell_buckets` is an optional hard campaign length, `1` (disabled) by
  default and skipped wherever a calendar block or the window end would
  conflict.

All values are ASSUMED demonstration defaults (S3-A32), editable from the
**Campaign, parcel and minimum-run policy** panel on the Plan and Advanced
pages and per machine kind / machine in configuration. `enabled: false`
reproduces the previous behaviour exactly.

## Held running: the state that removes token parcels

A machine may now be *held* through a bucket: on, assigned to its activity
group, moving no material. `EQUIPMENT_HOLD_BUCKET_PENALTY` prices it, and
`PlanResult.machine_hold_buckets` records which absolute buckets were held.

This exists because of a measured defect. With no way to say "stay on, move
nothing", the cheapest way to bridge a bucket without paying a stop and a later
start was to command a token parcel. The audited S0 week carried five 1 t
bridge buckets in the middle of otherwise substantial campaigns, and banning
one of them simply moved the trick elsewhere (5 below-floor parcels became 19).
With the held state the same week carries **one** such parcel at the default
search budget and **none** once the exception is priced at 4,000 — while
serving 45,062 t *more* demand than the pre-change model.

Held minutes are reported separately from active minutes
(`executed_hold_minutes` versus `executed_active_minutes`) and are never
presented as production. The minute layer reads the held decision, so a blank
interval the hourly model already paid to keep running is reported as HELD
rather than as a stop and a later start.

## Rate credibility: a token parcel is never dressed up

Each minute window carries `rate_credibility`:

| value | meaning |
|---|---|
| `STABLE_RATE` | the window runs at or above the machine's declared `rate_min_tph` |
| `NO_STABLE_MINIMUM` | the machine declares no minimum rate, so nothing in the model validates how slowly it may run |
| `BELOW_MIN_PARCEL` | the parcel is under the configured `min_parcel_t`; the window keeps its **physical minimum** duration and is *not* lengthened |

The last case matters: stretching 1 t across a 20- or 60-minute window implies
a 3 or 1 t/h operating rate. That is not a credible rate on a machine with no
declared minimum, so such a parcel is left at its physical duration, labelled,
and the surrounding minutes are classified as held or idle on their own
evidence. Judging credibility uses the *policy* figure only — a parcel below a
whole bucket at the machine's own stable minimum is perfectly credible, it
simply runs for part of the bucket.

## Physical-run identity is a property of the window

Run identity is many-to-many with allocation jobs: one run can span several
jobs (a campaign through the rolling day boundary) and **one job can span
several runs** when two of its buckets are separated by a real stop. Identity
is therefore stamped on each `JobExecutionWindow`, and the Gantt and the
execution-window export read it from there. Labelling only the parent job gave
every later bar of such a job the first run's id.

## Two transition counts, both reported

The solver decides hourly activity; execution happens by the minute. Those
answer different questions and their answers differ whenever a bucket run is
broken into separate material bursts:

| breakdown key | question |
|---|---|
| `scheduled_starts` / `_stops` / `scheduled_group_changeovers` | how many planning **buckets** changed state — the priced CP-SAT decision |
| `executed_starts` / `_stops` / `executed_changeovers` | what the placed **minute** schedule actually does |
| `executed_hold_minutes` | machine deliberately kept running between two bursts of the same activity group, within `max_hold_min` |
| `executed_changeover_minutes` | time spent moving between activity groups |
| `executed_idle_minutes` | blank time where the machine genuinely stopped |

`mph_sim/runs.py` derives all of these, plus the physical-run grouping, from
the minute schedule. A blank gap is either an explicit HOLD state or a counted
stop-and-start; it is never presented as continuous production.

### The acceptance bound

The two counts are reconciled by an identity, not by a tolerance. The hourly
model cannot see how LONG a changeover takes: an A to B group change in
adjacent buckets always costs exactly one changeover. The minute schedule knows
the real positioning gap, and when that gap is too long for one changeover it
is a genuine stop followed by a genuine start — one hourly event becoming two
executed events. Therefore::

    executed_transitions - hourly_transitions
        == changeover_reclassified_as_stop_start

`transition_reconciliation_residual` is whatever that identity fails to
explain, and **it must be zero**. It is asserted at zero per machine and in
aggregate on real solves. A non-zero value means the two layers disagree about
the schedule itself, not merely about how to name an interval.

`hourly_*` is recomputed independently from the extracted allocation, so it
also cross-checks the solver's own `scheduled_*` counters. For a single-window
solve the two must be equal. In a weekly rolling solve `scheduled_*` is the sum
of seven per-window counts, each ending without a falling edge for a machine
still running; that difference is reported as
`transition_window_boundary_delta` rather than hidden.

Both layers share one trailing convention: a machine the plan still assigns in
the horizon's final bucket has not stopped *inside* the horizon.

## Current S0 status

Built-in continuous feeds have no approved `minimum_tph` and no hard maximum
gap because those limits are not yet available; they use the ASSUMED 50 %
service share of their own hourly request instead of the token-satisfiable
one-tonne rule. Event and continuity priorities are active but marked ASSUMED and
uncalibrated. Productive work/link energy is implemented and opt-in; see
`ENERGY_KPI_MODEL.md`.

Planner results expose the best objective bound, relative solver gap, branches,
conflicts and deterministic search time. Mixed-resolution results expose their
near-term and tactical component certificates separately and deliberately do
not claim a certificate for the merged job set.
