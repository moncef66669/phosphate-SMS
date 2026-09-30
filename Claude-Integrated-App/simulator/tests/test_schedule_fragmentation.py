# -*- coding: utf-8 -*-
"""Job fragmentation is a scheduling-model property, not a chart property.

Each test names the defect it pins down.  Where the old behaviour is still
reachable — ``CAMPAIGN_POLICY.enabled = False`` and
``minimum_service_fraction = 0.0`` restore it exactly — a companion assertion
demonstrates the defect on that legacy configuration, so these tests fail on
the present behaviour rather than merely describing the new one.
"""
import copy

import pytest

from mph_sim import datasets
from mph_sim.campaigns import (CAMPAIGN_POLICY_KEY, DISABLED_POLICY,
                               SHORT_PARCEL_PENALTY_KEY,
                               campaign_policy_errors,
                               default_campaign_policy_config,
                               resolve_campaign_policy)
from mph_sim.continuity import (ContinuityPolicy, compute_continuity_metrics,
                                continuity_gap_threshold_t,
                                continuity_policy_errors)
from mph_sim.domain import DemandEntry, Job, JobBucketFlow, PlanResult
from mph_sim.kpis import compute_kpis
from mph_sim.optimizer import (SolveOptions, annotate_physical_runs,
                               job_group_key, solve_plan)
from mph_sim.runs import (GAP_CHANGEOVER, GAP_HOLD, GAP_IDLE,
                          executed_transition_metrics,
                          hourly_transition_metrics, micro_parcel_metrics,
                          physical_runs, reconcile_transitions)
from mph_sim.timing import (STABLE_RUN, refine_execution_timing,
                            timing_export_rows,
                            validate_execution_windows,
                            validated_execution_windows)
from mph_sim.validation import validate_scenario
from ui.theme import jobs_to_gantt_df


PATH_A = ["L-RCL-HP-01", "L-H-A", "L-RTE-A-PAP"]
PATH_C = ["L-RCL-HP-05", "L-H-A", "L-RTE-A-PAP"]


def _legacy(sc):
    """Restore the exact pre-policy scheduling behaviour."""
    sc.config[CAMPAIGN_POLICY_KEY] = dict(
        sc.config[CAMPAIGN_POLICY_KEY], enabled=False)
    sc.config["CONTINUITY_POLICIES"] = {
        destination_id: dict(policy, minimum_service_fraction=0.0)
        for destination_id, policy in
        sc.config["CONTINUITY_POLICIES"].items()}
    return sc


def _shrink_horizon(sc, buckets):
    """Shrink the planning horizon, keeping the minute grid consistent."""
    sc.sim.horizon_buckets = int(buckets)
    if sc.sim.horizon_minutes:
        sc.sim.horizon_minutes = int(
            round(buckets * sc.sim.dt_h * 60.0))
    return sc


def _reclaim(sc, job_id, machine="RC01-A", space="HP-01", flows=(),
             path=PATH_A, dest_id="PAP-1"):
    space_obj = sc.space(space)
    rows = [JobBucketFlow(bucket, float(tonnes), list(path))
            for bucket, tonnes in flows]
    total = sum(row.tonnage_t for row in rows)
    buckets = [row.bucket for row in rows]
    return Job(
        id=job_id, machine=machine, kind="reclaim", space=space,
        dest_id=dest_id, grade=space_obj.grade,
        rate_tph=total / max(len(set(buckets)), 1), tonnage_t=total,
        start_bucket=min(buckets), end_bucket=max(buckets),
        path=list(path), bucket_flows=rows,
        start_x=space_obj.x_from, stop_x=space_obj.x_to)


# ---------------------------------------------------------------------------
# 1. repeated one-tonne hourly bursts
# ---------------------------------------------------------------------------
def test_repeated_one_tonne_hourly_bursts_are_not_free_in_the_model():
    """The activity floor collapsed to one tonne when rate_min_tph is zero.

    Every stacker in these scenarios has ``rate_min_tph == 0``, so the group
    activity constraint degenerated to ``tot >= 1 t``.  A campaign could be
    padded with meaningless one-tonne hours at no cost, and the minute overlay
    drew each one as a one-minute bar.
    """
    sc = datasets.end_to_end()
    stacker = next(machine for machine in sc.machines
                   if machine.kind == "stacker" and machine.rate_min_tph == 0)

    policy = resolve_campaign_policy(sc, stacker.id)
    # The machine itself provides no floor, so the policy must provide one.
    assert stacker.rate_min_tph == 0
    assert policy.min_parcel_t > 1.0
    assert policy.parcel_floor_t(
        stacker.rate_min_tph * sc.sim.dt_h) == policy.min_parcel_t

    # ... and with the policy disabled the model is back to the one-tonne floor
    assert resolve_campaign_policy(_legacy(copy.deepcopy(sc)),
                                   stacker.id) is DISABLED_POLICY
    assert DISABLED_POLICY.parcel_floor_t(0.0) == 0.0


def test_one_tonne_hourly_bursts_are_counted_not_hidden():
    sc = datasets.mph_phosphate()
    job = _reclaim(sc, "J-TOKEN",
                   flows=[(0, 1.0), (1, 1.0), (2, 1.0), (3, 1.0)])

    metrics = micro_parcel_metrics(sc, [job])

    assert metrics["allocation_slices"] == 4
    assert metrics["below_parcel_floor_slices"] == 4
    assert metrics["below_parcel_floor_t"] == pytest.approx(4.0)
    assert metrics["smallest_allocation_slice_t"] == pytest.approx(1.0)
    # Never silently discarded: the tonnes are still in the schedule.
    assert sum(flow.tonnage_t for flow in job.bucket_flows) == 4.0


def _slices_below(plan, floor_t):
    """Allocation slices below a fixed tonnage, counted the same way for any
    configuration so a policy-on / policy-off comparison is meaningful."""
    below = 0
    for job in plan.jobs:
        per_bucket = {}
        for flow in job.bucket_flows:
            per_bucket[flow.bucket] = per_bucket.get(
                flow.bucket, 0.0) + flow.tonnage_t
        below += sum(1 for value in (per_bucket.values() or [job.tonnage_t])
                     if value + 1e-9 < floor_t)
    return below


