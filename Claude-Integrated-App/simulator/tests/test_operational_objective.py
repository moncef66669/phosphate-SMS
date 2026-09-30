"""End-to-end contracts for the operational planner objective terms."""
from __future__ import annotations

import copy
import math
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim.domain import (  # noqa: E402
    CalendarEntry,
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
    StockSpace,
)
from mph_sim.optimizer import SolveOptions, solve_plan  # noqa: E402
from mph_sim.kpis import compute_kpis  # noqa: E402
from mph_sim.validation import validate_scenario  # noqa: E402


def _weights(*, unmet=1.0) -> ObjectiveWeights:
    return ObjectiveWeights(
        w1_unmet=unmet,
        w2_offspec=0,
        w4_giveaway=0,
        w5_moves=0,
        w6_safety=0,
        w7_utilisation=0,
    )


def _operational_config(**updates) -> dict[str, object]:
    values: dict[str, object] = {
        "CONTINUITY_GAP_MINUTE_PENALTY": 0,
        "CONTINUITY_EPISODE_PENALTY": 0,
        "ENERGY_OBJECTIVE_ENABLED": False,
        "ENERGY_OBJECTIVE_CENTIKWH_PENALTY": 0,
        "EQUIPMENT_START_PENALTY": 0,
        "EQUIPMENT_STOP_PENALTY": 0,
        "EQUIPMENT_CHANGEOVER_PENALTY": 0,
    }
    values.update(updates)
    return values


def _single_pile_scenario(
        *,
        horizon: int,
        inventory_t: float,
        demand: list[DemandEntry],
        machine_min_tph: float = 0,
        config: dict[str, object] | None = None,
) -> Scenario:
    return Scenario(
        "operational-objective-test",
        sites=[Site("S", "Test site", "hub")],
        grades=[Grade("A", "A")],
        spaces=[StockSpace(
            "P", "S", "yard", "open", 100, inventory_t, "A",
            designated_grade="A", x_from=0, x_to=100, node_id="P")],
        machines=[Machine(
            "R", "S", "reclaimer", machine_min_tph, 10, ["P"],
            travel_speed_m_min=100, setup_min=0)],
        nodes=[Node("P", "S", "space"), Node("D", "S", "dest")],
        links=[Link("P-D", "P", "D", 10)],
        destinations=[Destination("D", "S", "D")],
        demand=demand,
        weights=_weights(),
        sim=SimParams(
            dt_h=1, horizon_buckets=horizon,
            horizon_minutes=60 * horizon, max_solve_s=1),
        config=dict(config or {}),
    )


def _solve(scenario: Scenario):
    plan = solve_plan(scenario, SolveOptions(max_time_s=1))
    assert plan.status == "OPTIMAL", plan.reasons
    return plan


def test_optimal_whole_plan_exposes_consistent_solver_certificate():
    scenario = _single_pile_scenario(
        horizon=1,
        inventory_t=10,
        demand=[DemandEntry("D", 0, 10, requested_grade="A")],
        machine_min_tph=10,
        config=_operational_config(),
    )

    plan = _solve(scenario)

    assert plan.solver_optimality_scope == "whole_plan"
    assert math.isfinite(plan.objective)
    assert math.isfinite(plan.solver_best_bound)
    assert plan.solver_best_bound <= plan.objective + 1e-9
    assert plan.solver_best_bound == pytest.approx(plan.objective)
    assert plan.solver_relative_gap_pct == pytest.approx(0.0)
    assert plan.breakdown["solver_best_objective_bound"] == pytest.approx(
        plan.solver_best_bound)
    assert plan.breakdown["solver_relative_gap_pct"] == pytest.approx(
        plan.solver_relative_gap_pct)


