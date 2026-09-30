"""Acceptance tests for demand-driven MPH stockpile blending.

These routed fixtures keep the stockpile capacity and input-grade minimum
physical, while deliberately omitting a target campaign size or ready hour.
"""

from collections import defaultdict
from copy import deepcopy

import pytest

from mph_sim.blending import CHEVRON_CONFIG_KEY
from mph_sim.domain import (
    DemandEntry,
    Destination,
    Grade,
    Link,
    Machine,
    Node,
    ObjectiveWeights,
    Scenario,
    SimParams,
    Site,
    SpecRange,
    StockSpace,
)
from mph_sim.optimizer import SolveOptions, solve_plan


def _dynamic_blend_scenario(
        demand_t=6000, *, mode="OPTIMIZED", minimum_t=500,
        requested_grade="R", bpl_min=68, bpl_max=72):
    policy = {
        "model_version": 7,
        "quantity_mode": "DEMAND_DRIVEN",
        "enabled": True,
        "enabled_spaces": ["HP-07"],
        "global_mode": mode,
        "min_input_grade_t": minimum_t,
        "optimized_share_bounds": [
            {"grade_id": "A", "min_fraction": 0, "max_fraction": 1},
            {"grade_id": "B", "min_fraction": 0, "max_fraction": 1},
        ],
        "matrix_catalog": [{
            "id": "A25-B75", "name": "A 25 percent / B 75 percent",
            "components": [
                {"grade_id": "A", "target_fraction": .25},
                {"grade_id": "B", "target_fraction": .75},
            ],
        }],
        "campaign_states_by_space": {},
        "proposed_recipes_by_space": {},
        "proposed_campaigns_by_space": {},
        "proposed_matrix_by_space": {},
    }
    return Scenario(
        "demand-driven MPH blend acceptance",
        sites=[Site("M", "Mine", "mine"), Site("MPH", "MPH", "hub")],
        grades=[
            Grade("A", "A", {"BPL": 80}, material_class="washed",
                  product_moisture_pct=0),
            Grade("B", "B", {"BPL": 60}, material_class="washed",
                  product_moisture_pct=0),
            Grade("R", "Result R", {"BPL": 70}, material_class="washed",
                  product_moisture_pct=0),
            Grade("ALTERNATE", "Alternative result", {"BPL": 70},
                  material_class="washed", product_moisture_pct=0),
            Grade("STALE", "Legacy pile designation", {"BPL": 99},
                  material_class="washed", product_moisture_pct=0),
        ],
        spaces=[
            StockSpace("SRC-A", "M", "yard", "open", 10000, 10000, "A",
                       material_class="washed", node_id="SA"),
            StockSpace("SRC-B", "M", "yard", "open", 10000, 10000, "B",
                       material_class="washed", node_id="SB"),
            StockSpace("HP-07", "MPH", "M16-humid", "open", 10000, 0,
                       "STALE", state="empty", material_class="washed",
                       node_id="HP"),
        ],
        machines=[
            Machine("RA", "M", "reclaimer", 0, 10000, ["SRC-A"]),
            Machine("RB", "M", "reclaimer", 0, 10000, ["SRC-B"]),
            Machine("ST", "MPH", "stacker", 0, 10000, ["HP-07"]),
            Machine("RHP", "MPH", "reclaimer", 0, 10000, ["HP-07"]),
        ],
        nodes=[
            Node("SA", "M", "space"), Node("SB", "M", "space"),
            Node("HEAD", "M", "tower"), Node("REC", "MPH", "tower"),
            Node("HP", "MPH", "space"), Node("DEST", "MPH", "dest"),
        ],
        links=[
            Link("A-HEAD", "SA", "HEAD", 10000),
            Link("B-HEAD", "SB", "HEAD", 10000),
            Link("C1", "HEAD", "REC", 10000, transit_buckets=1),
            Link("REC-HP", "REC", "HP", 10000),
            Link("HP-DEST", "HP", "DEST", 10000),
        ],
        destinations=[Destination(
            "D", "MPH", "DEST", requested_grade=requested_grade,
            spec=[SpecRange("BPL", pmin=bpl_min, pmax=bpl_max, hard=True)])],
        demand=[DemandEntry("D", 7, demand_t)] if demand_t else [],
        weights=ObjectiveWeights(w4_giveaway=0, w5_moves=0),
        sim=SimParams(dt_h=1, horizon_buckets=8, horizon_minutes=480,
                      max_solve_s=10, seed=42),
        config={
            "ALLOW_CONTROLLED_FLOW_SPILL": False,
            "FLOW_TONNE_PENALTY": 1,
            CHEVRON_CONFIG_KEY: policy,
        },
    )


def _solve(scenario):
    result = solve_plan(
        scenario, SolveOptions(max_time_s=10, max_wall_time_s=20))
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.reasons
    return result


def _stacked_by_grade(result):
    tonnes = defaultdict(float)
    for job in result.jobs:
        if job.kind == "stack" and job.space == "HP-07":
            tonnes[job.grade] += job.tonnage_t
    return dict(tonnes)


def test_enabled_stockpile_without_demand_does_not_force_a_blend():
    result = _solve(_dynamic_blend_scenario(demand_t=0))

    assert _stacked_by_grade(result) == {}
    assert not result.blend_campaigns
    assert result.inventory["HP-07"][-1] == 0
    assert not any(job.kind == "dispatch" for job in result.jobs)