def test_real_s0_plan_validates_minutes_and_accounts_short_exceptions():
    """The corrected objective need not reproduce the old padding defect.

    Validate the real chain and account any remaining short exceptions. The
    required-one-tonne control independently checks that useful small work
    remains feasible; historical micro counts are not acceptance criteria.
    """
    floor_t = resolve_campaign_policy(
        datasets.end_to_end(), "BG-RAW-ST-1").min_parcel_t

    policy_plan = solve_plan(
        datasets.end_to_end(), SolveOptions(max_time_s=6))

    assert policy_plan.status in ("OPTIMAL", "FEASIBLE")
    assert all(window.status == "VALIDATED"
               for job in policy_plan.jobs for window in job.execution_windows)
    assert not [issue for issue in validate_execution_windows(datasets.end_to_end(), policy_plan.jobs)
                if issue.severity == "ERROR"]
    # Every surviving below-floor slice is an accounted, priced exception.
    assert policy_plan.breakdown["short_parcel_buckets"] >=         policy_plan.breakdown["below_parcel_floor_slices"] - 1e-9
    # ... and no tonne was discarded to achieve it.
    assert sum(job.tonnage_t for job in policy_plan.jobs) > 0
    assert policy_plan.breakdown["handled_t"] == pytest.approx(
        sum(job.tonnage_t for job in policy_plan.jobs), rel=1e-6)


# ---------------------------------------------------------------------------
# 2. adjacent-hour gaps: blank time between bucket bursts
# ---------------------------------------------------------------------------
def test_adjacent_hours_of_one_campaign_execute_as_one_continuous_run():
    """Maximal compression turned a steady campaign into hourly bursts.

    1,000 t/h for four hours on a 2,500 t/h reclaimer used to be four
    24-minute bursts separated by 36 blank minutes each.  Nothing represented
    those gaps, and the hourly counters reported a single uninterrupted run.
    """
    sc = datasets.mph_phosphate()
    job = _reclaim(sc, "J-CONT",
                   flows=[(0, 1_000.0), (1, 1_000.0),
                          (2, 1_000.0), (3, 1_000.0)])

    assert refine_execution_timing(sc, [job]).valid
    windows = validated_execution_windows(job)

    assert len(windows) == 4
    assert [window.duration_min for window in windows] == [60, 60, 60, 60]
    assert all(window.basis == STABLE_RUN for window in windows)
    assert all(window.rate_tph == pytest.approx(1_000.0)
               for window in windows)
    # Consecutive windows now touch exactly: no blank minute is left between
    # two hours of the same campaign.
    for previous, current in zip(windows, windows[1:]):
        assert previous.start_minute + previous.duration_min == \
            current.start_minute
    assert validate_execution_windows(sc, [job]) == []

    # One physical run, one displayed bar, no sub-15-minute fragment.
    [run] = physical_runs(sc, [job])
    assert run.active_minutes == 240 and run.hold_minutes == 0
    rows = jobs_to_gantt_df(sc, [job], coalesce_execution_windows=True)
    assert len(rows) == 1
    assert rows.iloc[0].execution_duration_min == 240
    assert rows.iloc[0].rate_tph == pytest.approx(1_000.0)


def test_legacy_timing_fragments_the_same_campaign_into_bursts():
    """Characterises the defect this module fixes."""
    sc = _legacy(datasets.mph_phosphate())
    job = _reclaim(sc, "J-CONT",
                   flows=[(0, 1_000.0), (1, 1_000.0),
                          (2, 1_000.0), (3, 1_000.0)])

    assert refine_execution_timing(sc, [job]).valid
    windows = validated_execution_windows(job)

    assert [window.duration_min for window in windows] == [40, 40, 40, 40]
    # 20 blank minutes per hour that nothing in the old model accounted for.
    blank = sum(
        current.start_minute - (previous.start_minute + previous.duration_min)
        for previous, current in zip(windows, windows[1:]))
    assert blank == 60
    rows = jobs_to_gantt_df(sc, [job], coalesce_execution_windows=True)
    assert len(rows) == 4


def test_a_gap_below_the_hold_allowance_is_an_explicit_held_state():
    """A gap is never presented as continuous production by omission."""
    sc = datasets.mph_phosphate()
    # 300 t/h against a 500 t/h stable minimum cannot run continuously: the
    # honest schedule is 36 minutes on and 24 minutes held per hour.
    job = _reclaim(sc, "J-HOLD", flows=[(0, 300.0), (1, 300.0)])
    assert refine_execution_timing(sc, [job]).valid

    [run] = physical_runs(sc, [job])

    assert [segment.duration_min for segment in run.segments] == [36, 36]
    assert [hold.kind for hold in run.holds] == [GAP_HOLD]
    assert run.holds[0].duration_min == 24
    assert run.hold_minutes == 24
    assert run.elapsed_minutes == 96

    metrics = executed_transition_metrics(
        sc, [job], horizon_start_minute=0, horizon_end_minute=120)
    # One start and no stop inside the horizon: the plan still assigns this
    # machine in the final bucket, the same convention the hourly model uses.
    # Both blank stretches are named HELD, never absorbed into "continuous
    # production".
    assert metrics["executed_starts"] == 1
    assert metrics["executed_stops"] == 0
    assert metrics["executed_hold_minutes"] == 48
    assert metrics["executed_hold_intervals"] == 1
    assert metrics["executed_active_minutes"] == 72
    assert metrics["executed_idle_minutes"] == 0


def test_a_long_gap_between_same_group_bursts_counts_a_real_stop_and_start():
    sc = datasets.mph_phosphate()
    early = _reclaim(sc, "J-EARLY", flows=[(0, 1_400.0)])
    late = _reclaim(sc, "J-LATE", flows=[(5, 1_400.0)])
    for job in (early, late):
        assert refine_execution_timing(sc, [job]).valid
    assert job_group_key(early) == job_group_key(late)

    runs = physical_runs(sc, [early, late])
    metrics = executed_transition_metrics(
        sc, [early, late], horizon_start_minute=0, horizon_end_minute=360)

    # Same activity group, but four idle hours apart: two runs, not one.
    assert len(runs) == 2
    assert metrics["executed_starts"] == 2
    # One stop for the real four-hour outage. The second stop is NOT counted:
    # the plan still assigns this machine in the horizon's final bucket, so its
    # stop lies outside the window, exactly as the hourly model treats it.
    assert metrics["executed_stops"] == 1
    assert metrics["executed_changeovers"] == 0


# ---------------------------------------------------------------------------
# 3. route / activity-group changes
# ---------------------------------------------------------------------------
def test_a_group_change_is_a_changeover_and_never_merged_into_one_run():
    sc = datasets.mph_phosphate()
    first = _reclaim(sc, "J-G1", space="HP-01", path=PATH_A,
                     flows=[(0, 1_000.0)])
    second = _reclaim(sc, "J-G2", space="HP-05", path=PATH_C,
                      flows=[(1, 1_000.0)])
    first.start_x, first.stop_x = 0, 20
    second.start_x, second.stop_x = 280, 300
    assert refine_execution_timing(sc, [first, second]).valid
    assert job_group_key(first) != job_group_key(second)

    runs = physical_runs(sc, [first, second])
    metrics = executed_transition_metrics(
        sc, [first, second], horizon_start_minute=0, horizon_end_minute=120)

    assert [run.group for run in runs] == [
        job_group_key(first), job_group_key(second)]
    assert all(len(run.segments) == 1 for run in runs)
    # The reserved positioning gap is a changeover, not a stop plus a start.
    assert metrics["executed_changeovers"] == 1
    assert metrics["executed_starts"] == 1
    # No stop inside the horizon: the machine is still assigned in its final
    # bucket, so the stop lies outside the window.
    assert metrics["executed_stops"] == 0
    assert metrics["executed_changeover_minutes"] > 0