def test_continuity_episode_term_changes_equal_tonnage_gap_pattern():
    """Equal unmet tonnes prefer one continuous outage over two episodes."""
    demand = [
        DemandEntry("D", 0, 10, requested_grade="A", priority=2),
        DemandEntry("D", 1, 10, requested_grade="A", priority=1),
        DemandEntry("D", 2, 10, requested_grade="A", priority=2),
        DemandEntry("D", 3, 10, requested_grade="A", priority=1),
    ]
    config = _operational_config()
    config["CONTINUITY_POLICIES"] = {
        "D": {
            "mode": "continuous_feed",
            "minimum_tph": 10,
            "max_gap_min": 0,
        }
    }
    scenario = _single_pile_scenario(
        horizon=4, inventory_t=20, demand=demand,
        machine_min_tph=10, config=config)

    priority_only = _solve(copy.deepcopy(scenario))
    assert priority_only.delivered["D"] == pytest.approx([10, 0, 10, 0])
    assert priority_only.feed_gap["D"] == [False, True, False, True]
    assert priority_only.feed_interruption_start["D"] == [False, True, False, True]
    assert priority_only.breakdown["continuous_feed_interruption_count"] == 2

    scenario.config["CONTINUITY_EPISODE_PENALTY"] = 1_000
    continuity = _solve(scenario)
    assert sum(continuity.delivered["D"]) == pytest.approx(20)
    assert sum(continuity.feed_gap["D"]) == 2
    assert sum(continuity.feed_interruption_start["D"]) == 1
    assert continuity.breakdown["continuous_feed_gap_minutes"] == 120
    assert continuity.breakdown["continuous_feed_interruption_count"] == 1
    assert continuity.breakdown["continuous_feed_max_episode_minutes"] == 120


def test_machine_state_metrics_count_start_changeover_and_stop_once_each():
    """idle->A, A->B and B->idle are three distinct physical transitions."""
    scenario = Scenario(
        "machine-state-test",
        sites=[Site("S", "Test site", "hub")],
        grades=[Grade("A", "A"), Grade("B", "B")],
        spaces=[
            StockSpace(
                "A", "S", "yard", "open", 100, 10, "A",
                designated_grade="A", x_from=0, x_to=100, node_id="A"),
            StockSpace(
                "B", "S", "yard", "open", 100, 10, "B",
                designated_grade="B", x_from=0, x_to=100, node_id="B"),
        ],
        machines=[Machine(
            "R", "S", "reclaimer", 0, 60, ["A", "B"],
            travel_speed_m_min=100, setup_min=0)],
        nodes=[
            Node("A", "S", "space"), Node("B", "S", "space"),
            Node("D", "S", "dest"),
        ],
        links=[Link("A-D", "A", "D", 60), Link("B-D", "B", "D", 60)],
        destinations=[Destination("D", "S", "D")],
        demand=[
            DemandEntry("D", 1, 10, requested_grade="A"),
            DemandEntry("D", 2, 10, requested_grade="B"),
        ],
        weights=_weights(),
        sim=SimParams(
            dt_h=1, horizon_buckets=4, horizon_minutes=240,
            max_solve_s=1),
        config=_operational_config(
            EQUIPMENT_START_PENALTY=1,
            EQUIPMENT_STOP_PENALTY=1,
            EQUIPMENT_CHANGEOVER_PENALTY=1),
    )

    plan = _solve(scenario)
    assert plan.delivered["D"] == pytest.approx([0, 10, 10, 0])
    assert plan.breakdown["machine_start_count"] == 1
    assert plan.breakdown["machine_changeover_count"] == 1
    assert plan.breakdown["machine_stop_count"] == 1
    # A->B is one changeover, not the old pair of A-off and B-on indicators.
    assert plan.breakdown["machine_state_transition_count"] == 3


def test_enabled_energy_prefers_short_route_without_sacrificing_service():
    scenario = _single_pile_scenario(
        horizon=1,
        inventory_t=10,
        demand=[DemandEntry("D", 0, 10, requested_grade="A")],
        machine_min_tph=10,
        config=_operational_config(
            ENERGY_OBJECTIVE_ENABLED=True,
            ENERGY_OBJECTIVE_CENTIKWH_PENALTY=1),
    )
    scenario.nodes.append(Node("MID", "S", "tower"))
    # Use the production demand-first baseline while testing the secondary
    # energy choice; energy must resolve the route tie, not purchase unmet mass.
    scenario.weights.w1_unmet = 1_000
    scenario.links = [
        Link("SHORT", "P", "D", 10),
        Link("LONG-1", "P", "MID", 10),
        Link("LONG-2", "MID", "D", 10),
    ]

    plan = _solve(scenario)

    assert plan.unmet["D"] == pytest.approx([0])
    assert plan.delivered["D"] == pytest.approx([10])
    [job] = plan.jobs
    assert [(flow.tonnage_t, flow.path) for flow in job.bucket_flows] == [
        (10, ["SHORT"])
    ]
    # Reclaim 0.15 + one link 0.04 = 0.19 kWh/t, over ten tonnes.
    assert plan.breakdown["work_link_energy_kwh"] == pytest.approx(1.9)