@pytest.mark.parametrize("washing_enabled", [False, True])
def test_occupied_blend_opt_in_preserves_real_opening_stock_delivery(washing_enabled):
    from mph_sim import washing
    from mph_sim.simulator import Simulator

    scenario = _dynamic_blend_scenario(demand_t=1000)
    pile = scenario.space("HP-07")
    pile.grade, pile.mass_t, pile.state = "R", 1000, "available"
    if washing_enabled:
        for grade in scenario.grades:
            grade.quality["moisture"] = 0
        config = washing.defaults()
        config["enabled"] = True
        scenario = washing.stage_configuration(scenario, config)
    result = _solve(scenario)

    assert sum(result.delivered["D"]) == 1000
    assert not result.blend_campaigns
    assert not _stacked_by_grade(result)
    assert all(job.method != "chevron" for job in result.jobs)
    assert result.inventory["HP-07"][-1] == 0
    sim = Simulator(scenario, result, auto_replan=False)
    sim.run_to_end()
    assert sum(sim.delivered["D"]) == pytest.approx(1000)
    assert sim.inv["HP-07"] == pytest.approx(0)


def test_blended_quantity_changes_with_demand_and_preserves_input_policy():
    totals = []
    for demand_t in (2000, 6000):
        scenario = _dynamic_blend_scenario(demand_t=demand_t)
        original_policy = deepcopy(scenario.config[CHEVRON_CONFIG_KEY])
        result = _solve(scenario)
        stacked = _stacked_by_grade(result)

        assert sum(result.delivered["D"]) == demand_t
        assert sum(result.unmet["D"]) == 0
        assert len(stacked) >= 2
        assert all(tonnes >= 500 for tonnes in stacked.values())
        assert sum(stacked.values()) == demand_t
        assert result.inventory["HP-07"][-1] == 0
        assert scenario.config[CHEVRON_CONFIG_KEY] == original_policy
        totals.append(sum(stacked.values()))

    assert totals == [2000, 6000]


@pytest.mark.parametrize("minimum_t", [500, 1500])
def test_adjustable_minimum_is_enforced_for_each_selected_input_grade(minimum_t):
    scenario = _dynamic_blend_scenario(
        demand_t=6000, minimum_t=minimum_t, bpl_min=60, bpl_max=80)
    result = _solve(scenario)
    stacked = _stacked_by_grade(result)

    assert len(stacked) == 2
    assert sum(stacked.values()) == 6000
    assert all(tonnes >= minimum_t for tonnes in stacked.values())
    recorded = {row["grade_id"]: row["tonnage_t"]
                for row in result.blend_campaigns["HP-07"]["composition"]}
    assert recorded == stacked


def test_reclaim_waits_for_final_actual_stack_instead_of_fixed_ready_hour():
    scenario = _dynamic_blend_scenario()
    # A retired setting must not defer the blend outside this eight-hour plan.
    scenario.config[CHEVRON_CONFIG_KEY]["global_ready_minute"] = 2880
    result = _solve(scenario)
    stack_buckets = [
        flow.bucket for job in result.jobs
        if job.kind == "stack" and job.space == "HP-07"
        for flow in job.bucket_flows if flow.tonnage_t > 0]
    reclaim_buckets = [
        flow.bucket for job in result.jobs
        if job.kind == "reclaim" and job.space == "HP-07"
        for flow in job.bucket_flows if flow.tonnage_t > 0]

    assert stack_buckets and reclaim_buckets
    assert min(reclaim_buckets) > max(stack_buckets)
    assert result.blend_campaigns["HP-07"]["ready_minute"] == (
        max(stack_buckets) + 1) * 60
    assert sum(result.delivered["D"]) == 6000


@pytest.mark.parametrize("requested_grade", ["R", "ALTERNATE"])
def test_stockpile_can_serve_different_result_grades_with_actual_chemistry(
        requested_grade):
    scenario = _dynamic_blend_scenario(requested_grade=requested_grade)
    scenario.config[CHEVRON_CONFIG_KEY]["global_result_grade"] = "STALE"
    result = _solve(scenario)
    stacked = _stacked_by_grade(result)

    assert sum(result.delivered["D"]) == 6000
    assert sum(stacked.values()) == 6000
    bpl = (80 * stacked.get("A", 0) + 60 * stacked.get("B", 0)) / 6000
    assert 68 <= bpl <= 72
    campaign = result.blend_campaigns["HP-07"]
    assert campaign["result_grade"] == requested_grade
    assert campaign["resulting_quality"]["BPL"] == pytest.approx(bpl)


@pytest.mark.parametrize("demand_t", [2000, 6000])
def test_matrix_ratios_remain_exact_while_total_quantity_is_variable(demand_t):
    result = _solve(_dynamic_blend_scenario(
        demand_t=demand_t, mode="MATRIX", bpl_min=65, bpl_max=65))

    assert sum(result.delivered["D"]) == demand_t
    assert _stacked_by_grade(result) == {
        "A": demand_t * .25, "B": demand_t * .75}
    campaign = result.blend_campaigns["HP-07"]
    assert campaign["selected_matrix"] == "A25-B75"
    assert campaign["batch_t"] == demand_t
    assert campaign["resulting_quality"]["BPL"] == pytest.approx(65)


@pytest.mark.parametrize("reason", ["quality", "minimum_exceeds_capacity"])
def test_unavailable_blend_keeps_feasible_unmet_demand_instead_of_forced_campaign(
        reason):
    scenario = _dynamic_blend_scenario()
    if reason == "quality":
        scenario.destination("D").spec = [
            SpecRange("BPL", pmin=95, pmax=100, hard=True)]
    else:
        scenario.config[CHEVRON_CONFIG_KEY]["min_input_grade_t"] = 6000
    result = _solve(scenario)

    assert sum(result.delivered["D"]) == 0
    assert sum(result.unmet["D"]) == 6000
    assert _stacked_by_grade(result) == {}
    assert not result.blend_campaigns
