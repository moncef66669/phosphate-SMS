"""Deterministic mid-week contingency and recovery contracts.

The production three-site weekly model is intentionally large.  These tests
exercise the same 168 hourly planning buckets on a compact two-route network,
so rolling-response semantics are proved without making routine verification
depend on a multi-minute S3 solve.
"""
from __future__ import annotations

import copy

import pytest

from mph_sim.domain import (
    CalendarEntry,
    DemandEntry,
    Destination,
    Grade,
    Link,
    Machine,
    Node,
    Scenario,
    SimEvent,
    SimParams,
    Site,
    StockSpace,
)
from mph_sim.optimizer import SolveOptions, solve_plan
from mph_sim.simulator import Simulator


HOURS = 7 * 24
EVENT_BUCKET = 3 * 24 + 12  # Thursday 12:00 in the seven-day plan
EVENT_MINUTE = EVENT_BUCKET * 60 + 20
EVENT_END_MINUTE = EVENT_MINUTE + 120
REPLAN_BUCKET = EVENT_BUCKET + 1
RECOVERY_BUCKET = EVENT_BUCKET + 3


def _weekly_parallel_route_fixture(*, backup_mass_t: float = 200.0) -> Scenario:
    scenario = Scenario(
        "compact weekly contingency fixture",
        sites=[Site("TEST", "Test", "hub")],
        grades=[Grade("G", "Grade")],
        sim=SimParams(
            horizon_buckets=HOURS,
            dt_h=1.0,
            horizon_minutes=HOURS * 60,
            max_solve_s=0.5,
            seed=7,
        ),
        config={"FLOW_TONNE_PENALTY": 1},
    )
    scenario.spaces = [
        StockSpace(
            "PRIMARY-PILE", "TEST", "yard", "open",
            30_000.0, 20_000.0, "G"),
        StockSpace(
            "BACKUP-PILE", "TEST", "yard", "open",
            max(backup_mass_t, 1.0), backup_mass_t, "G"),
    ]
    scenario.machines = [
        Machine(
            "PRIMARY-RC", "TEST", "reclaimer", 0.0, 100.0,
            reachable_spaces=["PRIMARY-PILE"], setup_min=0.0),
        Machine(
            "BACKUP-RC", "TEST", "reclaimer", 0.0, 100.0,
            reachable_spaces=["BACKUP-PILE"], setup_min=0.0),
    ]
    scenario.nodes = [
        Node("SPACE:PRIMARY-PILE", "TEST", "space", "Primary pile"),
        Node("SPACE:BACKUP-PILE", "TEST", "space", "Backup pile"),
        Node("DEST:PLANT", "TEST", "dest", "Plant"),
    ]
    scenario.links = [
        Link(
            "PRIMARY-LINK", "SPACE:PRIMARY-PILE", "DEST:PLANT", 100.0),
        Link(
            "BACKUP-LINK", "SPACE:BACKUP-PILE", "DEST:PLANT", 100.0),
    ]
    scenario.destinations = [Destination(
        "PLANT", "TEST", "DEST:PLANT",
        demand_default_tph=100.0, requested_grade="G")]
    scenario.demand = [
        DemandEntry("PLANT", bucket, 100.0, requested_grade="G")
        for bucket in range(HOURS)
    ]
    return scenario


def _solve(scenario: Scenario):
    result = solve_plan(scenario, SolveOptions(max_time_s=0.5))
    assert result.status in {"OPTIMAL", "FEASIBLE"}
    return result


def _flow(plan, bucket: int, *, machine: str = "") -> float:
    return sum(
        flow.tonnage_t
        for job in plan.jobs
        if not machine or job.machine == machine
        for flow in job.bucket_flows
        if flow.bucket == bucket
    )


def _assert_elapsed_residual_is_carried(simulator: Simulator) -> None:
    """The interrupted part of Thursday 12:00 remains an explicit order."""
    expected_residual = 100.0 * 40.0 / 60.0
    snapshot = simulator.snapshot_scenario()
    assert snapshot.config["REPLAN_DEMAND_BACKLOG_T"]["PLANT"] == \
        pytest.approx(expected_residual)
    assert snapshot.config["REPLAN_DEMAND_BACKLOG_BY_GRADE_T"]["PLANT"] == \
        {"G": pytest.approx(expected_residual)}
    assert snapshot.demand_tph("PLANT", REPLAN_BUCKET) == pytest.approx(
        100.0 + expected_residual)
    assert simulator.plan.breakdown["replan_carried_demand_t"] == \
        pytest.approx(expected_residual)

    # CP-SAT balances integer physical tonnes, hence the half-tonne tolerance
    # around the exact 66.667 t minute residual.
    requested = sum(
        snapshot.demand_tph("PLANT", bucket)
        for bucket in range(REPLAN_BUCKET, HOURS))
    balanced = sum(simulator.plan.delivered["PLANT"]) + \
        sum(simulator.plan.unmet["PLANT"])
    assert balanced == pytest.approx(requested, abs=0.51)