def test_initial_boundary_state_does_not_duplicate_start_or_gap_episode():
    config = _operational_config(CONTINUITY_EPISODE_PENALTY=100)
    config["CONTINUITY_POLICIES"] = {
        "D": {
            "mode": "continuous_feed",
            "minimum_tph": 10,
            "max_gap_min": 0,
        }
    }
    scenario = _single_pile_scenario(
        horizon=2, inventory_t=10,
        demand=[DemandEntry("D", 0, 10), DemandEntry("D", 1, 10)],
        machine_min_tph=10, config=config)
    scenario.calendars = [CalendarEntry("R", 0, 0, "opening outage")]

    plan = solve_plan(
        scenario,
        SolveOptions(
            max_time_s=1,
            initial_machine_groups={
                "R": ("reclaim", "R", "P", "D", "", "", "")},
            initial_feed_gap={"D": True},
            initial_open_feed_gap_minutes={"D": 60.0},
        ))

    assert plan.status == "OPTIMAL", plan.reasons
    assert plan.feed_gap["D"] == [1.0, 0.0]
    assert plan.feed_interruption_start["D"] == [0.0, 0.0]
    assert plan.breakdown["continuous_feed_interruption_count"] == 0
    assert plan.breakdown["continuous_feed_max_episode_minutes"] == 120
    # The machine was active before the outage, so t0 is a stop; its return in
    # t1 is one start, not a fresh horizon start plus another transition.
    assert plan.breakdown["machine_stop_count"] == 1
    assert plan.breakdown["machine_start_count"] == 1


def test_hard_max_gap_can_make_demand_infeasible_and_reason_is_explicit():
    config = _operational_config()
    config["CONTINUITY_POLICIES"] = {
        "D": {
            "mode": "continuous_feed",
            "minimum_tph": 10,
            "max_gap_min": 60,
        }
    }
    scenario = _single_pile_scenario(
        horizon=2, inventory_t=0,
        demand=[DemandEntry("D", 0, 10), DemandEntry("D", 1, 10)],
        machine_min_tph=10, config=config)

    plan = solve_plan(scenario, SolveOptions(max_time_s=1))

    assert plan.status == "INFEASIBLE"
    assert any("max-gap" in reason for reason in plan.reasons)
    assert any("max_gap_min" in item for item in plan.relaxations)


def test_unsafe_energy_input_is_rejected_before_cp_sat_model_solve():
    scenario = _single_pile_scenario(
        horizon=1, inventory_t=10,
        demand=[DemandEntry("D", 0, 10)],
        config=_operational_config(
            ENERGY_OBJECTIVE_ENABLED=True,
            ENERGY_OBJECTIVE_CENTIKWH_PENALTY=1))
    scenario.config["ENERGY_LINK_KWH_PER_T"] = 1e20

    plan = solve_plan(scenario, SolveOptions(max_time_s=1))

    assert plan.status == "ERROR"
    assert any("must not exceed" in reason for reason in plan.reasons)
    assert any(
        issue.code == "BAD_ENERGY_OBJECTIVE"
        and "must not exceed" in issue.message
        for issue in validate_scenario(scenario))


def test_fractional_rate_has_one_gap_definition_in_solver_and_kpis():
    config = _operational_config()
    config["CONTINUITY_POLICIES"] = {
        "D": {
            "mode": "continuous_feed",
            "minimum_tph": 10.5,
            "max_gap_min": 0,
        }
    }
    scenario = _single_pile_scenario(
        horizon=1, inventory_t=10,
        demand=[DemandEntry("D", 0, 10.5)],
        machine_min_tph=10, config=config)

    plan = _solve(scenario)
    kpis = compute_kpis(scenario, plan)

    assert plan.delivered["D"] == [10.0]
    assert plan.feed_gap["D"] == [0.0]
    assert plan.breakdown["continuous_feed_gap_minutes"] == 0
    assert kpis["continuous_feed_gap_minutes"] == 0


@pytest.mark.parametrize("bad_minutes", [float("nan"), float("inf"), -1, "oops"])
def test_bad_initial_open_gap_state_returns_typed_error(bad_minutes):
    config = _operational_config()
    config["CONTINUITY_POLICIES"] = {
        "D": {
            "mode": "continuous_feed",
            "minimum_tph": 10,
            "max_gap_min": 60,
        }
    }
    scenario = _single_pile_scenario(
        horizon=1, inventory_t=10,
        demand=[DemandEntry("D", 0, 10)], config=config)

    plan = solve_plan(
        scenario,
        SolveOptions(
            initial_feed_gap={"D": True},
            initial_open_feed_gap_minutes={"D": bad_minutes}))

    assert plan.status == "ERROR"
    assert plan.reasons[0].startswith("BAD_INITIAL_OBJECTIVE_STATE")
