"""Focused invariants for bounded weekly and sub-hour planning."""
from __future__ import annotations

import copy

import pytest

from mph_sim.domain import (
    BeltProfileEntry,
    CalendarEntry,
    DemandEntry,
    Destination,
    FixedJob,
    Grade,
    Job,
    JobBucketFlow,
    Link,
    Machine,
    Node,
    PlanResult,
    ProcessTransfer,
    Scenario,
    Segment,
    SimParams,
    Site,
    StockSpace,
)
from mph_sim.optimizer import (
    SolveOptions,
    _carry_future_belt_profile,
    _carry_segment_and_machine_state,
    _merge_weekly_windows,
    _split_integer_target,
    _weekly_window_target_profiles,
    solve_plan,
)
from mph_sim.validation import validate_scenario


def _three_day_delivery() -> Scenario:
    scenario = Scenario(
        "three-day rolling fixture",
        sites=[Site("TEST", "Test", "hub")],
        grades=[Grade("G", "Grade")],
        sim=SimParams(
            dt_h=1.0, horizon_buckets=72, horizon_minutes=4_320,
            max_solve_s=0.2, seed=7),
        config={
            "WEEKLY_ROLLING_SOLVE_ENABLED": True,
            "WEEKLY_TOTAL_WALL_TIME_S": 6.0,
            "WEEKLY_WINDOW_WALL_TIME_S": 2.0,
            "FLOW_TONNE_PENALTY": 1,
        },
    )
    scenario.spaces = [StockSpace(
        "PILE", "TEST", "yard", "open", 2_000, 1_000, "G")]
    scenario.segments = [Segment(
        "PILE", 10, 90, 1_000, "G", age_days=3)]
    scenario.machines = [Machine(
        "RC", "TEST", "reclaimer", 0, 100,
        reachable_spaces=["PILE"], position_x=10)]
    scenario.nodes = [
        Node("SPACE:PILE", "TEST", "space", "Pile"),
        Node("DEST:PLANT", "TEST", "dest", "Plant"),
    ]
    scenario.links = [Link(
        "PILE-PLANT", "SPACE:PILE", "DEST:PLANT", 100)]
    scenario.destinations = [Destination(
        "PLANT", "TEST", "DEST:PLANT", requested_grade="G")]
    scenario.demand = [
        DemandEntry("PLANT", bucket, 10, "G") for bucket in range(72)]
    return scenario


def _bucket_mass(result: PlanResult, bucket: int) -> float:
    return sum(
        flow.tonnage_t
        for job in result.jobs
        for flow in job.bucket_flows
        if flow.bucket == bucket)


def test_weekly_rolling_preserves_hourly_demand_and_inventory_boundaries():
    scenario = _three_day_delivery()
    scenario.calendars = [CalendarEntry("RC", 30, 31, "planned stop")]

    result = solve_plan(scenario, SolveOptions(max_wall_time_s=6))

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert result.solver == "cp-sat-weekly-rolling"
    assert result.breakdown["planning_weekly_window_count"] == 3
    assert result.breakdown["planning_weekly_completed_windows"] == 3
    assert len(result.delivered["PLANT"]) == 72
    assert len(result.unmet["PLANT"]) == 72
    assert len(result.inventory["PILE"]) == 73
    assert result.inventory["PILE"][24] == pytest.approx(760)
    assert result.inventory["PILE"][25] == pytest.approx(750)
    assert result.inventory["PILE"][48] == pytest.approx(540)
    assert [_bucket_mass(result, bucket) for bucket in (29, 30, 31, 32)] == \
        [10, 0, 0, 10]
    assert sum(result.unmet["PLANT"]) == pytest.approx(20)


def test_weekly_window_failure_is_explicit_and_returns_only_partial_plan():
    scenario = _three_day_delivery()
    scenario.calendars = [CalendarEntry("RC", 30, 30, "planned stop")]
    scenario.fixed_jobs = [FixedJob(
        "IMPOSSIBLE", "RC", "reclaim", "PILE", dest_id="PLANT",
        rate_tph=10, start_bucket=30, end_bucket=30, grade="G")]

    result = solve_plan(scenario, SolveOptions(max_wall_time_s=6))

    assert result.status == "INFEASIBLE"
    assert result.solver == "cp-sat-weekly-rolling-partial"
    assert result.breakdown["planning_weekly_completed_windows"] == 1
    assert result.breakdown["planning_weekly_window_02_status_code"] == 0
    assert result.breakdown["planning_weekly_unplanned_buckets"] == 48
    assert result.reasons[0].startswith("WEEKLY_WINDOW_FAILED buckets 24-47")
    assert all(job.end_bucket < 24 for job in result.jobs)
    assert len(result.delivered["PLANT"]) == 72
    assert len(result.unmet["PLANT"]) == 72
    assert len(result.inventory["PILE"]) == 73
    assert sum(result.unmet["PLANT"]) == pytest.approx(480)


