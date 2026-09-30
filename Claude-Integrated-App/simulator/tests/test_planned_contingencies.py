"""Focused planner checks for time-bounded resource contingencies."""
from __future__ import annotations

import pytest

from mph_sim.adapters import SimulatedAspAdapter, job_to_plan_item
from mph_sim.domain import (CalendarEntry, Destination, Grade, Job,
                            JobBucketFlow, JobExecutionWindow, Link, Machine,
                            Node, PlanResult, ProcessTransfer, Scenario,
                            SimParams, Site, StockSpace)
from mph_sim.optimizer import SolveOptions, solve_plan
from mph_sim.persistence import scenario_from_json, scenario_to_json
from mph_sim.planning_time import resample_scenario_window
from mph_sim.simulator import Simulator
from mph_sim.timing import VALIDATED, validate_execution_windows
from mph_sim.validation import validate_scenario


def _reclaim_fixture(*, dt_h: float = 0.25,
                     horizon_buckets: int = 4) -> Scenario:
    scenario = Scenario(
        name="planned-contingency fixture",
        sim=SimParams(
            horizon_buckets=horizon_buckets, dt_h=dt_h,
            max_solve_s=0.5, seed=7),
    )
    scenario.sites = [Site("TEST", "Test", "hub")]
    scenario.grades = [Grade("G", "Grade")]
    scenario.spaces = [StockSpace(
        "PILE", "TEST", "yard", "open", 1_000, 1_000, "G")]
    scenario.machines = [Machine(
        "RC", "TEST", "reclaimer", 0, 100,
        reachable_spaces=["PILE"])]
    scenario.nodes = [
        Node("SPACE:PILE", "TEST", "space", "Pile"),
        Node("DEST:PLANT", "TEST", "dest", "Plant"),
    ]
    scenario.links = [Link(
        "PILE-PLANT", "SPACE:PILE", "DEST:PLANT", 100)]
    scenario.destinations = [Destination(
        "PLANT", "TEST", "DEST:PLANT", demand_default_tph=100)]
    return scenario


def _flow_in_bucket(plan, bucket: int) -> float:
    return sum(
        flow.tonnage_t
        for job in plan.jobs
        for flow in job.bucket_flows
        if flow.bucket == bucket)


def _process_line_fixture() -> Scenario:
    scenario = Scenario(
        name="physical-line contingency fixture",
        sim=SimParams(
            horizon_buckets=4, dt_h=0.25, max_solve_s=0.5, seed=7),
    )
    scenario.sites = [Site("BENGUERIR", "Benguerir", "mine")]
    scenario.grades = [Grade("RAW", "Raw"), Grade("WASHED", "Washed")]
    scenario.spaces = [
        StockSpace("RAW", "BENGUERIR", "raw", "open",
                   1_000, 1_000, "RAW"),
        StockSpace("WASHED", "BENGUERIR", "washed", "closed",
                   1_000, 0, "WASHED"),
    ]
    scenario.machines = [Machine(
        "LINE-FEEDER", "BENGUERIR", "reclaimer", 0, 100,
        reachable_spaces=["RAW"])]
    scenario.nodes = [
        Node("SPACE:RAW", "BENGUERIR", "space", "Raw"),
        Node("SPACE:WASHED", "BENGUERIR", "space", "Washed"),
    ]
    scenario.links = [Link(
        "WASH-PATH", "SPACE:RAW", "SPACE:WASHED", 100)]
    scenario.process_transfers = [ProcessTransfer(
        "BEN-WASH-TRANSFER", "LINE-FEEDER", "RAW", "WASHED",
        ["WASH-PATH"], operation_kind="reclaim",
        process_stage="BEN-WASH-L1", output_grade="WASHED")]
    scenario.destinations = [Destination(
        "BEN-WASH-FEED", "BENGUERIR", "SPACE:WASHED",
        demand_default_tph=100, requested_grade="RAW",
        demand_kind="process_feed", process_stage="BEN-WASH-L1")]
    return scenario