def test_a_different_route_on_the_same_machine_and_pile_is_its_own_group():
    sc = datasets.mph_phosphate()
    to_pap = _reclaim(sc, "J-R1", dest_id="PAP-1", flows=[(0, 1_000.0)])
    to_dry = _reclaim(sc, "J-R2", dest_id="PAP-2", flows=[(1, 1_000.0)])

    assert job_group_key(to_pap) != job_group_key(to_dry)
    assert refine_execution_timing(sc, [to_pap, to_dry]).valid
    runs = physical_runs(sc, [to_pap, to_dry])
    assert len(runs) == 2


# ---------------------------------------------------------------------------
# 4. midnight rolling-planning-day boundary
# ---------------------------------------------------------------------------
def test_a_campaign_through_midnight_is_one_run_not_two_jobs():
    """The rolling solve cuts a continuous campaign at every civil midnight.

    Two allocation jobs are a correct model artefact; two *physical runs* with
    an invented stop and start at 00:00 are not.
    """
    sc = datasets.end_to_end()
    day_one = _reclaim(sc, "J-D1", flows=[(22, 1_400.0), (23, 1_400.0)])
    day_two = _reclaim(sc, "J-D2", flows=[(24, 1_400.0), (25, 1_400.0)])
    assert refine_execution_timing(sc, [day_one, day_two]).valid
    assert job_group_key(day_one) == job_group_key(day_two)

    runs = physical_runs(sc, [day_one, day_two])
    metrics = executed_transition_metrics(
        sc, [day_one, day_two],
        horizon_start_minute=22 * 60, horizon_end_minute=26 * 60)

    assert len(runs) == 1
    [run] = runs
    assert run.job_ids == ["J-D1", "J-D2"]
    assert run.start_minute == 22 * 60 and run.end_minute == 26 * 60
    assert run.spans_days() == 1
    assert metrics["executed_starts"] == 1
    assert metrics["executed_stops"] == 0    # still running at horizon end
    assert metrics["executed_physical_runs"] == 1

    result = PlanResult("FEASIBLE", jobs=[day_one, day_two])
    annotate_physical_runs(sc, result)
    assert day_one.physical_run_id == day_two.physical_run_id
    assert day_two.continues_previous_planning_day
    assert not day_one.continues_previous_planning_day
    rows = jobs_to_gantt_df(sc, [day_one, day_two])
    assert set(rows.physical_run) == {run.id}
    assert list(rows.physical_run_job_count) == [2, 2, 2, 2]


def test_weekly_rolling_breakdown_does_not_sum_boundary_transitions():
    """Executed counters are recomputed on the merged schedule.

    Summing each civil-day window's own counters would add one stop and one
    start at every midnight, whether or not anything stopped.
    """
    sc = datasets.end_to_end()
    day_one = _reclaim(sc, "J-D1", flows=[(23, 1_400.0)])
    day_two = _reclaim(sc, "J-D2", flows=[(24, 1_400.0)])
    assert refine_execution_timing(sc, [day_one, day_two]).valid
    result = PlanResult("FEASIBLE", jobs=[day_one, day_two])

    from mph_sim.optimizer import _executed_schedule_breakdown
    breakdown = _executed_schedule_breakdown(
        sc, result, result.jobs,
        horizon_start_minute=23 * 60, horizon_end_minute=25 * 60)

    assert breakdown["parent_jobs"] == 2
    assert breakdown["physical_runs"] == 1
    assert breakdown["executed_starts"] == 1
    assert breakdown["physical_runs_spanning_planning_days"] == 1


# ---------------------------------------------------------------------------
# 5. a short job that is genuinely necessary
# ---------------------------------------------------------------------------
def test_a_genuinely_necessary_short_parcel_stays_feasible_and_labelled():
    """Feasibility first: the parcel floor is relaxable, with a price.

    Here the configured floor (5,000 t per bucket) is larger than anything any
    machine can move in one hour, so EVERY delivery bucket is necessarily a
    short parcel.  The plan must still be produced and still serve the demand;
    the short parcels must be counted and priced, never silently dropped and
    never turned into an infeasible model.
    """
    sc = datasets.mph_phosphate()
    sc.sim.max_solve_s = 10
    sc.config[CAMPAIGN_POLICY_KEY] = dict(
        default_campaign_policy_config(),
        default=dict(default_campaign_policy_config()["default"],
                     min_parcel_t=5_000.0),
        # The per-kind blocks would otherwise override the default floor.
        by_machine_kind={})

    plan = solve_plan(sc, SolveOptions(max_time_s=10))

    assert plan.status in ("OPTIMAL", "FEASIBLE")
    assert max(machine.rate_max_tph for machine in sc.machines)         * sc.sim.dt_h < 5_000.0
    # The impossible floor neither blocks the plan nor erases the work.
    assert plan.jobs
    assert plan.breakdown["short_parcel_buckets"] > 0
    assert plan.breakdown["below_parcel_floor_slices"] > 0
    assert plan.breakdown["below_parcel_floor_t"] > 0
    # Mass is conserved: every commanded tonne is still in the job set.
    assert plan.breakdown["handled_t"] == pytest.approx(
        sum(job.tonnage_t for job in plan.jobs), rel=1e-6)


def test_parcel_floor_never_exceeds_what_a_machine_can_move_in_one_bucket():
    """A floor above a machine's bucket capacity must not disable the machine."""
    sc = datasets.mph_phosphate()
    sc.sim.max_solve_s = 8
    sc.config[CAMPAIGN_POLICY_KEY] = dict(
        default_campaign_policy_config(),
        by_machine={"RC01-A": {"min_parcel_t": 10_000_000.0}})
    machine = sc.machine("RC01-A")
    assert machine.rate_max_tph * sc.sim.dt_h < 10_000_000.0

    plan = solve_plan(sc, SolveOptions(max_time_s=8))

    assert plan.status in ("OPTIMAL", "FEASIBLE")
    # The absurd floor neither empties the plan nor removes RC01-A from the
    # model: it is capped at the machine's own bucket capacity, and any bucket
    # below that cap is an explicit, priced exception rather than a ban.
    assert plan.jobs
    assert plan.breakdown["parcel_floor_constrained_groups"] > 0
    if any(job.machine == "RC01-A" for job in plan.jobs):
        assert plan.breakdown["short_parcel_buckets"] > 0


