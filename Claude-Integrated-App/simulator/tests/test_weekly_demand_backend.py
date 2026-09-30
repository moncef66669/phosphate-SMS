"""Focused backend regressions for seven-day, hourly demand campaigns."""
from __future__ import annotations

import json

import pytest

from mph_sim import datasets
from mph_sim.campaigns import (default_campaign_policy_config,
                               resolve_campaign_policy)
from mph_sim.domain import (
    DemandEntry,
    Destination,
    Grade,
    Link,
    Machine,
    Node,
    ProcessTransfer,
    Scenario,
    SimParams,
    Site,
    StockSpace,
)
from mph_sim.optimizer import SolveOptions, solve_plan
from mph_sim.persistence import scenario_from_json, scenario_to_json
from mph_sim.planning_time import resample_scenario_window
from mph_sim.validation import validate_scenario


def _two_grade_delivery() -> Scenario:
    sc = Scenario(
        "two daily delivery grades",
        sites=[Site("TEST", "Test", "stockyard")],
        grades=[Grade("A", "A"), Grade("B", "B")],
        sim=SimParams(
            horizon_buckets=48, dt_h=1.0, horizon_minutes=2_880,
            max_solve_s=0.5, seed=7),
        config={"FLOW_TONNE_PENALTY": 1},
    )
    sc.spaces = [
        StockSpace(
            "PILE-A", "TEST", "yard", "open", 100.0, 50.0, "A"),
        StockSpace(
            "PILE-B", "TEST", "yard", "open", 100.0, 50.0, "B"),
    ]
    sc.machines = [
        Machine(
            "RC-A", "TEST", "reclaimer", 0.0, 50.0,
            reachable_spaces=["PILE-A"]),
        Machine(
            "RC-B", "TEST", "reclaimer", 0.0, 50.0,
            reachable_spaces=["PILE-B"]),
    ]
    sc.nodes = [
        Node("SPACE:PILE-A", "TEST", "space", "A"),
        Node("SPACE:PILE-B", "TEST", "space", "B"),
        Node("DEST:PLANT", "TEST", "destination", "Plant"),
    ]
    sc.links = [
        Link("A-PLANT", "SPACE:PILE-A", "DEST:PLANT", 50.0),
        Link("B-PLANT", "SPACE:PILE-B", "DEST:PLANT", 50.0),
    ]
    sc.destinations = [Destination(
        "PLANT", "TEST", "DEST:PLANT", requested_grade="A")]
    sc.demand = [
        DemandEntry(
            "PLANT", bucket,
            10.0 if bucket == 0 else 20.0 if bucket == 24 else 0.0,
            requested_grade="A" if bucket < 24 else "B")
        for bucket in range(48)
    ]
    return sc


def _two_grade_process_feed() -> Scenario:
    sc = Scenario(
        "two daily feed grades",
        sites=[Site("TEST", "Test", "mine")],
        grades=[Grade("RAW-A", "Raw A"), Grade("RAW-B", "Raw B"),
                Grade("PRODUCT", "Product")],
        sim=SimParams(
            horizon_buckets=48, dt_h=1.0, horizon_minutes=2_880,
            max_solve_s=0.5, seed=7),
        config={"FLOW_TONNE_PENALTY": 1},
    )
    sc.spaces = [
        StockSpace(
            "RAW-A", "TEST", "raw", "open", 100.0, 50.0, "RAW-A"),
        StockSpace(
            "RAW-B", "TEST", "raw", "open", 100.0, 50.0, "RAW-B"),
        StockSpace(
            "WIP", "TEST", "wash", "closed", 100.0, 0.0, "PRODUCT"),
    ]
    sc.machines = [
        Machine(
            "FEED-A", "TEST", "reclaimer", 0.0, 50.0,
            reachable_spaces=["RAW-A"]),
        Machine(
            "FEED-B", "TEST", "reclaimer", 0.0, 50.0,
            reachable_spaces=["RAW-B"]),
    ]
    sc.nodes = [
        Node("SPACE:RAW-A", "TEST", "space", "Raw A"),
        Node("SPACE:RAW-B", "TEST", "space", "Raw B"),
        Node("SPACE:WIP", "TEST", "space", "WIP"),
    ]
    sc.links = [
        Link("A-WASH", "SPACE:RAW-A", "SPACE:WIP", 50.0),
        Link("B-WASH", "SPACE:RAW-B", "SPACE:WIP", 50.0),
    ]
    sc.process_transfers = [
        ProcessTransfer(
            "WASH-A", "FEED-A", "RAW-A", "WIP", ["A-WASH"],
            operation_kind="reclaim", process_stage="WASH",
            output_grade="PRODUCT"),
        ProcessTransfer(
            "WASH-B", "FEED-B", "RAW-B", "WIP", ["B-WASH"],
            operation_kind="reclaim", process_stage="WASH",
            output_grade="PRODUCT"),
    ]
    sc.destinations = [Destination(
        "WASH-FEED", "TEST", "SPACE:WIP",
        requested_grade="RAW-A", demand_kind="process_feed",
        process_stage="WASH")]
    sc.demand = [
        DemandEntry(
            "WASH-FEED", bucket,
            10.0 if bucket == 0 else 20.0 if bucket == 24 else 0.0,
            requested_grade="RAW-A" if bucket < 24 else "RAW-B")
        for bucket in range(48)
    ]
    return sc