def test_exact_fifteen_minute_link_outage_blocks_only_that_fine_slot():
    scenario = _reclaim_fixture()
    scenario.calendars = [CalendarEntry(
        "LINK:PILE-PLANT", 0, 0, "planned belt inspection",
        start_minute=15, end_minute=30)]

    assert scenario.calendar_scope_blocked(
        "LINK:PILE-PLANT", minute=15)
    assert scenario.calendar_scope_blocked(
        "LINK:PILE-PLANT", minute=29)
    assert not scenario.calendar_scope_blocked(
        "LINK:PILE-PLANT", minute=30)

    result = solve_plan(scenario, SolveOptions(max_time_s=0.5))

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert _flow_in_bucket(result, 1) == 0
    assert [_flow_in_bucket(result, bucket) for bucket in (0, 2, 3)] == \
        [25.0, 25.0, 25.0]
    assert result.unmet["PLANT"][1] == pytest.approx(25.0)
    assert not [issue for issue in validate_scenario(scenario)
                if issue.severity == "ERROR"]


def test_exact_calendar_window_is_preserved_when_resampled():
    scenario = _reclaim_fixture(dt_h=1.0, horizon_buckets=2)
    scenario.calendars = [CalendarEntry(
        "LINK:PILE-PLANT", 1, 1, "planned belt inspection",
        start_minute=75, end_minute=90)]

    refined = resample_scenario_window(
        scenario, window_start_minute=60, window_duration_min=60,
        step_min=15)

    outage = refined.calendars[0]
    assert (outage.start_bucket, outage.end_bucket) == (1, 1)
    assert (outage.start_minute, outage.end_minute) == (15, 30)
    assert refined.calendar_scope_blocked("LINK:PILE-PLANT", bucket=1)
    assert not refined.calendar_scope_blocked("LINK:PILE-PLANT", bucket=0)


def test_exact_rate_cap_is_preserved_when_resampled():
    scenario = _reclaim_fixture(dt_h=1.0, horizon_buckets=2)
    scenario.calendars = [CalendarEntry(
        "LINK:PILE-PLANT", 1, 1, "planned belt derating",
        start_minute=75, end_minute=90,
        action="RATE_CAP", rate_tph=40)]

    refined = resample_scenario_window(
        scenario, window_start_minute=60, window_duration_min=60,
        step_min=15)

    change = refined.calendars[0]
    assert (change.start_minute, change.end_minute) == (15, 30)
    assert (change.action, change.rate_tph) == ("RATE_CAP", 40)
    assert refined.resource_rate_cap_tph(
        "LINK:PILE-PLANT", bucket=1) == 40
    assert refined.resource_rate_cap_tph(
        "LINK:PILE-PLANT", bucket=0) is None
    assert not refined.calendar_scope_blocked(
        "LINK:PILE-PLANT", bucket=1)


def test_rate_change_survives_scenario_json_round_trip():
    scenario = _reclaim_fixture()
    scenario.calendars = [CalendarEntry(
        "RC", 0, 0, "planned derating",
        start_minute=15, end_minute=30,
        action="RATE_CAP", rate_tph=40)]

    restored = scenario_from_json(scenario_to_json(scenario))

    assert restored.calendars == scenario.calendars


def test_exact_outage_does_not_block_earlier_minutes_in_legacy_bucket():
    scenario = _reclaim_fixture(dt_h=1.0, horizon_buckets=1)
    scenario.calendars = [CalendarEntry(
        "RC", 0, 0, "planned inspection",
        start_minute=15, end_minute=30)]
    job = Job(
        "J-BEFORE-OUTAGE", "RC", "reclaim", "PILE", dest_id="PLANT",
        grade="G", rate_tph=100, tonnage_t=25,
        start_bucket=0, end_bucket=0, path=["PILE-PLANT"],
        execution_rate_cap_tph=100,
        bucket_flows=[JobBucketFlow(0, 25, ["PILE-PLANT"])],
        execution_windows=[JobExecutionWindow(
            0, 0, 0, 15, 25, 100, status=VALIDATED)])

    assert validate_execution_windows(scenario, [job]) == []
    asp_issues = SimulatedAspAdapter(scenario).submit_plan_items([
        job_to_plan_item(scenario, job)])
    assert "WorkCalendar" not in {issue.rule for issue in asp_issues}
    assert scenario.effective_resource_available(
        "RC", minute=14)
    assert not scenario.effective_resource_available(
        "RC", minute=15)
    simulator = Simulator(scenario, PlanResult(status="FEASIBLE"))
    assert simulator.machine_ok("RC", minute=14)
    assert not simulator.machine_ok("RC", minute=15)