def test_rejected_stage_search_slack_is_not_counted_as_a_planned_window():
    scenario = _three_day_delivery()
    rejected = PlanResult(
        status="PARTIAL",
        breakdown={"process_stage_target_shortfall_t": 25.0},
        reasons=["PROCESS_STAGE_TARGET_SHORTFALL[WASH] 25 t"],
    )

    merged = _merge_weekly_windows(
        scenario, [(0, 24), (24, 48), (48, 72)], [rejected],
        hard_wall_s=6.0, elapsed_s=1.0, failed_index=0,
    )

    assert merged.status == "PARTIAL"
    assert merged.breakdown["planning_weekly_completed_windows"] == 0
    assert merged.breakdown["planning_weekly_rejected_stage_shortfall_t"] == 25
    assert merged.jobs == []
    assert "candidate rejected" in merged.reasons[0]


def test_whole_run_disabled_route_is_respected_in_every_daily_window():
    scenario = _three_day_delivery()
    scenario.links[0].available = False

    result = solve_plan(scenario, SolveOptions(max_wall_time_s=6))

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert not result.jobs
    assert sum(result.unmet["PLANT"]) == pytest.approx(720)


def test_whole_horizon_terminal_target_is_enforced_only_at_final_window():
    scenario = _three_day_delivery()
    scenario.config["TERMINAL_INVENTORY_EXACT_T"] = {"PILE": 500}

    result = solve_plan(scenario, SolveOptions(max_wall_time_s=6))

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert result.breakdown["planning_weekly_completed_windows"] == 3
    assert result.inventory["PILE"][-1] == pytest.approx(500)
    assert sum(result.delivered["PLANT"]) == pytest.approx(500)
    assert sum(result.unmet["PLANT"]) == pytest.approx(220)


def test_integer_target_split_is_exact_and_deterministic():
    assert _split_integer_target(10, [1, 1, 1]) == [4, 3, 3]
    assert sum(_split_integer_target(74_200, [24] * 7)) == 74_200
    assert _split_integer_target(0, [0, 0]) == [0, 0]


def test_daily_output_targets_follow_grade_yield_not_raw_feed_share():
    scenario = _three_day_delivery()
    scenario.grades = [Grade("RAW-A", "Raw A"), Grade("RAW-B", "Raw B")]
    scenario.spaces = [
        StockSpace("RAW-A", "TEST", "yard", "open", 1_000, 500, "RAW-A"),
        StockSpace("RAW-B", "TEST", "yard", "open", 1_000, 500, "RAW-B"),
        StockSpace("WET", "TEST", "yard", "open", 1_000, 0, ""),
    ]
    scenario.destinations = [Destination(
        "PROCESS", "TEST", "DEST:PLANT", requested_grade="RAW-A",
        demand_kind="process_feed", process_stage="BEN-WASH-L1")]
    scenario.demand = [
        DemandEntry("PROCESS", bucket, 10, "RAW-A")
        for bucket in range(24)
    ] + [
        DemandEntry("PROCESS", bucket, 10, "RAW-B")
        for bucket in range(24, 48)
    ]
    scenario.process_transfers = [
        ProcessTransfer(
            "WASH-A", "RC", "RAW-A", "WET",
            process_stage="BEN-WASH-L1", yield_factor=0.5),
        ProcessTransfer(
            "WASH-B", "RC", "RAW-B", "WET",
            process_stage="BEN-WASH-L1", yield_factor=0.8),
    ]
    scenario.config["PROCESS_STAGE_OUTPUT_TARGET_T"] = {
        "BEN-MINE-STACK-L1": 480,
        "BEN-MINE-RECLAIM-L1": 480,
        "BEN-RAW-STACK-L1": 480,
        "BEN-WASH-L1": 312,
        "BEN-PRODUCT-STACK-L1": 312,
    }

    stage_profiles, _ = _weekly_window_target_profiles(
        scenario, [(0, 24), (24, 48), (48, 72)])

    assert stage_profiles["BEN-MINE-STACK-L1"] == [240, 240, 0]
    assert stage_profiles["BEN-RAW-STACK-L1"] == [240, 240, 0]
    assert stage_profiles["BEN-WASH-L1"] == [120, 192, 0]
    assert stage_profiles["BEN-PRODUCT-STACK-L1"] == [120, 192, 0]
    assert all(sum(values) == scenario.config[
        "PROCESS_STAGE_OUTPUT_TARGET_T"][stage]
               for stage, values in stage_profiles.items())


