"""Adversarial model-validation probes for demand-driven MPH blending."""

from copy import deepcopy

import pytest

from mph_sim.blending import CHEVRON_CONFIG_KEY
from mph_sim.domain import DemandEntry, Destination, Grade, Link, Node, SpecRange
from mph_sim.kpis import compute_kpis
from mph_sim.optimizer import SolveOptions, solve_plan
from test_dynamic_blend_schedule import (
    _dynamic_blend_scenario,
    _solve,
    _stacked_by_grade,
)


def test_unused_receiver_with_incompatible_spec_does_not_constrain_used_receiver():
    scenario = _dynamic_blend_scenario(demand_t=6000)
    scenario.nodes.append(Node("DEST-BLOCKED", "MPH", "dest"))
    scenario.links.append(Link("HP-BLOCKED", "HP", "DEST-BLOCKED", 10000))
    scenario.destinations.append(Destination(
        "D-BLOCKED", "MPH", "DEST-BLOCKED", requested_grade="R",
        spec=[SpecRange("BPL", pmin=95, pmax=100, hard=True)]))

    result = _solve(scenario)

    assert sum(result.delivered["D"]) == 6000
    assert sum(result.delivered["D-BLOCKED"]) == 0
    assert _stacked_by_grade(result)
    checks = result.blend_campaigns["HP-07"]["receiving_checks"]
    assert {row["destination"] for row in checks} == {"D"}


def test_unserved_incompatible_campaign_group_is_not_reported_as_certified():
    scenario = _dynamic_blend_scenario(demand_t=0)
    scenario.grades.append(Grade(
        "IMPOSSIBLE", "Impossible receiving profile", {},
        material_class="washed", is_specification=True,
        receiving_spec=[
            SpecRange("BPL", pmin=95, pmax=100, hard=True)]))
    scenario.demand = [
        DemandEntry("D", 6, 2000, "IMPOSSIBLE"),
        DemandEntry("D", 7, 2000, "R"),
    ]

    result = _solve(scenario)

    assert result.delivered["D"][6] == 0
    assert result.unmet["D"][6] == 2000
    assert result.delivered["D"][7] == 2000
    checks = result.blend_campaigns["HP-07"]["receiving_checks"]
    assert checks
    assert {row["requested_grade"] for row in checks} == {"R"}
    assert all(row["on_spec"] for row in checks)


def test_mixed_moisture_uses_dry_solids_for_bpl_and_wet_mass_for_moisture():
    scenario = _dynamic_blend_scenario(
        demand_t=2000, mode="MATRIX", bpl_min=66, bpl_max=66)
    scenario.grade("A").quality["moisture"] = 10
    scenario.grade("A").product_moisture_pct = 10
    scenario.grade("B").quality["moisture"] = 30
    scenario.grade("B").product_moisture_pct = 30
    scenario.destination("D").spec.append(
        SpecRange("moisture", pmin=25, pmax=25, hard=True))

    result = _solve(scenario)
    record = result.blend_campaigns["HP-07"]

    assert _stacked_by_grade(result) == {"A": 500, "B": 1500}
    assert record["resulting_quality"]["BPL"] == pytest.approx(66)
    assert record["resulting_quality"]["moisture"] == pytest.approx(25)
    assert sum(_stacked_by_grade(result).values()) == pytest.approx(
        sum(result.delivered["D"]))


def test_requested_grade_without_spec_is_reported_as_unspecified_not_on_spec():
    scenario = _dynamic_blend_scenario(demand_t=2000)
    scenario.destination("D").spec = []

    result = _solve(scenario)
    metrics = compute_kpis(scenario, result)

    assert sum(result.delivered["D"]) == 2000
    assert result.blend_campaigns["HP-07"]["receiving_checks"] == []
    assert metrics["quality_specified_demand_t"] == 0
    assert metrics["quality_unspecified_demand_t"] == 2000
    assert metrics["final_product_on_spec_on_time_t"] == 0
    assert metrics["on_spec_mass_t"] == 0
    assert metrics["on_spec_mass_pct"] is None


def test_zero_dry_solids_cannot_satisfy_a_chemistry_spec_vacuously():
    scenario = _dynamic_blend_scenario(demand_t=2000)
    for grade_id in ("A", "B"):
        scenario.grade(grade_id).quality["moisture"] = 100
        scenario.grade(grade_id).product_moisture_pct = 100

    result = _solve(scenario)

    assert sum(result.delivered["D"]) == 0
    assert sum(result.unmet["D"]) == 2000
    assert _stacked_by_grade(result) == {}