def _run_midweek(event: SimEvent) -> tuple[Simulator, object, str]:
    scenario = _weekly_parallel_route_fixture()
    initial = _solve(scenario)
    initial_job = next(
        job for job in initial.jobs
        if job.machine == "PRIMARY-RC"
        and any(flow.bucket == EVENT_BUCKET for flow in job.bucket_flows)
    )
    simulator = Simulator(
        scenario, initial, events=[event], auto_replan=True)
    simulator.advance_minutes((EVENT_BUCKET + 1) * 60)
    assert simulator.replans == 1
    assert simulator.plan.status in {"OPTIMAL", "FEASIBLE"}
    assert simulator.plan.horizon_offset == REPLAN_BUCKET
    return simulator, initial, initial_job.id


@pytest.mark.parametrize(
    ("event_type", "target", "calendar_scope"),
    [
        ("machine_failure", "PRIMARY-RC", "PRIMARY-RC"),
        ("conveyor_trip", "PRIMARY-LINK", "LINK:PRIMARY-LINK"),
    ],
)
def test_midweek_outage_uses_backup_then_recovers_original_route(
        event_type: str, target: str, calendar_scope: str):
    simulator, _initial, interrupted_job_id = _run_midweek(SimEvent(
        "MIDWEEK-OUTAGE", event_type, EVENT_BUCKET, target,
        minute=EVENT_MINUTE, duration_minutes=120))

    # The exact 20-minute prefix survives.  The remaining instruction is
    # closed, and the two conservatively intersected hourly buckets move to
    # the backup route.  The elapsed hour's residual remains explicit unmet
    # demand because this fixture has no spare catch-up capacity.
    interrupted = simulator.interrupted_jobs[interrupted_job_id]
    assert interrupted["minute"] == EVENT_MINUTE
    assert interrupted["completed_t"] == pytest.approx(
        EVENT_BUCKET * 100.0 + 100.0 / 3.0)
    assert _flow(simulator.plan, REPLAN_BUCKET, machine="PRIMARY-RC") == 0.0
    assert _flow(simulator.plan, REPLAN_BUCKET + 1,
                 machine="PRIMARY-RC") == 0.0
    assert _flow(simulator.plan, REPLAN_BUCKET,
                 machine="BACKUP-RC") == 100.0
    assert _flow(simulator.plan, REPLAN_BUCKET + 1,
                 machine="BACKUP-RC") == 100.0
    _assert_elapsed_residual_is_carried(simulator)
    assert sum(simulator.plan.unmet["PLANT"]) == pytest.approx(67.0)

    # Recovery is part of the same bounded replan: the original resource is
    # legal again in the first untouched hour, rather than staying disabled
    # for the rest of the week.
    assert _flow(simulator.plan, RECOVERY_BUCKET,
                 machine="PRIMARY-RC") == 100.0
    snapshot = simulator.snapshot_scenario()
    change = next(row for row in snapshot.calendars
                  if row.scope == calendar_scope)
    assert (change.start_minute, change.end_minute) == (
        (EVENT_BUCKET + 1) * 60, EVENT_END_MINUTE)