def test_nonzero_replan_offset_is_preserved_across_civil_day_windows():
    scenario = _three_day_delivery()
    scenario.sim.horizon_buckets = 83
    scenario.sim.horizon_minutes = 83 * 60
    scenario.demand = [
        DemandEntry("PLANT", bucket, 10, "G")
        for bucket in range(85, 168)
    ]

    result = solve_plan(
        scenario, SolveOptions(horizon_offset=85, max_wall_time_s=6))

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert result.horizon_offset == 85
    assert len(result.delivered["PLANT"]) == 83
    assert len(result.inventory["PILE"]) == 84
    assert min(job.start_bucket for job in result.jobs) >= 85


def test_mixed_resolution_uses_weekly_rolling_tactical_plan():
    scenario = _three_day_delivery()
    scenario.sim.mixed_resolution_enabled = True
    scenario.sim.near_term_step_min = 15
    scenario.sim.near_term_horizon_min = 240
    scenario.sim.far_term_step_min = 60

    result = solve_plan(
        scenario, SolveOptions(max_time_s=1, max_wall_time_s=6))

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert result.solver == "cp-sat-mixed"
    assert result.breakdown["planning_weekly_rolling"] == 1
    assert result.breakdown["planning_weekly_window_count"] == 3
    assert result.breakdown["planning_mixed_total_hard_wall_s"] == 6
    assert result.breakdown["planning_mixed_elapsed_s"] <= 6
    assert len(result.delivered["PLANT"]) == 72
    assert len(result.inventory["PILE"]) == 73


def _terminal_dispatch_fixture(grade: str) -> Scenario:
    scenario = Scenario(
        "terminal dispatch fixture",
        sites=[Site("MINE", "Mine", "mine"), Site("HUB", "Hub", "hub")],
        grades=[Grade("GOOD", "Good"), Grade("BAD", "Bad")],
        sim=SimParams(
            dt_h=1.0, horizon_buckets=2, horizon_minutes=120,
            max_solve_s=0.2, seed=7),
        config={"CORRIDOR_DISPATCH_TARGET_T": {"C": 10}},
    )
    scenario.spaces = [
        StockSpace("GOOD", "MINE", "yard", "open", 100, 50, "GOOD"),
        StockSpace("BAD", "MINE", "yard", "open", 100, 50, "BAD"),
        StockSpace(
            "PILE", "HUB", "yard", "open", 100, 0, "GOOD",
            designated_grade="GOOD"),
    ]
    scenario.machines = [
        Machine(
            "RC", "MINE", "reclaimer", 0, 100,
            reachable_spaces=["GOOD", "BAD"]),
        Machine(
            "ST", "HUB", "stacker", 0, 100,
            reachable_spaces=["PILE"]),
    ]
    scenario.nodes = [
        Node("SPACE:GOOD", "MINE", "space", "Good source"),
        Node("SPACE:BAD", "MINE", "space", "Bad source"),
        Node("CORRIDOR:IN", "MINE", "tower", "Corridor inlet"),
        Node("CORRIDOR:OUT", "HUB", "tower", "Corridor outlet"),
        Node("SPACE:PILE", "HUB", "space", "Receiving pile"),
    ]
    scenario.links = [
        Link("GOOD-IN", "SPACE:GOOD", "CORRIDOR:IN", 100),
        Link("BAD-IN", "SPACE:BAD", "CORRIDOR:IN", 100),
        Link("C", "CORRIDOR:IN", "CORRIDOR:OUT", 100,
             transit_buckets=2),
        Link("OUT-PILE", "CORRIDOR:OUT", "SPACE:PILE", 100),
    ]
    scenario.fixed_jobs = [FixedJob(
        "LATE", "RC", "dispatch", grade, dest_id="C", grade=grade,
        rate_tph=10, start_bucket=1, end_bucket=1)]
    return scenario