def test_s0_is_seven_days_of_hourly_grade_aware_demand():
    scenario = datasets.end_to_end()

    assert scenario.sim.horizon_buckets == 168
    assert scenario.sim.dt_h == pytest.approx(1.0)
    assert scenario.sim.effective_horizon_minutes() == 7 * 24 * 60
    assert scenario.sim.mixed_resolution_enabled is False
    assert len(scenario.demand) == len(scenario.destinations) * 168

    pap_daily = [
        sum(scenario.demand_tph("PAP-1", bucket)
            for bucket in range(day * 24, (day + 1) * 24))
        for day in range(7)
    ]
    assert pap_daily == pytest.approx(
        [36_000, 32_400, 39_600, 36_000, 34_200, 37_800, 36_000])
    assert [scenario.demand_grade("PAP-1", day * 24)
            for day in range(7)] == ["G10"] * 7

    # The restored finite-interface calendar runs one Louta campaign day.
    louta_daily = [sum(scenario.demand_tph("LOU-WASH-L1", bucket)
                        for bucket in range(day * 24, (day + 1) * 24))
                    for day in range(7)]
    assert louta_daily == pytest.approx([5_864, 0, 0, 0, 0, 0, 0])


def test_daily_delivery_grade_selects_only_matching_stock():
    scenario = _two_grade_delivery()
    plan = solve_plan(scenario, SolveOptions(max_time_s=0.5))

    assert plan.status in {"OPTIMAL", "FEASIBLE"}
    assert sum(plan.unmet["PLANT"]) == pytest.approx(0.0)
    moved = {}
    for job in plan.jobs:
        moved[job.space] = moved.get(job.space, 0.0) + job.tonnage_t
    assert moved == pytest.approx({"PILE-A": 10.0, "PILE-B": 20.0})


def test_two_delivery_grades_can_be_requested_by_one_unit_on_the_same_day():
    scenario = _two_grade_delivery()
    scenario.sim.horizon_buckets = 24
    scenario.sim.horizon_minutes = 1_440
    scenario.demand = [
        DemandEntry(
            "PLANT", bucket,
            10.0 if bucket == 0 else 20.0 if bucket == 12 else 0.0,
            requested_grade="A" if bucket < 12 else "B")
        for bucket in range(24)
    ]

    plan = solve_plan(scenario, SolveOptions(max_time_s=0.5))

    assert plan.status in {"OPTIMAL", "FEASIBLE"}
    assert sum(plan.unmet["PLANT"]) == pytest.approx(0.0)
    moved = {}
    for job in plan.jobs:
        moved[job.space] = moved.get(job.space, 0.0) + job.tonnage_t
    assert moved == pytest.approx({"PILE-A": 10.0, "PILE-B": 20.0})


def test_two_process_feed_grades_can_run_for_one_unit_on_the_same_day():
    scenario = _two_grade_process_feed()
    scenario.sim.horizon_buckets = 24
    scenario.sim.horizon_minutes = 1_440
    scenario.demand = [
        DemandEntry(
            "WASH-FEED", bucket,
            10.0 if bucket == 0 else 20.0 if bucket == 12 else 0.0,
            requested_grade="RAW-A" if bucket < 12 else "RAW-B")
        for bucket in range(24)
    ]

    plan = solve_plan(scenario, SolveOptions(max_time_s=0.5))

    assert plan.status in {"OPTIMAL", "FEASIBLE"}
    assert sum(plan.unmet["WASH-FEED"]) == pytest.approx(0.0)
    moved = {}
    for job in plan.jobs:
        if job.transfer_id:
            moved[job.source_space] = (
                moved.get(job.source_space, 0.0) + job.tonnage_t)
    assert moved == pytest.approx({"RAW-A": 10.0, "RAW-B": 20.0})