def test_midweek_derate_is_timed_and_replan_restores_base_rate():
    simulator, _initial, interrupted_job_id = _run_midweek(SimEvent(
        "MIDWEEK-DERATE", "reduced_rate", EVENT_BUCKET, "PRIMARY-RC",
        magnitude=0.5, minute=EVENT_MINUTE, duration_minutes=120))

    assert simulator.interrupted_jobs[interrupted_job_id]["reason"] == \
        "reduced_rate:PRIMARY-RC"
    _assert_elapsed_residual_is_carried(simulator)
    assert _flow(simulator.plan, REPLAN_BUCKET) == pytest.approx(150.0)
    assert _flow(simulator.plan, REPLAN_BUCKET + 1) == pytest.approx(100.0)
    for bucket in (REPLAN_BUCKET, REPLAN_BUCKET + 1):
        assert _flow(simulator.plan, bucket,
                     machine="PRIMARY-RC") == pytest.approx(50.0)
    assert _flow(simulator.plan, REPLAN_BUCKET,
                 machine="BACKUP-RC") == pytest.approx(100.0)
    assert _flow(simulator.plan, REPLAN_BUCKET + 1,
                 machine="BACKUP-RC") == pytest.approx(50.0)
    assert _flow(simulator.plan, RECOVERY_BUCKET,
                 machine="PRIMARY-RC") > 50.0
    assert sum(simulator.plan.unmet["PLANT"]) == pytest.approx(17.0)

    change = next(row for row in simulator.snapshot_scenario().calendars
                  if row.scope == "PRIMARY-RC"
                  and row.action == "RATE_CAP")
    assert change.rate_tph == pytest.approx(50.0)
    assert change.end_minute == EVENT_END_MINUTE


def test_midweek_plant_stop_retains_order_as_unmet_then_recovers():
    simulator, initial, interrupted_job_id = _run_midweek(SimEvent(
        "MIDWEEK-PLANT", "downstream_interruption", EVENT_BUCKET, "PLANT",
        minute=EVENT_MINUTE, duration_minutes=120))

    assert simulator.interrupted_jobs[interrupted_job_id]["reason"] == \
        "downstream_interruption:PLANT"
    assert simulator.demand_override == {}
    assert [simulator.sc.demand_tph("PLANT", bucket)
            for bucket in (REPLAN_BUCKET, REPLAN_BUCKET + 1)] == \
        [100.0, 100.0]
    _assert_elapsed_residual_is_carried(simulator)
    assert simulator.plan.unmet["PLANT"][:3] == pytest.approx(
        [167.0, 100.0, 0.0])
    assert sum(simulator.plan.unmet["PLANT"]) == pytest.approx(267.0)
    assert _flow(simulator.plan, REPLAN_BUCKET) == 0.0
    assert _flow(simulator.plan, REPLAN_BUCKET + 1) == 0.0
    assert _flow(simulator.plan, RECOVERY_BUCKET) == pytest.approx(100.0)
    assert simulator.downstream_ok(initial.jobs[0], EVENT_END_MINUTE - 1) \
        is False
    assert simulator.downstream_ok(initial.jobs[0], EVENT_END_MINUTE) is True


def test_elapsed_backlog_does_not_erase_a_new_grade_campaign():
    scenario = _weekly_parallel_route_fixture()
    scenario.grades.append(Grade("H", "Next campaign grade"))
    for entry in scenario.demand:
        if entry.bucket >= REPLAN_BUCKET:
            entry.requested_grade = "H"
    initial = _solve(scenario)
    simulator = Simulator(
        scenario, initial,
        events=[SimEvent(
            "GRADE-BOUNDARY-OUTAGE", "machine_failure", EVENT_BUCKET,
            "PRIMARY-RC", minute=EVENT_MINUTE, duration_minutes=120)],
        auto_replan=False)

    simulator.advance_minutes((EVENT_BUCKET + 1) * 60)
    snapshot = simulator.snapshot_scenario()

    # G does not occur again in the untouched weekly profile.  Its 66.667 t
    # residual gets a dedicated row; the displaced 100 t H obligation is
    # merged into the next H row rather than being relabelled as G or dropped.
    assert snapshot.demand_grade("PLANT", REPLAN_BUCKET) == "G"
    assert snapshot.demand_tph("PLANT", REPLAN_BUCKET) == pytest.approx(
        100.0 * 40.0 / 60.0)
    assert snapshot.demand_grade("PLANT", REPLAN_BUCKET + 1) == "H"
    assert snapshot.demand_tph("PLANT", REPLAN_BUCKET + 1) == \
        pytest.approx(200.0)
    assert sum(snapshot.demand_tph("PLANT", bucket)
               for bucket in range(REPLAN_BUCKET, HOURS)) == pytest.approx(
        (HOURS - REPLAN_BUCKET) * 100.0 + 100.0 * 40.0 / 60.0)
    assert snapshot.config["REPLAN_SCHEDULED_BACKLOG_BY_GRADE_T"] == {
        "PLANT": {"G": pytest.approx(100.0 * 40.0 / 60.0)}}
    assert snapshot.config["REPLAN_UNSCHEDULED_BACKLOG_BY_GRADE_T"] == {}