def test_terminal_in_transit_requires_receiving_grade_compatibility():
    incompatible = solve_plan(_terminal_dispatch_fixture("BAD"))
    compatible = solve_plan(_terminal_dispatch_fixture("GOOD"))
    mapped_scenario = _terminal_dispatch_fixture("BAD")
    mapped_scenario.config["STORAGE_GRADE_COMPATIBILITY"] = {
        "BAD": ["GOOD"]}
    explicitly_mapped = solve_plan(mapped_scenario)

    assert incompatible.status == "INFEASIBLE"
    assert compatible.status in {"OPTIMAL", "FEASIBLE"}
    assert explicitly_mapped.status in {"OPTIMAL", "FEASIBLE"}
    assert sum(job.tonnage_t for job in compatible.jobs
               if job.kind == "dispatch") == pytest.approx(10)


def test_in_transit_and_twin_detail_are_carried_between_windows():
    scenario = _three_day_delivery()
    scenario.links[0].transit_buckets = 2
    scenario.belt_profile = [BeltProfileEntry(
        "PILE-PLANT", 24, 7, "G")]
    dispatch = Job(
        "D", "RC", "dispatch", "PILE", dest_id="PILE-PLANT",
        grade="G", tonnage_t=10, start_bucket=23, end_bucket=23,
        stop_x=77,
        bucket_flows=[JobBucketFlow(23, 10, ["PILE-PLANT"])])
    result = PlanResult(
        status="FEASIBLE", jobs=[dispatch],
        inventory={"PILE": [1_000, 990]})

    pending = _carry_future_belt_profile(scenario, result, 24)
    assert sorted((entry.arrive_bucket, entry.mass_t) for entry in pending) == \
        [(24, 7), (25, 10)]

    _carry_segment_and_machine_state(scenario, result)
    assert scenario.space("PILE").mass_t == pytest.approx(990)
    assert len(scenario.segments) == 1
    assert (scenario.segments[0].x_from, scenario.segments[0].x_to,
            scenario.segments[0].grade) == (10, 90, "G")
    assert scenario.segments[0].mass_t == pytest.approx(990)
    # Rolling carry uses the same representative work location as the
    # minute-level positioning pass: the midpoint of the reclaimed interval.
    assert scenario.machine("RC").position_x == pytest.approx(38.5)


def test_weekly_merge_preserves_final_terminal_supply_kpis():
    scenario = _three_day_delivery()
    windows = [(0, 24), (24, 48), (48, 72)]
    results = [
        PlanResult(
            status="OPTIMAL",
            inventory={"PILE": [1_000.0] * 25},
            breakdown={})
        for _ in windows
    ]
    results[-1].breakdown.update({
        "terminal_supply_position_max_t": 1_250.0,
        "terminal_supply_position_t": 1_100.0,
        "terminal_corridor_in_transit_t": 100.0,
    })

    merged = _merge_weekly_windows(
        scenario, windows, results,
        hard_wall_s=6.0, elapsed_s=1.0)

    assert merged.breakdown["terminal_supply_position_max_t"] == 1_250.0
    assert merged.breakdown["terminal_supply_position_t"] == 1_100.0
    assert merged.breakdown["terminal_corridor_in_transit_t"] == 100.0


def test_invalid_native_time_grid_is_rejected_before_model_build():
    scenario = _three_day_delivery()
    scenario.sim.dt_h = 0.333  # 19.98 minutes is not a canonical minute grid.

    result = solve_plan(scenario)
    codes = {issue.code for issue in validate_scenario(scenario)}

    assert result.status == "ERROR"
    assert result.reasons[0].startswith("BAD_TIME_GRID")
    assert "BAD_TIME_GRID" in codes


def test_blank_duplicate_grade_obeys_last_row_default_contract():
    scenario = _three_day_delivery()
    scenario.destinations[0].requested_grade = "G"
    scenario.grades.append(Grade("OTHER", "Other"))
    scenario.demand = [
        DemandEntry("PLANT", 0, 10, "OTHER"),
        DemandEntry("PLANT", 0, 20, ""),
    ]

    assert scenario.demand_tph("PLANT", 0) == 20
    assert scenario.demand_grade("PLANT", 0) == "G"
    assert "DUPLICATE_DEMAND_BUCKET" in {
        issue.code for issue in validate_scenario(scenario)}
