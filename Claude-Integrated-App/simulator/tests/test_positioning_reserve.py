"""The tactical plan reserves real time for changing physical work piles."""
import pytest

from mph_sim.domain import (DemandEntry, Destination, Grade, Link, Machine,
                            Node, ObjectiveWeights, Scenario, SimParams, Site,
                            StockSpace)
from mph_sim.optimizer import SolveOptions, solve_plan
from mph_sim.timing import validate_execution_windows


def _scenario(*, initial=False, same_pile=False, dt_h=1.0):
    horizon = 1 if initial else 2
    second_grade = "A" if same_pile else "B"
    demand = ([DemandEntry("D", 0, 60, requested_grade=second_grade)] if initial else [
        DemandEntry("D", 0, 60, requested_grade="A"),
        DemandEntry("D", 1, 60, requested_grade=second_grade)])
    return Scenario(
        "positioning reserve",
        sites=[Site("M", "Mine", "mine")],
        grades=[Grade("A", "A"), Grade("B", "B")],
        spaces=[
            StockSpace("A", "M", "yard", "open", 1000, 500, "A",
                       x_from=0, x_to=100, node_id="A"),
            StockSpace("B", "M", "yard", "open", 1000, 500, "B",
                       x_from=200, x_to=300, node_id="B")],
        machines=[Machine("RC", "M", "reclaimer", 0, 100, ["A", "B"],
                          travel_speed_m_min=100, setup_min=10)],
        nodes=[Node("A", "M", "space"), Node("B", "M", "space"),
               Node("D", "M", "dest")],
        links=[Link("A-D", "A", "D", 60), Link("B-D", "B", "D", 60)],
        destinations=[Destination("D", "M", "D")], demand=demand,
        weights=ObjectiveWeights(w4_giveaway=0, w5_moves=0, w6_safety=0),
        sim=SimParams(dt_h=dt_h, horizon_buckets=horizon,
                      horizon_minutes=round(horizon * dt_h * 60), max_solve_s=0.5),
        config={"MACHINE_INITIAL_SPACE": {"RC": "A"}} if initial else {},
    )


def test_adjacent_pile_change_reserves_bottleneck_time_and_validates():
    sc = _scenario()
    plan = solve_plan(sc, SolveOptions(max_time_s=0.5))
    assert plan.status in {"OPTIMAL", "FEASIBLE"}
    assert plan.delivered["D"][0] == pytest.approx(60)
    # Conservative work-midpoint range needs 13 min. The 60 tph conveyor,
    # rather than the 100 tph reclaimer, sets the remaining physical mass.
    assert plan.delivered["D"][1] == pytest.approx(47)
    assert all(w.status == "VALIDATED" for j in plan.jobs for w in j.execution_windows)
    assert not [issue for issue in validate_execution_windows(sc, plan.jobs)
                if issue.severity == "ERROR"]
    assert plan.breakdown["positioning_reservation_constraints"] > 0


def test_same_pile_continuation_retains_full_bottleneck_capacity():
    sc = _scenario(same_pile=True)
    plan = solve_plan(sc, SolveOptions(max_time_s=0.5))
    assert plan.delivered["D"] == pytest.approx([60, 60])


@pytest.mark.parametrize("dt_h,expected_t", [(1.0, 47), (0.25, 2)])
def test_carried_initial_position_reserves_time_in_first_bucket(dt_h, expected_t):
    sc = _scenario(initial=True, dt_h=dt_h)
    plan = solve_plan(sc, SolveOptions(max_time_s=0.5))
    assert plan.status in {"OPTIMAL", "FEASIBLE"}
    assert plan.delivered["D"][0] * dt_h == pytest.approx(expected_t)
    assert all(w.status == "VALIDATED" for j in plan.jobs for w in j.execution_windows)


def test_initial_position_same_as_work_space_has_no_fabricated_setup():
    sc = _scenario(initial=True, same_pile=True)
    plan = solve_plan(sc, SolveOptions(max_time_s=0.5))
    assert plan.delivered["D"][0] == pytest.approx(60)