@pytest.mark.parametrize(
    ("event_type", "target", "keeps_parallel_lock"),
    [
        ("machine_failure", "PRIMARY-RC", True),
        ("conveyor_trip", "PRIMARY-LINK", True),
        ("downstream_interruption", "PLANT", False),
    ],
)
def test_future_locks_overlapping_event_are_cancelled_before_replan(
        event_type: str, target: str, keeps_parallel_lock: bool):
    scenario = _weekly_parallel_route_fixture(backup_mass_t=1_000.0)
    initial = _solve(scenario)
    primary = next(job for job in initial.jobs
                   if job.machine == "PRIMARY-RC")
    future = copy.deepcopy(primary)
    future.id = "FUTURE-AFFECTED-LOCK"
    future.start_bucket = REPLAN_BUCKET
    future.end_bucket = REPLAN_BUCKET
    future.start_minute = None
    future.end_minute = None
    future.tonnage_t = 100.0
    future.bucket_flows = [copy.deepcopy(next(
        flow for flow in primary.bucket_flows
        if flow.bucket == REPLAN_BUCKET))]
    future.bucket_flows[0].tonnage_t = 100.0
    future.execution_windows = []
    future.origin = "HUMAN"
    future.locked = True
    future.status = "PLANNED"
    initial.jobs.append(future)

    if keeps_parallel_lock:
        unaffected = copy.deepcopy(future)
        unaffected.id = "FUTURE-UNAFFECTED-LOCK"
        unaffected.machine = "BACKUP-RC"
        unaffected.space = "BACKUP-PILE"
        unaffected.path = ["BACKUP-LINK"]
        unaffected.bucket_flows[0].path = ["BACKUP-LINK"]
        initial.jobs.append(unaffected)

    simulator = Simulator(
        scenario, initial,
        events=[SimEvent(
            "LOCK-WINDOW-OUTAGE", event_type, EVENT_BUCKET, target,
            minute=EVENT_MINUTE, duration_minutes=120)],
        auto_replan=True)

    simulator.advance_minutes((EVENT_BUCKET + 1) * 60)

    assert simulator.plan.status in {"OPTIMAL", "FEASIBLE"}
    assert simulator.jobs[future.id].status == "CANCELLED"
    assert simulator.interrupted_jobs[future.id]["completed_t"] == 0.0
    assert simulator.interrupted_jobs[future.id]["remaining_t"] == 100.0
    if keeps_parallel_lock:
        assert unaffected.id not in simulator.interrupted_jobs
        assert simulator.jobs[unaffected.id].status == "PLANNED"
        assert any(fixed.id == f"FROZEN-{unaffected.id}"
                   for fixed in simulator.snapshot_scenario().fixed_jobs)


@pytest.mark.parametrize("disabled_resource", ["machine", "route"])
def test_whole_week_machine_or_route_disable_uses_available_parallel_path(
        disabled_resource: str):
    scenario = _weekly_parallel_route_fixture(backup_mass_t=20_000.0)
    if disabled_resource == "machine":
        scenario.machine("PRIMARY-RC").available = False
    else:
        scenario.link("PRIMARY-LINK").available = False

    result = _solve(scenario)

    assert sum(result.unmet["PLANT"]) == pytest.approx(0.0)
    assert _flow(result, 0, machine="BACKUP-RC") == pytest.approx(100.0)
    assert _flow(result, HOURS - 1,
                 machine="BACKUP-RC") == pytest.approx(100.0)
    assert all(job.machine != "PRIMARY-RC" for job in result.jobs)


def test_whole_week_plant_disable_keeps_all_demand_visible_as_unmet():
    scenario = _weekly_parallel_route_fixture(backup_mass_t=20_000.0)
    scenario.calendars.append(CalendarEntry(
        "LINE:PLANT", 0, HOURS - 1, "whole-week plant stop"))

    result = _solve(scenario)

    assert result.jobs == []
    assert len(result.unmet["PLANT"]) == HOURS
    assert sum(result.unmet["PLANT"]) == pytest.approx(HOURS * 100.0)
    assert all(scenario.demand_tph("PLANT", bucket) == 100.0
               for bucket in range(HOURS))
