# Minute execution overlay

The CP-SAT model remains authoritative at the native scenario bucket
resolution. Minute start/end timestamps are a separate post-solve execution
overlay. They do not change allocation, inventory, demand, equipment
exclusivity, route capacity, KPIs, or solver feasibility.

## Optimizer extraction contract

`optimizer.py` must populate `Job.bucket_flows` when it constructs a `Job`.
For every positive solver allocation `X[candidate, bucket]`, preserve one
`JobBucketFlow` row:

```python
JobBucketFlow(
    bucket=bucket + opts.horizon_offset,
    tonnage_t=float(value(X[candidate, bucket])),
    path=list(candidate.path.links) if candidate.path else [],
)
```

The extraction must meet all of these invariants:

- Preserve one row per candidate, route path, and native bucket.
- Do not merge parallel-path rows within a bucket.
- Store the exact absolute bucket after applying the horizon offset.
- Sort deterministically by `(bucket, tuple(path))`.
- `sum(flow.tonnage_t for flow in job.bucket_flows)` must equal
  `job.tonnage_t` within the normal solver extraction tolerance.
- Every flow bucket must lie inside the inclusive
  `job.start_bucket..job.end_bucket` envelope.
- Every path must be the exact candidate route used by the allocation.
- `Job.execution_rate_cap_tph` must preserve the exact integer CP-SAT
  machine ceiling: `round(machine_max × rate_factor × dt_h) / dt_h`. If a
  positive fixed-job rate is lower, apply its equivalently rounded
  `round(fixed_rate × dt_h) / dt_h` ceiling.

Until this contract is populated, the timing status is `BUCKET_ONLY` and the
complete solver envelope is used. No per-bucket tonnes or paths are inferred.

## Refinement and validation

`mph_sim.timing.refine_execution_timing()` creates deterministic,
integer-minute windows inside each native bucket. It first uses the shortest
individually safe duration allowed by machine, link, and shared-group
capacities. A deterministic list scheduler assigns each window the earliest
feasible minute inside its native bucket. Windows using independent resources
may overlap; windows sharing a machine, link, capacity group, or mutually
exclusive route are staggered when required. A one-minute resource sweep then
checks:

- solver-envelope containment and mass/rate/duration identity;
- machine identity, availability, stable/minimum and maximum rates;
- the solve-specific effective machine cap, including derating/fixed rates;
- equipment calendars and same-machine overlap;
- configured site-level exclusive-machine groups;
- link and shared-group instantaneous capacities; and
- mutually exclusive conflict groups.

If integer-minute rounding means the complete constrained set cannot fit,
every exact window in that bucket falls back to the full native bucket at its
average rate. If the fallback still fails, the window becomes `UNVALIDATED`.

Only a complete set of `VALIDATED` windows may drive minute timestamps/rates
in the Gantt and ASP export. `BUCKET_ONLY` and `UNVALIDATED` jobs retain the
authoritative bucket envelope with an explicit timing status.