def test_physical_small_component_survives_later_minimum_floor_change():
    scenario = _dynamic_blend_scenario(
        demand_t=1000, minimum_t=500, bpl_min=62, bpl_max=62)
    recipe = [
        {"grade_id": "A", "target_fraction": 0.1, "tonnage_t": 100},
        {"grade_id": "B", "target_fraction": 0.9, "tonnage_t": 900},
    ]
    scenario.space("HP-07").mass_t = 100
    scenario.config[CHEVRON_CONFIG_KEY]["campaign_states_by_space"] = {
        "HP-07": {
            "status": "BUILDING",
            "stacked_t_by_grade": {"A": 100},
            "resolved_recipe": deepcopy(recipe),
            "batch_t": 1000,
            "result_grade": "R",
        }
    }

    result = solve_plan(
        scenario, SolveOptions(max_time_s=10, max_wall_time_s=20))

    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.reasons
    assert sum(result.delivered["D"]) == 1000
    assert _stacked_by_grade(result) == {"B": 900}
    assert 100 + sum(_stacked_by_grade(result).values()) == pytest.approx(
        sum(result.delivered["D"]) + result.inventory["HP-07"][-1])
    assert result.blend_campaigns["HP-07"]["composition"] == recipe


@pytest.mark.parametrize("mixed_resolution", [False, True])
def test_drained_pile_can_start_a_new_blend_in_later_weekly_window(
        mixed_resolution):
    scenario = _dynamic_blend_scenario(demand_t=0)
    scenario.sim.horizon_buckets = 48
    scenario.sim.horizon_minutes = 48 * 60
    scenario.sim.mixed_resolution_enabled = mixed_resolution
    scenario.sim.near_term_step_min = 15
    scenario.sim.near_term_horizon_min = 240
    scenario.sim.far_term_step_min = 60
    scenario.demand = [
        DemandEntry("D", 7, 2000, "R"),
        DemandEntry("D", 31, 6000, "ALTERNATE"),
    ]
    scenario.config.update({
        "WEEKLY_ROLLING_SOLVE_ENABLED": True,
        "WEEKLY_TOTAL_WALL_TIME_S": 10,
        "WEEKLY_WINDOW_WALL_TIME_S": 5,
    })

    result = solve_plan(
        scenario, SolveOptions(max_time_s=2, max_wall_time_s=10))

    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.reasons
    assert sum(result.delivered["D"][:24]) == 2000
    assert sum(result.delivered["D"][24:]) == 6000
    assert sum(result.unmet["D"]) == 0
    record = result.blend_campaigns["HP-07"]
    history = record["campaign_history"]
    assert [row["batch_t"] for row in history] == [2000, 6000]
    assert [row["result_grade"] for row in history] == ["R", "ALTERNATE"]
    assert [row["served_grades"] for row in history] == [["R"], ["ALTERNATE"]]
    assert len({row["campaign_id"] for row in history}) == 2
    assert record["campaign_id"] == history[-1]["campaign_id"]
    if mixed_resolution:
        prefix_stacks = [
            job for job in result.jobs
            if job.kind == "stack" and job.start_minute is not None
            and job.start_minute < scenario.sim.near_term_horizon_min]
        assert prefix_stacks
        for job in prefix_stacks:
            assert job.blend_campaign["campaign_id"] == history[0]["campaign_id"]
            assert job.blend_campaign["batch_t"] == 2000
            assert job.blend_campaign["result_grade"] == "R"


def test_repeat_solves_are_composition_and_schedule_reproducible():
    scenario = _dynamic_blend_scenario(demand_t=2000)

    first = _solve(deepcopy(scenario))
    second = _solve(deepcopy(scenario))

    assert _stacked_by_grade(first) == _stacked_by_grade(second)
    assert first.delivered == second.delivered
    assert [
        (job.kind, job.machine, job.space, job.grade, job.start_bucket,
         job.end_bucket, job.tonnage_t)
        for job in first.jobs
    ] == [
        (job.kind, job.machine, job.space, job.grade, job.start_bucket,
         job.end_bucket, job.tonnage_t)
        for job in second.jobs
    ]