# ---------------------------------------------------------------------------
# 6. continuity: token deliveries must not buy a clean KPI
# ---------------------------------------------------------------------------
def test_one_tonne_does_not_serve_a_thousand_tonne_hour():
    demand_tph, duration_h = 1_000.0, 1.0
    scored = ContinuityPolicy(
        mode="continuous_feed", minimum_service_fraction=0.5)
    legacy = ContinuityPolicy(mode="continuous_feed")

    # The historic rule: one tonne closes the gap for the whole hour.
    assert continuity_gap_threshold_t(legacy, demand_tph, duration_h,
                                      1_000) == 1.0
    assert continuity_gap_threshold_t(scored, demand_tph, duration_h,
                                      1_000) == 500.0
    # A fully served bucket is never a gap, whatever the fraction.
    assert continuity_gap_threshold_t(
        ContinuityPolicy(mode="continuous_feed",
                         minimum_service_fraction=1.0),
        10.5, 1.0, 10) == 10


def test_token_delivery_is_reported_as_a_gap_and_as_a_token():
    policies = {"PAP-1": ContinuityPolicy(
        mode="continuous_feed", minimum_service_fraction=0.5)}
    legacy = {"PAP-1": ContinuityPolicy(mode="continuous_feed")}
    demand = {"PAP-1": [1_000.0] * 4}
    delivered = {"PAP-1": [1.0, 1.0, 1.0, 1_000.0]}

    corrected = compute_continuity_metrics(
        policies, demand, delivered, [1.0] * 4)
    historic = compute_continuity_metrics(
        legacy, demand, delivered, [1.0] * 4)

    # Three hours in which 1 t arrived against a 1,000 t order.
    assert corrected["gap_minutes"] == 180
    assert corrected["episode_count"] == 1
    assert corrected["token_delivery_buckets"] == 3
    assert corrected["token_delivery_t"] == pytest.approx(3.0)
    # The old rule called all four hours perfectly served.
    assert historic["gap_minutes"] == 0
    assert historic["token_delivery_buckets"] == 0


def test_builtin_s0_scores_continuous_feed_on_its_own_requested_mass():
    sc = datasets.end_to_end()
    policies = sc.config["CONTINUITY_POLICIES"]

    scored = {destination_id for destination_id, policy in policies.items()
              if policy["mode"] == "continuous_feed"}
    assert scored
    for destination_id in scored:
        assert policies[destination_id]["minimum_service_fraction"] == \
            datasets.CONTINUOUS_FEED_MIN_SERVICE_FRACTION
        # No unapproved absolute stable feed rate was invented.
        assert policies[destination_id]["minimum_tph"] == 0.0
    assert "ASSUMED" in datasets.CONTINUOUS_FEED_MIN_SERVICE_FRACTION_BASIS


# ---------------------------------------------------------------------------
# 7-8. mass balance and resource conflicts recomputed from minutes
# ---------------------------------------------------------------------------
def _minute_mass_by_space(sc, jobs):
    """Physical mass in/out of each space, summed from the MINUTE schedule."""
    totals = {}
    for job in jobs:
        windows = validated_execution_windows(job)
        assert windows, job.id
        for window in windows:
            mass = window.rate_tph * window.duration_min / 60.0
            source = getattr(job, "source_space", "") or (
                job.space if job.kind in ("reclaim", "dispatch") else "")
            target = getattr(job, "target_space", "") or (
                job.space if job.kind == "stack" else "")
            if source:
                totals[source] = totals.get(source, 0.0) - mass
            if target:
                totals[target] = totals.get(target, 0.0) + mass
    return totals


def test_minute_schedule_conserves_mass_against_the_solver_allocation():
    sc = datasets.mph_phosphate()
    plan = solve_plan(sc, SolveOptions(max_time_s=8))
    exact = [job for job in plan.jobs if validated_execution_windows(job)]
    assert exact

    minutes = _minute_mass_by_space(sc, exact)
    buckets = {}
    for job in exact:
        for flow in job.bucket_flows:
            source = getattr(job, "source_space", "") or (
                job.space if job.kind in ("reclaim", "dispatch") else "")
            target = getattr(job, "target_space", "") or (
                job.space if job.kind == "stack" else "")
            if source:
                buckets[source] = buckets.get(source, 0.0) - flow.tonnage_t
            if target:
                buckets[target] = buckets.get(target, 0.0) + flow.tonnage_t

    assert set(minutes) == set(buckets)
    for space_id, mass in buckets.items():
        assert minutes[space_id] == pytest.approx(mass, abs=0.5), space_id


def test_minute_schedule_has_no_machine_or_resource_conflicts():
    sc = datasets.mph_phosphate()
    plan = solve_plan(sc, SolveOptions(max_time_s=8))

    issues = validate_execution_windows(sc, plan.jobs)

    assert [issue for issue in issues if issue.severity == "ERROR"] == []
    # Independent re-derivation of machine exclusivity from minute intervals.
    occupied = {}
    for job in plan.jobs:
        for window in validated_execution_windows(job):
            for minute in range(window.start_minute,
                                window.start_minute + window.duration_min):
                key = (job.machine, minute)
                assert key not in occupied, (key, job.id, occupied.get(key))
                occupied[key] = job.id


def test_stable_run_expansion_never_breaks_physical_validation():
    sc = datasets.end_to_end()
    plan = solve_plan(sc, SolveOptions(max_time_s=6))

    assert plan.breakdown["minute_timing_errors"] == 0
    assert [issue for issue in validate_execution_windows(sc, plan.jobs)
            if issue.severity == "ERROR"] == []
    for job in plan.jobs:
        for window in validated_execution_windows(job):
            machine = sc.machine(job.machine)
            assert window.rate_tph <= machine.rate_max_tph + 1e-6
            if machine.rate_min_tph > 0:
                assert window.rate_tph >= machine.rate_min_tph - 1e-6


# ---------------------------------------------------------------------------
# 9. FEASIBLE is not a service statement
# ---------------------------------------------------------------------------
def test_feasible_status_is_never_presented_as_demand_met():
    sc = datasets.mph_phosphate()
    _shrink_horizon(sc, 2)
    sc.sim.max_solve_s = 6
    sc.belt_profile = []
    # An order nothing can serve: every machine is unavailable.
    sc.demand = [DemandEntry(destination, bucket, 0.0)
                 for destination in ("PAP-1", "PAP-2", "DRY-FEED")
                 for bucket in range(2)]
    sc.demand[0] = DemandEntry("PAP-1", 0, 1_000.0)
    for machine in sc.machines:
        machine.available = False

    plan = solve_plan(sc, SolveOptions(max_time_s=6))
    kpis = compute_kpis(sc, plan)

    assert plan.status in ("OPTIMAL", "FEASIBLE")
    assert kpis["unmet_t"] == pytest.approx(1_000.0)
    assert kpis["demand_fully_served"] is False
    assert "NOT served" in kpis["plan_status_statement"]
    assert plan.status in kpis["plan_status_statement"]


