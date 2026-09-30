"""Focused checks for canonical-minute and mixed-resolution planning."""
from __future__ import annotations

import copy
import math
import pytest

from mph_sim.domain import (BeltProfileEntry, CalendarEntry, DemandEntry,
                            Destination, ExogenousFlow, FixedJob, Grade, Link,
                            Machine, Node, ProcessTransfer, Scenario, SimParams,
                            Site, SpecRange, StockSpace)
from mph_sim.optimizer import SolveOptions, solve_plan
from mph_sim.planning_time import resample_scenario_window


def _small_flexible_feed() -> Scenario:
    scenario = Scenario(
        name="mixed-resolution fixture",
        sim=SimParams(
            horizon_buckets=4, dt_h=1.0, horizon_minutes=240,
            mixed_resolution_enabled=True,
            near_term_step_min=15, near_term_horizon_min=240,
            far_term_step_min=60, max_solve_s=1.0, seed=7),
        config={"FLOW_TONNE_PENALTY": 1},
    )
    scenario.sites = [Site("TEST", "Test", "mine")]
    scenario.grades = [Grade("RAW", "Raw"), Grade("PRODUCT", "Product")]
    scenario.spaces = [
        StockSpace("RAW", "TEST", "raw", "open", 1_000, 500, "RAW"),
        StockSpace("PRODUCT", "TEST", "product", "closed",
                   1_000, 0, "PRODUCT"),
    ]
    scenario.machines = [Machine(
        "FEEDER", "TEST", "reclaimer", 0, 100,
        reachable_spaces=["RAW"])]
    scenario.nodes = [
        Node("SPACE:RAW", "TEST", "space", "Raw"),
        Node("SPACE:PRODUCT", "TEST", "space", "Product"),
    ]
    scenario.links = [Link(
        "RAW-WASH", "SPACE:RAW", "SPACE:PRODUCT", 100)]
    scenario.process_transfers = [ProcessTransfer(
        "WASH", "FEEDER", "RAW", "PRODUCT", ["RAW-WASH"],
        operation_kind="reclaim", process_stage="WASH",
        output_grade="PRODUCT", yield_factor=0.8)]
    scenario.destinations = [Destination(
        "WASH-FEED", "TEST", "SPACE:PRODUCT",
        demand_default_tph=6.25, requested_grade="RAW",
        demand_kind="process_feed", process_stage="WASH")]
    return scenario


def test_legacy_horizon_is_derived_until_canonical_value_is_configured():
    legacy = SimParams(horizon_buckets=4, dt_h=0.5)
    assert legacy.effective_horizon_minutes() == 120
    legacy.horizon_minutes = 180
    assert legacy.effective_horizon_minutes() == 180


def test_resampling_preserves_physical_bucket_durations():
    scenario = _small_flexible_feed()
    scenario.demand = [DemandEntry("WASH-FEED", 1, 40.0)]
    scenario.calendars = [CalendarEntry("FEEDER", 1, 1)]
    scenario.exogenous = [ExogenousFlow(
        "EX", "RAW", 110.0, "RAW", start_bucket=1, end_bucket=1)]
    scenario.links[0].transit_buckets = 2
    scenario.belt_profile = [BeltProfileEntry(
        "RAW-WASH", arrive_bucket=2, mass_t=10, grade="RAW")]
    scenario.fixed_jobs = [FixedJob(
        "FIX", "FEEDER", "reclaim", "RAW", rate_tph=50,
        start_bucket=1, end_bucket=1,
        start_minute=75, end_minute=105)]

    refined = resample_scenario_window(
        scenario, window_start_minute=0, window_duration_min=240,
        step_min=15, commit_window_min=30)

    assert refined.sim.dt_h == pytest.approx(0.25)
    assert refined.sim.horizon_buckets == 16
    assert refined.sim.frozen_buckets == 2
    assert refined.links[0].transit_buckets == 8  # two physical hours
    assert (refined.calendars[0].start_bucket,
            refined.calendars[0].end_bucket) == (4, 7)
    assert [(entry.start_bucket, entry.end_bucket)
            for entry in refined.exogenous] == [
                (4, 4), (5, 5), (6, 6), (7, 7)]
    assert sum(entry.rate_tph * refined.sim.dt_h
               for entry in refined.exogenous) == pytest.approx(110.0)
    assert [entry.arrive_bucket for entry in refined.belt_profile] == [
        8, 9, 10, 11]
    assert [entry.mass_t for entry in refined.belt_profile] == [
        3.0, 3.0, 2.0, 2.0]
    assert sum(entry.mass_t for entry in refined.belt_profile) == 10.0
    assert (refined.fixed_jobs[0].start_bucket,
            refined.fixed_jobs[0].end_bucket) == (5, 6)
    assert (refined.fixed_jobs[0].start_minute,
            refined.fixed_jobs[0].end_minute) == (75, 105)
    rates = [refined.demand_tph("WASH-FEED", bucket)
             for bucket in range(16)]
    assert rates[4:8] == [40.0] * 4


