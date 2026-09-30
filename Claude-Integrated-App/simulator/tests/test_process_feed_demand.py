"""Fast regressions for washing-line feed demand semantics."""

from __future__ import annotations

import copy

import pytest

from mph_sim.domain import (
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


def _tiny_wash_scenario() -> Scenario:
    scenario = Scenario(
        name="Tiny washing feed",
        sim=SimParams(horizon_buckets=1, dt_h=1.0, seed=7, max_solve_s=0.2),
        config={"FLOW_TONNE_PENALTY": 1},
    )
    scenario.sites = [Site("TEST", "Test mine", "mine")]
    scenario.grades = [
        Grade("RAW-A", "Raw A"),
        Grade("RAW-B", "Raw B"),
        Grade("PRODUCT", "Washed product"),
    ]
    scenario.spaces = [
        StockSpace(
            "RAW-PILE", "TEST", "raw-yard", "open",
            capacity_t=1_000.0, mass_t=200.0, grade="RAW-A",
        ),
        StockSpace(
            "WASHED-WIP", "TEST", "wash", "closed",
            capacity_t=1_000.0, mass_t=0.0, grade="PRODUCT",
        ),
    ]
    scenario.machines = [
        Machine(
            "WASH-FEEDER", "TEST", "reclaimer",
            rate_min_tph=0.0,
            rate_max_tph=100.0,
            reachable_spaces=["RAW-PILE"],
        ),
    ]
    scenario.nodes = [
        Node("SPACE:RAW-PILE", "TEST", "space", "Raw pile"),
        Node("SPACE:WASHED-WIP", "TEST", "space", "Washed WIP"),
    ]
    scenario.links = [
        Link(
            "RAW-TO-WASH",
            "SPACE:RAW-PILE",
            "SPACE:WASHED-WIP",
            capacity_tph=100.0,
        ),
    ]
    scenario.process_transfers = [
        ProcessTransfer(
            "WASH-TRANSFER",
            machine="WASH-FEEDER",
            source_space="RAW-PILE",
            target_space="WASHED-WIP",
            path=["RAW-TO-WASH"],
            operation_kind="reclaim",
            process_stage="WASH-1",
            output_grade="PRODUCT",
            yield_factor=0.8,
        ),
    ]
    scenario.destinations = [
        Destination(
            "WASH-1-FEED",
            "TEST",
            "SPACE:WASHED-WIP",
            demand_default_tph=100.0,
            requested_grade="RAW-A",
            demand_kind="process_feed",
            process_stage="WASH-1",
        ),
    ]
    return scenario


def test_process_feed_uses_transfer_input_and_requested_source_grade():
    scenario = _tiny_wash_scenario()
    plan = solve_plan(scenario, SolveOptions(max_time_s=0.2))

    assert plan.status in {"OPTIMAL", "FEASIBLE"}
    assert sum(plan.delivered["WASH-1-FEED"]) == pytest.approx(100.0)
    assert sum(plan.unmet["WASH-1-FEED"]) == pytest.approx(0.0)

    process_jobs = [
        job for job in plan.jobs if job.transfer_id == "WASH-TRANSFER"
    ]
    assert len(process_jobs) == 1
    job = process_jobs[0]
    assert job.process_stage == "WASH-1"
    assert job.source_space == "RAW-PILE"
    assert job.target_space == "WASHED-WIP"
    assert job.tonnage_t == pytest.approx(100.0)
    assert job.output_tonnage_t == pytest.approx(80.0)

    wrong_grade = copy.deepcopy(scenario)
    wrong_grade.destination("WASH-1-FEED").requested_grade = "RAW-B"
    blocked = solve_plan(wrong_grade, SolveOptions(max_time_s=0.2))

    assert blocked.status in {"OPTIMAL", "FEASIBLE"}
    assert sum(blocked.delivered["WASH-1-FEED"]) == pytest.approx(0.0)
    assert sum(blocked.unmet["WASH-1-FEED"]) == pytest.approx(100.0)
    assert not [
        job for job in blocked.jobs if job.transfer_id == "WASH-TRANSFER"
    ]