def test_fully_served_plan_says_so_explicitly():
    sc = datasets.mph_phosphate()
    _shrink_horizon(sc, 2)
    sc.sim.max_solve_s = 6
    sc.belt_profile = []
    sc.demand = [DemandEntry(destination, bucket, 0.0)
                 for destination in ("PAP-1", "PAP-2", "DRY-FEED")
                 for bucket in range(2)]

    plan = solve_plan(sc, SolveOptions(max_time_s=6))
    kpis = compute_kpis(sc, plan)

    assert kpis["demand_fully_served"] is True
    assert "all requested demand served" in kpis["plan_status_statement"]


# ---------------------------------------------------------------------------
# policy plumbing: adjustable, validated, and labelled ASSUMED
# ---------------------------------------------------------------------------
def test_campaign_policy_is_validated_and_resolves_default_kind_machine():
    sc = datasets.end_to_end()
    assert validate_scenario(sc) == [] or all(
        CAMPAIGN_POLICY_KEY not in issue.message
        for issue in validate_scenario(sc))

    sc.config[CAMPAIGN_POLICY_KEY] = {
        "enabled": True,
        "default": {"min_parcel_t": 100.0, "min_run_min": 5},
        "by_machine_kind": {"stacker": {"min_parcel_t": 400.0}},
        "by_machine": {"ST01-A": {"min_run_min": 45}},
    }
    assert campaign_policy_errors(
        sc.config[CAMPAIGN_POLICY_KEY],
        (machine.id for machine in sc.machines)) == []

    reclaimer = resolve_campaign_policy(sc, "RC01-A")
    stacker = resolve_campaign_policy(sc, "ST01-B")
    specific = resolve_campaign_policy(sc, "ST01-A")
    assert (reclaimer.min_parcel_t, reclaimer.min_run_min) == (100.0, 5)
    assert (stacker.min_parcel_t, stacker.min_run_min) == (400.0, 5)
    assert (specific.min_parcel_t, specific.min_run_min) == (400.0, 45)


def test_campaign_policy_rejects_nonsense_and_unknown_machines():
    errors = campaign_policy_errors({
        "enabled": "yes",
        "default": {"min_parcel_t": -1, "min_run_min": 2.5,
                    "min_dwell_buckets": 0, "prefer_continuous": 1},
        "by_machine_kind": {"teleporter": {"min_parcel_t": 1.0}},
        "by_machine": {"NOPE": {"max_hold_min": -5}},
        "sideways": {},
    }, machine_ids=("RC01-A",))

    joined = " | ".join(errors)
    assert "enabled must be true or false" in joined
    assert "min_parcel_t must be a finite nonnegative number" in joined
    assert "min_run_min must be a nonnegative whole number" in joined
    assert "min_dwell_buckets must be at least 1" in joined
    assert "prefer_continuous must be true or false" in joined
    assert "not a recognised machine kind" in joined
    assert "does not match a registered machine" in joined
    assert "not a policy section" in joined


def test_continuity_policy_validation_covers_the_service_fraction():
    errors = continuity_policy_errors({
        "PAP-1": {"mode": "continuous_feed",
                  "minimum_service_fraction": 1.5},
        "PAP-2": {"mode": "continuous_feed", "typo_field": 3},
    }, destination_ids=("PAP-1", "PAP-2"))

    joined = " | ".join(errors)
    assert "minimum_service_fraction must be between 0 and 1" in joined
    assert "unknown fields: typo_field" in joined


def test_defaults_are_labelled_assumed_and_are_adjustable():
    sc = datasets.end_to_end()
    basis = sc.config["CAMPAIGN_POLICY_BASIS"]

    assert basis.startswith("ASSUMED")
    assert "NOT a plant-approved operating limit" in basis
    assert sc.config[SHORT_PARCEL_PENALTY_KEY] >= 0
    # Adjustable: a changed value is honoured without touching the code.
    sc.config[CAMPAIGN_POLICY_KEY]["by_machine"]["RC01-A"] = {
        "min_parcel_t": 75.0}
    assert resolve_campaign_policy(sc, "RC01-A").min_parcel_t == 75.0
    sc.config[CAMPAIGN_POLICY_KEY]["by_machine_kind"] = {}
    sc.config[CAMPAIGN_POLICY_KEY]["default"]["min_parcel_t"] = 120.0
    assert resolve_campaign_policy(sc, "ST01-B").min_parcel_t == 120.0
    sc.config[CAMPAIGN_POLICY_KEY]["enabled"] = False
    assert resolve_campaign_policy(sc, "RC01-A") is DISABLED_POLICY


def test_minimum_dwell_keeps_a_started_campaign_running():
    """The optional campaign-length constraint, off by default."""
    sc = datasets.mph_phosphate()
    assert resolve_campaign_policy(sc, "RC01-A").min_dwell_buckets == 1

    sc.config[CAMPAIGN_POLICY_KEY] = dict(
        default_campaign_policy_config(),
        default=dict(default_campaign_policy_config()["default"],
                     min_dwell_buckets=3))
    _shrink_horizon(sc, 6)
    sc.sim.max_solve_s = 10
    sc.belt_profile = []
    sc.demand = [DemandEntry(destination, bucket, 0.0)
                 for destination in ("PAP-1", "PAP-2", "DRY-FEED")
                 for bucket in range(6)]

    plan = solve_plan(sc, SolveOptions(max_time_s=10))

    assert plan.status in ("OPTIMAL", "FEASIBLE")
    assert plan.breakdown["campaign_dwell_constraints"] > 0


def test_gap_classification_names_every_kind_of_blank_interval():
    sc = datasets.mph_phosphate()
    policy = resolve_campaign_policy(sc, "RC01-A")
    assert policy.max_hold_min > 0

    from mph_sim.runs import Segment, classify_gap
    group_a = ("reclaim", "RC01-A", "HP-01", "PAP-1", "", "", "")
    group_b = ("reclaim", "RC01-A", "HP-05", "PAP-1", "", "", "")

    def segment(start, end, group):
        return Segment("RC01-A", group, "J", start, end, 100.0, 0, 0)

    assert classify_gap(
        sc, segment(0, 10, group_a),
        segment(10 + policy.max_hold_min, 60, group_a)) == GAP_HOLD
    assert classify_gap(
        sc, segment(0, 10, group_a),
        segment(11 + policy.max_hold_min, 60, group_a)) == GAP_IDLE
    assert classify_gap(
        sc, segment(0, 10, group_a),
        segment(30, 60, group_b),
        reserved_positioning_min=20) == GAP_CHANGEOVER
    assert classify_gap(
        sc, segment(0, 10, group_a),
        segment(600, 660, group_b),
        reserved_positioning_min=20) == GAP_IDLE