def test_legacy_destination_grade_remains_an_output_label_for_blending():
    scenario = _two_grade_delivery()
    scenario.sim.horizon_buckets = 1
    scenario.sim.horizon_minutes = 60
    scenario.demand = []
    scenario.destination("PLANT").demand_default_tph = 10.0
    scenario.space("PILE-A").mass_t = 0.0

    plan = solve_plan(scenario, SolveOptions(max_time_s=0.5))

    assert plan.status in {"OPTIMAL", "FEASIBLE"}
    assert sum(plan.unmet["PLANT"]) == pytest.approx(0.0)
    assert sum(job.tonnage_t for job in plan.jobs
               if job.space == "PILE-B") == pytest.approx(10.0)


def test_process_feed_is_exact_within_each_day_and_uses_that_days_grade():
    scenario = _two_grade_process_feed()
    plan = solve_plan(scenario, SolveOptions(max_time_s=0.5))

    assert plan.status in {"OPTIMAL", "FEASIBLE"}
    assert sum(plan.unmet["WASH-FEED"]) == pytest.approx(0.0)
    moved = {}
    for job in plan.jobs:
        if job.transfer_id:
            moved[job.source_space] = (
                moved.get(job.source_space, 0.0) + job.tonnage_t)
    assert moved == pytest.approx({"RAW-A": 10.0, "RAW-B": 20.0})
    assert all(
        job.end_bucket < 24 for job in plan.jobs
        if job.source_space == "RAW-A")
    assert all(
        job.start_bucket >= 24 for job in plan.jobs
        if job.source_space == "RAW-B")


def test_weekly_target_sync_matches_daily_integer_yield_and_clears_custom_c2():
    scenario = datasets.end_to_end()
    datasets.synchronize_weekly_demand_targets(scenario)

    assert scenario.config["CORRIDOR_DISPATCH_TARGET_T"]["C2"] == 0
    louta = scenario.config["LOU_WASH_LINE_OUTPUT_TARGET_T"]
    assert louta == pytest.approx({
        "LOU-WASH-L1": 4_000.0,
        "LOU-WASH-L2": 4_000.0,
    })
    assert scenario.config["CORRIDOR_DISPATCH_TARGET_T"]["C1"] == \
        pytest.approx(sum(louta.values()))
    assert scenario.config["PROCESS_YIELD_CARRY_WINDOW_BUCKETS"] == 24


def test_restored_full_flow_preserves_explicit_campaign_targets():
    scenario = datasets.end_to_end()
    datasets.apply_planning_demand_profile(
        scenario, datasets.DEFAULT_PLANNING_DEMAND_PROFILE)
    summary = scenario.config["DEMAND_DRIVER_SUMMARY"]

    active_mph_t = sum(
        scenario.demand_tph(destination_id, bucket) * scenario.sim.dt_h
        for destination_id in ("PAP-1", "PAP-2", "DRY-FEED")
        for bucket in range(scenario.sim.horizon_buckets)
    )
    corridors = scenario.config["CORRIDOR_DISPATCH_TARGET_T"]
    assert summary["active_mph_demand_t"] == pytest.approx(
        active_mph_t, abs=2.0)
    assert abs(summary["corridor_replenishment_t"] - (
        corridors["C1"] + corridors["C2"])) <= 2.0
    assert corridors["C1"] == pytest.approx(8_000.0)
    assert corridors["C2"] == pytest.approx(74_200.0)
    assert summary["bg_same_week_production_t"] == pytest.approx(16_200.0)
    assert summary["bg_train_product_t"] == pytest.approx(168_000.0)

    stages = scenario.config["PROCESS_STAGE_OUTPUT_TARGET_T"]
    assert stages["LOU-WASH-L1"] + stages["LOU-WASH-L2"] == \
        pytest.approx(corridors["C1"])
    assert sum(stages[f"BEN-WASH-L{line}"] for line in (1, 2, 3)) == \
        pytest.approx(summary["bg_same_week_production_t"], abs=3.0)


def test_restored_presets_and_default_search_budget():
    scenario = datasets.end_to_end()
    assert list(datasets.PLANNING_DEMAND_PROFILES) == [
        "BALANCED", "BG_TRAIN", "FULL_FLOW", "BLEND_SHOWCASE"]
    assert scenario.config["WEEKLY_TOTAL_WALL_TIME_S"] == 280.0
    assert scenario.config["WEEKLY_WINDOW_WALL_TIME_S"] == 40.0
    assert resolve_campaign_policy(
        scenario, "BG-MINE-ST-2").allow_short_parcel
    assert scenario.config["PARCEL_CONSOLIDATION_ENABLED"] is False
    assert scenario.config["MIN_JOB_VOLUME_T"] == 0.0
    assert scenario.config["OBJECTIVE_MODE"] == "service_first"
    assert scenario.config["PARCEL_CONSOLIDATION_WALL_TIME_S"] == 15.0
    assert scenario.config["PROCESS_STAGE_TARGET_SHORTFALL_MAX_T"] == {}