def test_legacy_machine_bucket_calendar_remains_supported():
    scenario = _reclaim_fixture()
    scenario.calendars = [CalendarEntry(
        "RC", 1, 1, "legacy planned maintenance")]

    result = solve_plan(scenario, SolveOptions(max_time_s=0.5))

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert scenario.machine_blocked("RC", 1)
    assert not scenario.machine_blocked("RC", 0)
    assert _flow_in_bucket(result, 1) == 0
    assert result.unmet["PLANT"][1] == pytest.approx(25.0)


def test_physical_process_line_scope_expands_to_its_process_stages():
    scenario = _process_line_fixture()
    scenario.calendars = [CalendarEntry(
        "LINE:BG-WASH-L1", 0, 0, "planned line stop",
        start_minute=15, end_minute=30)]

    result = solve_plan(scenario, SolveOptions(max_time_s=0.5))

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert _flow_in_bucket(result, 1) == 0
    assert sum(result.unmet["BEN-WASH-FEED"]) == pytest.approx(25.0)
    assert not [issue for issue in validate_scenario(scenario)
                if issue.severity == "ERROR"]


@pytest.mark.parametrize("scope", ["RC", "LINK:PILE-PLANT"])
def test_timed_machine_or_conveyor_rate_change_caps_only_that_slot(scope):
    scenario = _reclaim_fixture()
    scenario.calendars = [CalendarEntry(
        scope, 0, 0, "planned derating",
        start_minute=15, end_minute=30,
        action="RATE_CAP", rate_tph=40)]

    result = solve_plan(scenario, SolveOptions(max_time_s=0.5))

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert _flow_in_bucket(result, 1) == 10
    assert [_flow_in_bucket(result, bucket) for bucket in (0, 2, 3)] == \
        [25.0, 25.0, 25.0]
    assert result.unmet["PLANT"][1] == pytest.approx(15.0)
    assert all(
        window.status == "VALIDATED"
        for job in result.jobs for window in job.execution_windows)
    assert not [issue for issue in validate_scenario(scenario)
                if issue.severity == "ERROR"]


def test_whole_run_destination_plant_rate_cap_is_model_backed():
    scenario = _reclaim_fixture()
    scenario.config["PROCESS_LINE_CAPACITY_TPH"] = {"PLANT": 40}

    result = solve_plan(scenario, SolveOptions(max_time_s=0.5))

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert [_flow_in_bucket(result, bucket) for bucket in range(4)] == \
        [10.0, 10.0, 10.0, 10.0]
    assert sum(result.unmet["PLANT"]) == pytest.approx(60.0)


def test_whole_run_physical_process_line_rate_cap_applies_to_mapped_stage():
    scenario = _process_line_fixture()
    scenario.config["PROCESS_LINE_CAPACITY_TPH"] = {"BG-WASH-L1": 40}

    result = solve_plan(scenario, SolveOptions(max_time_s=0.5))

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert [_flow_in_bucket(result, bucket) for bucket in range(4)] == \
        [10.0, 10.0, 10.0, 10.0]
    assert sum(result.unmet["BEN-WASH-FEED"]) == pytest.approx(60.0)


def test_rate_change_validation_rejects_ambiguous_site_rate_scope():
    scenario = _reclaim_fixture()
    scenario.calendars = [CalendarEntry(
        "SITE:TEST", 0, 0, action="RATE_CAP", rate_tph=40)]

    assert "BAD_RATE_SCOPE" in {
        issue.code for issue in validate_scenario(scenario)
        if issue.severity == "ERROR"}