def test_gantt_exposes_run_identity_without_hiding_any_slice():
    sc = datasets.mph_phosphate()
    job = _reclaim(sc, "J-ID", flows=[(0, 1_000.0), (1, 1_000.0)])
    assert refine_execution_timing(sc, [job]).valid
    result = PlanResult("FEASIBLE", jobs=[job])
    annotate_physical_runs(sc, result)

    raw = jobs_to_gantt_df(sc, [job], coalesce_execution_windows=False)
    merged = jobs_to_gantt_df(sc, [job], coalesce_execution_windows=True)

    assert len(raw) == 2 and len(merged) == 1
    # Both views carry the same physical identity and the same total mass.
    assert set(raw.physical_run) == set(merged.physical_run)
    assert raw.tonnage_t.sum() == pytest.approx(merged.tonnage_t.sum())
    assert raw.tonnage_t.sum() == pytest.approx(job.tonnage_t)
    assert int(merged.iloc[0].allocation_slice_count) == 2
    assert int(merged.iloc[0].execution_window_count) == 2


# ---------------------------------------------------------------------------
# run identity is a property of the EXECUTION WINDOW, not of the parent job
# ---------------------------------------------------------------------------
def test_one_job_split_by_a_real_stop_labels_each_bar_with_its_own_run():
    """Regression: every bar and exported row carries its ACTUAL run id.

    Run identity is many-to-many with allocation jobs.  One run can span
    several jobs (a campaign through the rolling day boundary) and one job can
    span several runs — two of its buckets separated by a real stop.  Stamping
    only the parent job gave every later bar of such a job the FIRST run's id,
    so two physically separate runs were both reported as ``#R001``.
    """
    sc = datasets.mph_phosphate()
    # One allocation job, two buckets four idle hours apart.
    job = _reclaim(sc, "J-SPLIT", flows=[(0, 1_400.0), (5, 1_400.0)])
    assert refine_execution_timing(sc, [job]).valid
    result = PlanResult("FEASIBLE", jobs=[job])

    runs = annotate_physical_runs(sc, result)

    assert [run.id for run in runs] == ["RC01-A#R001", "RC01-A#R002"]
    # The job knows it spans both runs and does not pretend to be one.
    assert job.physical_run_ids == ["RC01-A#R001", "RC01-A#R002"]
    assert job.physical_run_count == 2
    # Each window carries its own run.
    windows = validated_execution_windows(job)
    assert [window.physical_run_id for window in windows] == [
        "RC01-A#R001", "RC01-A#R002"]

    for coalesce in (False, True):
        rows = jobs_to_gantt_df(
            sc, [job], coalesce_execution_windows=coalesce)
        assert len(rows) == 2, coalesce
        assert list(rows.physical_run) == [
            "RC01-A#R001", "RC01-A#R002"], coalesce
        assert set(rows.parent_job) == {"J-SPLIT"}
        assert rows.tonnage_t.sum() == pytest.approx(job.tonnage_t)

    exported = timing_export_rows([job], sc)
    assert [row["physical_run"] for row in exported] == [
        "RC01-A#R001", "RC01-A#R002"]
    # The export also records the whole set, so a consumer can see the job
    # spans two runs without re-deriving it.
    assert all(row["parent_physical_runs"]
               == "RC01-A#R001 RC01-A#R002" for row in exported)


def test_coalescing_never_merges_two_physical_runs():
    """Touching bucket windows in different runs stay separate bars."""
    sc = datasets.mph_phosphate()
    # 1,400 t then 300 t in the NEXT bucket, but on different piles, so the
    # activity group changes and a positioning gap separates them.
    first = _reclaim(sc, "J-A", space="HP-01", path=PATH_A,
                     flows=[(0, 1_000.0)])
    second = _reclaim(sc, "J-B", space="HP-05", path=PATH_C,
                      flows=[(1, 1_000.0)])
    first.start_x, first.stop_x = 0, 20
    second.start_x, second.stop_x = 280, 300
    assert refine_execution_timing(sc, [first, second]).valid
    result = PlanResult("FEASIBLE", jobs=[first, second])
    runs = annotate_physical_runs(sc, result)

    rows = jobs_to_gantt_df(
        sc, [first, second], coalesce_execution_windows=True)

    assert len({run.id for run in runs}) == 2
    assert len(rows) == 2
    assert len(set(rows.physical_run)) == 2


def test_run_identity_survives_the_rolling_day_boundary_per_bar():
    """A run through midnight gives every one of its bars the same id.

    The complement of the split case: here two allocation jobs are one run, so
    all four bars must share one id and the second day's bars must be marked
    as continuing the previous planning day.
    """
    sc = datasets.end_to_end()
    day_one = _reclaim(sc, "J-D1", flows=[(22, 1_400.0), (23, 1_400.0)])
    day_two = _reclaim(sc, "J-D2", flows=[(24, 1_400.0), (25, 1_400.0)])
    assert refine_execution_timing(sc, [day_one, day_two]).valid
    result = PlanResult("FEASIBLE", jobs=[day_one, day_two])

    runs = annotate_physical_runs(sc, result)

    assert len(runs) == 1
    [run] = runs
    rows = jobs_to_gantt_df(
        sc, [day_one, day_two], coalesce_execution_windows=False)
    assert set(rows.physical_run) == {run.id}
    assert list(rows.physical_run_job_count) == [2, 2, 2, 2]
    # Only the bars on the second civil day continue the previous day.
    by_day = dict(zip(rows.start_time, rows.continues_previous_planning_day))
    assert [flag for stamp, flag in sorted(by_day.items())] == [
        False, False, True, True]
    exported = timing_export_rows([day_one, day_two], sc)
    assert {row["physical_run"] for row in exported} == {run.id}


def test_annotation_is_idempotent_and_reflects_a_changed_schedule():
    """Stamping twice must not accumulate, and must follow the schedule."""
    sc = datasets.mph_phosphate()
    job = _reclaim(sc, "J-IDEM", flows=[(0, 1_400.0), (5, 1_400.0)])
    assert refine_execution_timing(sc, [job]).valid
    result = PlanResult("FEASIBLE", jobs=[job])

    annotate_physical_runs(sc, result)
    annotate_physical_runs(sc, result)

    assert job.physical_run_ids == ["RC01-A#R001", "RC01-A#R002"]
    assert job.physical_run_count == 2

    # Close the gap: the same job becomes ONE run and every bar agrees.
    contiguous = _reclaim(sc, "J-IDEM2", flows=[(0, 1_400.0), (1, 1_400.0)])
    assert refine_execution_timing(sc, [contiguous]).valid
    second = PlanResult("FEASIBLE", jobs=[contiguous])
    runs = annotate_physical_runs(sc, second)
    assert len(runs) == 1
    assert contiguous.physical_run_count == 1
    rows = jobs_to_gantt_df(
        sc, [contiguous], coalesce_execution_windows=True)
    assert len(rows) == 1
    assert rows.iloc[0].physical_run == runs[0].id