def test_capped_stage_target_shortfall_is_explicit_and_never_a_token_job():
    scenario = _two_grade_process_feed()
    scenario.config["PROCESS_STAGE_OUTPUT_TARGET_T"] = {"WASH": 25}
    scenario.config["PROCESS_STAGE_TARGET_SHORTFALL_MAX_T"] = {"WASH": 25}
    policy = default_campaign_policy_config()
    policy["default"]["min_parcel_t"] = 50.0
    policy["default"]["allow_short_parcel"] = False
    policy["by_machine_kind"] = {}
    scenario.config["CAMPAIGN_POLICY"] = policy

    assert not [issue for issue in validate_scenario(scenario)
                if issue.code == "BAD_PROCESS_STAGE_SHORTFALL"]
    result = solve_plan(scenario, SolveOptions(max_time_s=2))

    assert result.status == "PARTIAL"
    assert result.breakdown["process_stage_target_shortfall_t"] == 25
    assert result.breakdown["process_stage_target_shortfall_t[WASH]"] == 25
    assert result.breakdown["below_parcel_floor_slices"] == 0
    assert not result.jobs
    assert any("PROCESS_STAGE_TARGET_SHORTFALL[WASH]" in reason
               for reason in result.reasons)

    scenario.config["PROCESS_STAGE_TARGET_SHORTFALL_MAX_T"] = {"WASH": 26}
    assert "BAD_PROCESS_STAGE_SHORTFALL" in {
        issue.code for issue in validate_scenario(scenario)}


def test_restored_preset_ids_remain_distinct_and_integrated_aliases_full_flow():
    scenario = datasets.end_to_end()
    for profile_id in ("BALANCED", "BG_TRAIN", "FULL_FLOW", "BLEND_SHOWCASE"):
        datasets.apply_planning_demand_profile(scenario, profile_id)
        assert scenario.config["ACTIVE_PLANNING_DEMAND_PROFILE"] == profile_id
    datasets.apply_planning_demand_profile(scenario, "INTEGRATED_WEEKLY")
    assert scenario.config["ACTIVE_PLANNING_DEMAND_PROFILE"] == "FULL_FLOW"


def test_preset_rejects_invalid_allocation_share(monkeypatch):
    invalid = dict(datasets.PLANNING_DEMAND_PROFILES[
        datasets.DEFAULT_PLANNING_DEMAND_PROFILE])
    invalid["c2_share"] = 1.01
    monkeypatch.setitem(datasets.PLANNING_DEMAND_PROFILES, "INVALID", invalid)

    with pytest.raises(ValueError, match="c2_share"):
        datasets.apply_planning_demand_profile(
            datasets.end_to_end(), "INVALID")


def test_demand_grade_survives_json_resampling_and_legacy_payloads():
    scenario = _two_grade_delivery()
    restored = scenario_from_json(scenario_to_json(scenario))
    assert restored.demand_grade("PLANT", 0) == "A"
    assert restored.demand_grade("PLANT", 24) == "B"

    refined = resample_scenario_window(
        restored, window_start_minute=24 * 60,
        window_duration_min=60, step_min=15)
    assert {entry.requested_grade for entry in refined.demand} == {"B"}

    payload = json.loads(scenario_to_json(scenario))
    for entry in payload["demand"]:
        entry.pop("requested_grade", None)
    legacy = scenario_from_json(json.dumps(payload))
    assert legacy.demand_grade("PLANT", 24) == "A"


def test_hot_loaded_s0_migrates_from_24h_to_weekly_and_rebuilds_demand():
    scenario = datasets.end_to_end()
    scenario.sim.horizon_buckets = 24
    scenario.sim.horizon_minutes = 1_440
    scenario.config["BUILTIN_BASELINE_REVISION"] = "old-24-hour-revision"
    scenario.demand = [DemandEntry("PAP-1", 0, 1.0)]

    assert datasets.migrate_loaded_builtin(scenario) == "solver"
    assert scenario.sim.horizon_buckets == 168
    assert scenario.sim.horizon_minutes == 10_080
    assert len(scenario.demand) == len(scenario.destinations) * 168
    assert all(hasattr(entry, "requested_grade") for entry in scenario.demand)