def test_mixed_solve_changes_authoritative_near_term_allocation():
    scenario = _small_flexible_feed()
    tactical_scenario = copy.deepcopy(scenario)
    tactical_scenario.sim.mixed_resolution_enabled = False
    tactical = solve_plan(
        tactical_scenario, SolveOptions(max_time_s=0.5))
    result = solve_plan(scenario, SolveOptions(max_time_s=1.0))

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert result.solver == "cp-sat-mixed"
    assert result.solver_optimality_scope == "mixed_component_reference"
    assert result.breakdown[
        "planning_objective_is_tactical_reference"] == 1.0
    assert result.breakdown[
        "planning_merged_optimality_certificate_available"] == 0.0
    assert result.objective == pytest.approx(
        result.breakdown["planning_tactical_objective"])
    assert result.solver_best_bound == pytest.approx(
        result.breakdown["planning_tactical_best_objective_bound"])
    assert result.solver_relative_gap_pct == pytest.approx(
        result.breakdown["planning_tactical_relative_gap_pct"])
    for key in (
        "planning_near_objective",
        "planning_near_best_objective_bound",
        "planning_near_relative_gap_pct",
        "planning_tactical_objective",
        "planning_tactical_best_objective_bound",
        "planning_tactical_relative_gap_pct",
    ):
        assert math.isfinite(result.breakdown[key])
    assert result.breakdown["planning_near_relative_gap_pct"] >= 0
    assert result.breakdown["planning_tactical_relative_gap_pct"] >= 0
    assert result.breakdown["planning_near_step_min"] == 15.0
    assert result.breakdown["planning_far_step_min"] == 60.0
    near_jobs = [job for job in result.jobs if job.id.startswith("N15-")]
    assert near_jobs
    assert all(job.start_minute is not None and job.end_minute is not None
               for job in result.jobs)
    # The authoritative prefix can start/stop on a 15-minute boundary instead
    # of being constrained to native hour boundaries.
    assert any(
        job.start_minute % 60 != 0 or job.end_minute % 60 != 0
        for job in near_jobs)
    assert sum(job.tonnage_t for job in near_jobs) == pytest.approx(24.0)
    tactical_by_hour = {
        bucket: sum(
            flow.tonnage_t for job in tactical.jobs
            for flow in job.bucket_flows if flow.bucket == bucket)
        for bucket in range(4)
    }
    refined_by_hour = {
        bucket: sum(
            flow.tonnage_t for job in near_jobs
            for flow in job.bucket_flows if flow.bucket == bucket)
        for bucket in range(4)
    }
    assert refined_by_hour == tactical_by_hour
    # One hierarchical row is created for each non-zero tactical candidate x
    # native-hour block. Exact transition penalties may consolidate the same
    # campaign into fewer hours, so this count follows the tactical solution
    # instead of assuming all four hours are occupied.
    assert result.breakdown["hierarchical_native_flow_targets"] == sum(
        mass > 0 for mass in tactical_by_hour.values())


def test_exact_rolling_origin_is_not_rounded_back_to_an_hour():
    scenario = _small_flexible_feed()
    result = solve_plan(
        scenario,
        SolveOptions(start_minute_offset=30, max_time_s=1.0))
    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert result.horizon_offset == 0
    assert result.jobs
    assert all(job.start_minute >= 30 for job in result.jobs)
    near_jobs = [job for job in result.jobs if job.id.startswith("N15-")]
    assert near_jobs
    assert all((job.start_minute - 30) % 15 == 0 and
               (job.end_minute - 30) % 15 == 0
               for job in near_jobs)
    assert result.breakdown["planning_near_step_min"] == 15.0


def test_invalid_mixed_grid_is_rejected_instead_of_reinterpreting_time():
    scenario = _small_flexible_feed()
    scenario.sim.far_term_step_min = 30
    result = solve_plan(scenario, SolveOptions(max_time_s=0.2))
    assert result.status == "ERROR"
    assert result.reasons and result.reasons[0].startswith(
        "MIXED_RESOLUTION_CONFIG")


def _quality_window_fixture(aggregate: bool) -> Scenario:
    scenario = Scenario(
        name="quality-window fixture",
        sim=SimParams(horizon_buckets=2, dt_h=0.25, max_solve_s=0.5),
        config={"QUALITY_WINDOW_AGGREGATION_ENABLED": aggregate},
    )
    scenario.sites = [Site("TEST", "Test", "hub")]
    scenario.grades = [
        Grade("LOW", "Low", {"BPL": 0.0}),
        Grade("HIGH", "High", {"BPL": 100.0}),
    ]
    scenario.spaces = [
        StockSpace("LOW", "TEST", "yard", "open", 100, 100, "LOW"),
        StockSpace("HIGH", "TEST", "yard", "open", 100, 100, "HIGH"),
    ]
    scenario.machines = [
        Machine("ML", "TEST", "reclaimer", 0, 100,
                reachable_spaces=["LOW"]),
        Machine("MH", "TEST", "reclaimer", 0, 100,
                reachable_spaces=["HIGH"]),
    ]
    scenario.nodes = [
        Node("SPACE:LOW", "TEST", "space", "Low"),
        Node("SPACE:HIGH", "TEST", "space", "High"),
        Node("DEST:D", "TEST", "dest", "Destination"),
    ]
    scenario.links = [
        Link("LLOW", "SPACE:LOW", "DEST:D", 100),
        Link("LHIGH", "SPACE:HIGH", "DEST:D", 100),
    ]
    scenario.destinations = [Destination(
        "D", "TEST", "DEST:D", demand_default_tph=100,
        spec=[SpecRange("BPL", pmin=50, hard=True, averaging_h=0.5)])]
    scenario.fixed_jobs = [
        FixedJob("LOW-FIRST", "ML", "reclaim", "LOW", dest_id="D",
                 rate_tph=100, start_bucket=0, end_bucket=0),
        FixedJob("HIGH-SECOND", "MH", "reclaim", "HIGH", dest_id="D",
                 rate_tph=100, start_bucket=1, end_bucket=1),
    ]
    return scenario


def test_quality_averaging_duration_is_preserved_on_fine_grid():
    grouped = solve_plan(
        _quality_window_fixture(True), SolveOptions(max_time_s=0.5))
    per_bucket = solve_plan(
        _quality_window_fixture(False), SolveOptions(max_time_s=0.5))
    assert grouped.status in {"OPTIMAL", "FEASIBLE"}
    assert per_bucket.status == "INFEASIBLE"