# ---------------------------------------------------------------------------
# hourly decisions and executed minutes reconcile by an exact identity
# ---------------------------------------------------------------------------
def _reconcile(sc, jobs, *, start_bucket, end_bucket, holds=None,
               initial_groups=None):
    native = max(sc.sim.native_bucket_minutes(), 1)
    hourly = hourly_transition_metrics(
        sc, jobs, horizon_start_bucket=start_bucket,
        horizon_end_bucket=end_bucket, machine_hold_buckets=holds,
        initial_machine_groups=initial_groups)
    executed = executed_transition_metrics(
        sc, jobs, horizon_start_minute=start_bucket * native,
        horizon_end_minute=end_bucket * native,
        machine_hold_buckets=holds, initial_machine_groups=initial_groups)
    return hourly, executed, reconcile_transitions(hourly, executed)


def test_adjacent_hour_gap_inside_one_campaign_reconciles_to_zero():
    """A stable-rate-limited blank inside a campaign is HELD, not a stop.

    300 t/h against RC01-A's 500 t/h floor can only run 36 of every 60 minutes.
    The hourly model sees one uninterrupted two-bucket run; the minute schedule
    sees a 24-minute blank. Those must not disagree about the number of starts.
    """
    sc = datasets.mph_phosphate()
    job = _reclaim(sc, "J-ADJ", flows=[(0, 300.0), (1, 300.0)])
    assert refine_execution_timing(sc, [job]).valid

    hourly, executed, recon = _reconcile(
        sc, [job], start_bucket=0, end_bucket=2)

    assert hourly["hourly_starts"] == 1
    assert hourly["hourly_stops"] == 0
    assert hourly["hourly_changeovers"] == 0
    assert executed["executed_starts"] == 1
    assert executed["executed_stops"] == 0
    assert executed["executed_active_minutes"] == 72
    assert executed["executed_hold_minutes"] == 48
    assert recon["transition_reconciliation_residual"] == 0
    # Held minutes are reported as held, never folded into active time, and
    # together they account for every minute of the two assigned buckets.
    assert executed["executed_active_minutes"] + \
        executed["executed_hold_minutes"] == 120
    assert executed["executed_idle_minutes"] == 0


def test_an_explicitly_held_bucket_costs_no_start_and_no_stop():
    """The plan pays to hold; the minute layer must agree it never stopped."""
    sc = datasets.mph_phosphate()
    early = _reclaim(sc, "J-H1", flows=[(0, 1_400.0)])
    late = _reclaim(sc, "J-H2", flows=[(2, 1_400.0)])
    for job in (early, late):
        assert refine_execution_timing(sc, [job]).valid
    assert job_group_key(early) == job_group_key(late)

    without = _reconcile(sc, [early, late], start_bucket=0, end_bucket=3)
    with_hold = _reconcile(sc, [early, late], start_bucket=0, end_bucket=3,
                           holds={"RC01-A": [1]})

    # Without the hold: bucket 1 is empty, so both layers see a stop/start.
    assert without[0]["hourly_starts"] == 2
    assert without[1]["executed_starts"] == 2
    assert without[2]["transition_reconciliation_residual"] == 0
    # With the hold: one run, one start, and the blank named as held.
    assert with_hold[0]["hourly_starts"] == 1
    assert with_hold[0]["hourly_hold_buckets"] == 1
    assert with_hold[1]["executed_starts"] == 1
    assert with_hold[1]["executed_hold_minutes"] > 0
    assert with_hold[2]["transition_reconciliation_residual"] == 0
    assert len(physical_runs(sc, [early, late],
                            machine_hold_buckets={"RC01-A": [1]})) == 1


def test_positioning_changeover_is_reconciled_not_double_counted():
    sc = datasets.mph_phosphate()
    first = _reclaim(sc, "J-P1", space="HP-01", path=PATH_A,
                     flows=[(0, 1_000.0)])
    second = _reclaim(sc, "J-P2", space="HP-05", path=PATH_C,
                      flows=[(1, 1_000.0)])
    first.start_x, first.stop_x = 0, 20
    second.start_x, second.stop_x = 280, 300
    assert refine_execution_timing(sc, [first, second]).valid

    hourly, executed, recon = _reconcile(
        sc, [first, second], start_bucket=0, end_bucket=2)

    # One A->B group change in adjacent buckets: exactly one hourly changeover.
    assert hourly["hourly_changeovers"] == 1
    assert hourly["hourly_starts"] == 1
    # The reserved positioning gap keeps it a changeover in the minute view.
    assert executed["executed_changeovers"] == 1
    assert executed["executed_changeover_reclassified_as_stop_start"] == 0
    assert recon["transition_reconciliation_residual"] == 0


def test_a_changeover_too_long_for_positioning_is_reclassified_and_explained():
    """The hourly model cannot see gap LENGTH; the identity absorbs that."""
    sc = datasets.mph_phosphate()
    first = _reclaim(sc, "J-L1", space="HP-01", path=PATH_A,
                     flows=[(0, 300.0)])
    second = _reclaim(sc, "J-L2", space="HP-05", path=PATH_C,
                      flows=[(1, 300.0)])
    first.start_x, first.stop_x = 0, 20
    second.start_x, second.stop_x = 280, 300
    assert refine_execution_timing(sc, [first, second]).valid

    hourly, executed, recon = _reconcile(
        sc, [first, second], start_bucket=0, end_bucket=2)

    assert hourly["hourly_changeovers"] == 1
    assert executed["executed_changeovers"] + \
        executed["executed_changeover_reclassified_as_stop_start"] == 1
    # Whichever way the minute schedule names it, the identity closes.
    assert recon["transition_reconciliation_residual"] == 0
    assert recon["transition_executed_total"] - \
        recon["transition_hourly_total"] == \
        recon["transition_reclassified_changeovers"]


def test_day_boundary_adds_no_transition_to_either_measure():
    sc = datasets.end_to_end()
    day_one = _reclaim(sc, "J-B1", flows=[(23, 1_400.0)])
    day_two = _reclaim(sc, "J-B2", flows=[(24, 1_400.0)])
    assert refine_execution_timing(sc, [day_one, day_two]).valid

    hourly, executed, recon = _reconcile(
        sc, [day_one, day_two], start_bucket=23, end_bucket=25)

    assert hourly["hourly_starts"] == 1 and hourly["hourly_stops"] == 0
    assert executed["executed_starts"] == 1 and executed["executed_stops"] == 0
    assert executed["executed_changeovers"] == 0
    assert recon["transition_reconciliation_residual"] == 0


def test_real_solves_close_the_reconciliation_identity_exactly():
    """The acceptance bound, on real plans rather than constructed ones."""
    for scenario in (datasets.mph_phosphate(), datasets.end_to_end()):
        plan = solve_plan(scenario, SolveOptions(max_time_s=6))
        breakdown = plan.breakdown
        assert breakdown["transition_reconciliation_residual"] == 0, (
            scenario.name, breakdown["transition_reconciliation_residual"])


def test_uniform_solve_hourly_recomputation_matches_the_solver_counters():
    """An independent check on the numbers the objective actually priced.

    Only meaningful for a single-window solve. In a weekly rolling solve
    ``scheduled_*`` is the SUM of seven per-window counts, each of which ends
    without a falling edge for a machine still running, while the recomputation
    walks the merged week; that difference is reported separately as
    ``transition_window_boundary_delta``, not hidden.
    """
    scenario = datasets.mph_phosphate()
    plan = solve_plan(scenario, SolveOptions(max_time_s=8))
    breakdown = plan.breakdown

    assert plan.solver == "cp-sat", plan.solver      # one window
    assert breakdown["hourly_starts"] == breakdown["scheduled_starts"]
    assert breakdown["hourly_stops"] == breakdown["scheduled_stops"]
    assert breakdown["hourly_changeovers"] == \
        breakdown["scheduled_group_changeovers"]
    assert breakdown["hourly_hold_buckets"] == \
        breakdown["scheduled_hold_buckets"]
    assert breakdown["transition_window_boundary_delta"] == 0
    assert breakdown["transition_reconciliation_residual"] == 0


def test_weekly_rolling_reports_its_window_boundary_delta_explicitly():
    """The per-window sum and the merged-week walk may differ; say by how much."""
    scenario = datasets.end_to_end()
    plan = solve_plan(scenario, SolveOptions(max_time_s=6))
    breakdown = plan.breakdown

    assert plan.solver.startswith("cp-sat-weekly-rolling")
    scheduled_total = (breakdown["scheduled_starts"]
                       + breakdown["scheduled_stops"]
                       + breakdown["scheduled_group_changeovers"])
    assert breakdown["transition_window_boundary_delta"] == \
        scheduled_total - breakdown["hourly_transition_count"]
    # The executed-vs-hourly identity still has to close exactly.
    assert breakdown["transition_reconciliation_residual"] == 0


# ---------------------------------------------------------------------------
# a below-floor parcel is never dressed up as a normal run
# ---------------------------------------------------------------------------
def _token_stack(sc, tonnes, job_id="J-TOKEN-1T"):
    space = sc.space("BG-RAW-1")
    path = ["L-BG-SCREEN-STK-IN-L1", "L-BG-SCREEN-STK-OUT-L1"]
    return Job(
        id=job_id, machine="BG-RAW-ST-1", kind="stack", space="BG-RAW-1",
        grade=space.grade, rate_tph=float(tonnes), tonnage_t=float(tonnes),
        start_bucket=0, end_bucket=0, path=list(path),
        bucket_flows=[JobBucketFlow(0, float(tonnes), list(path))],
        start_x=space.x_from, stop_x=space.x_to)


def test_a_below_floor_parcel_keeps_its_physical_duration_and_is_labelled():
    """Widening 1 t to 20 or 60 minutes invents a 3 or 1 t/h operating rate.

    The machine here declares no minimum stable rate, so nothing in the model
    validates how slowly it may run. The honest window is the physical minimum,
    explicitly labelled, with the surrounding minutes classified on their own
    evidence.
    """
    sc = datasets.end_to_end()
    assert sc.machine("BG-RAW-ST-1").rate_min_tph == 0
    token = _token_stack(sc, 1.0)

    assert refine_execution_timing(sc, [token]).valid
    [window] = validated_execution_windows(token)

    assert window.rate_credibility == "BELOW_MIN_PARCEL"
    # NOT widened to the 20-minute minimum run: 1 t over 20 min is 3 t/h.
    assert window.duration_min == 1
    assert window.basis != STABLE_RUN
    row = jobs_to_gantt_df(
        sc, [token], coalesce_execution_windows=True).iloc[0]
    assert row.rate_credibility == "BELOW_MIN_PARCEL"
    assert row.execution_duration_min == 1
    # The tonne is still there; nothing was discarded to tidy the chart.
    assert row.tonnage_t == pytest.approx(1.0)


def test_a_credible_parcel_is_labelled_by_its_machine_rate_floor():
    sc = datasets.mph_phosphate()
    on_floor = _reclaim(sc, "J-FLOOR", flows=[(0, 1_000.0)])
    assert refine_execution_timing(sc, [on_floor]).valid
    [window] = validated_execution_windows(on_floor)
    assert sc.machine("RC01-A").rate_min_tph > 0
    assert window.rate_credibility == "STABLE_RATE"
    assert window.rate_tph >= sc.machine("RC01-A").rate_min_tph

    stacker_sc = datasets.end_to_end()
    big = _token_stack(stacker_sc, 900.0, job_id="J-NOFLOOR")
    assert refine_execution_timing(stacker_sc, [big]).valid
    [window] = validated_execution_windows(big)
    # Above the parcel floor, but the machine declares no minimum rate, so the
    # label says so rather than implying a validated operating rate.
    assert window.rate_credibility == "NO_STABLE_MINIMUM"


def test_the_hold_state_removes_the_need_for_token_bridge_parcels():
    """The model can now express staying on without moving material."""
    sc = datasets.mph_phosphate()
    # Holding must be cheaper than a stop plus a later start, or it is never
    # chosen; and dearer than nothing, or the planner holds everywhere free.
    assert 0 < sc.config["EQUIPMENT_HOLD_BUCKET_PENALTY"] < (
        sc.config["EQUIPMENT_STOP_PENALTY"]
        + sc.config["EQUIPMENT_START_PENALTY"])

    plan = solve_plan(sc, SolveOptions(max_time_s=8))

    assert plan.status in ("OPTIMAL", "FEASIBLE")
    # The held decision is recorded per machine and per absolute bucket, so the
    # minute layer can name the interval instead of guessing.
    for machine_id, buckets in plan.machine_hold_buckets.items():
        assert buckets == sorted(set(buckets))
        material = {
            int(flow.bucket) for job in plan.jobs
            if job.machine == machine_id for flow in job.bucket_flows}
        # A held bucket is never also a material bucket.
        assert not material.intersection(buckets)
    assert plan.breakdown["scheduled_hold_buckets"] == float(
        sum(len(v) for v in plan.machine_hold_buckets.values()))
