"""Demand-driven blends preserve one physical mixture through execution."""
import copy

import pytest

from mph_sim import washing
from mph_sim.blending import CHEVRON_CONFIG_KEY, chevron_campaign_state
from mph_sim.domain import (BeltProfileEntry, Destination, Job, Link, Machine,
                            Node, PlanResult, SimEvent)
from mph_sim.optimizer import Cand
from mph_sim.routes import Path
from mph_sim.simulator import Simulator
from test_simulator_chevron_quality import _stacking_scenario, _stack_jobs


def _dynamic_replay(*, outage=False):
    scenario = _stacking_scenario()
    scenario.sim.horizon_buckets = 3
    scenario.sim.horizon_minutes = 180
    recipe = [
        {"grade_id": "A", "target_fraction": 0.5, "tonnage_t": 100},
        {"grade_id": "B", "target_fraction": 0.5, "tonnage_t": 100},
    ]
    scenario.config[CHEVRON_CONFIG_KEY] = {
        "model_version": 7, "quantity_mode": "DEMAND_DRIVEN",
        "enabled": True, "enabled_spaces": ["PILE"],
        "global_mode": "MATRIX", "min_input_grade_t": 100,
        "matrix_catalog": [{"id": "AB", "name": "AB", "components": recipe}],
        "campaign_states_by_space": {},
    }
    scenario.machines.append(Machine("RC", "MPH", "reclaimer", 0, 100, ["PILE"]))
    scenario.nodes.append(Node("DEST", "MPH", "dest"))
    scenario.links.append(Link("OUT", "PILE-NODE", "DEST", 100))
    scenario.destinations.append(Destination("D", "MPH", "DEST", demand_default_tph=100))
    jobs = _stack_jobs("chevron", "blend matrix")
    for job in jobs:
        job.blend_recipe = copy.deepcopy(recipe)
    jobs.append(Job(
        "ZZ-RECLAIM", "RC", "reclaim", "PILE", dest_id="D", grade="BLEND-PILE",
        rate_tph=100, tonnage_t=100, start_bucket=2, end_bucket=2, path=["OUT"]))
    plan = PlanResult("FEASIBLE", jobs=jobs, blend_campaigns={"PILE": {
        "composition": recipe, "batch_t": 200,
        "ready_minute": 999, "result_grade": "BLEND-PILE", "selected_matrix": "AB",
    }})
    events = [SimEvent("OUTAGE", "machine_failure", 1, "ST")] if outage else []
    return Simulator(scenario, plan, events=events, auto_replan=False)


def test_selected_variable_quantity_is_carried_without_marking_it_constructed():
    replay = _dynamic_replay()
    config = replay.sc.config[CHEVRON_CONFIG_KEY]
    assert config["proposed_campaigns_by_space"]["PILE"]["batch_t"] == 200
    assert config["proposed_matrix_by_space"]["PILE"] == "AB"
    assert chevron_campaign_state(replay.sc, "PILE")["status"] == "NOT_STARTED"
    replay.run_to_end()
    # Physical completion at minute 120 releases the blend immediately;
    # the former future-ready planning timestamp does not impose a hold.
    assert sum(replay.delivered["D"]) == pytest.approx(100)
    assert chevron_campaign_state(replay.sc, "PILE")["status"] == "COMPLETE"


def test_partial_variable_quantity_does_not_release_reclaim():
    replay = _dynamic_replay(outage=True)
    replay.run_to_end()
    assert sum(replay.delivered["D"]) == 0
    assert replay.inv["PILE"] == pytest.approx(100)
    assert chevron_campaign_state(replay.sc, "PILE")["status"] == "BUILDING"


def test_accepting_replan_preserves_the_executed_recipe_and_selected_total():
    replay = _dynamic_replay(outage=True)
    replay.run_to_end()
    state_before = copy.deepcopy(chevron_campaign_state(replay.sc, "PILE"))
    proposal_before = copy.deepcopy(
        replay.sc.config[CHEVRON_CONFIG_KEY]["proposed_campaigns_by_space"]["PILE"])
    replay._adopt_blend_plan(PlanResult("FEASIBLE", blend_campaigns={"PILE": {
        "composition": [{"grade_id": "A", "target_fraction": 1, "tonnage_t": 1}],
        "batch_t": 1, "ready_minute": 0, "result_grade": "A",
    }}))
    assert chevron_campaign_state(replay.sc, "PILE") == state_before
    assert replay.sc.config[CHEVRON_CONFIG_KEY]["proposed_campaigns_by_space"]["PILE"] == proposal_before


def test_washing_expansion_keeps_dynamic_blend_as_one_mixture_per_route():
    replay = _dynamic_replay()
    candidate = Cand("reclaim", "RC", "PILE", dest_id="D", path=Path(["OUT"], 0, 100))
    _, expanded = washing.prepare_materials(replay.sc, [candidate])
    assert len(expanded) == 1
    assert expanded[0].grade == "BLEND-PILE"
    assert expanded[0].material_id == ""
    assert expanded[0].quality_override is None


def _sequential_replay(*, first_shortfall=False, identical_recipe=False,
                       first_reclaim_t=200):
    initial = _dynamic_replay()
    scenario = copy.deepcopy(initial.sc)
    scenario.sim.horizon_buckets = 6
    scenario.sim.horizon_minutes = 360
    scenario.config[CHEVRON_CONFIG_KEY]["min_input_grade_t"] = 50
    for machine in scenario.machines:
        machine.rate_max_tph = 200
    for link in scenario.links:
        link.capacity_tph = 200
    first = copy.deepcopy(initial.plan.blend_campaigns["PILE"])
    first.update(campaign_id="LOT-1", ready_minute=120)
    second = copy.deepcopy(first)
    second.update(campaign_id="LOT-2", ready_minute=300)
    if not identical_recipe:
        second["composition"] = [
            {"grade_id": "A", "target_fraction": 2 / 3, "tonnage_t": 100},
            {"grade_id": "B", "target_fraction": 1 / 3, "tonnage_t": 50},
        ]
        second.update(batch_t=150, total_t=150, result_grade="SECOND-ORDER")
    jobs = copy.deepcopy(initial.plan.jobs)
    for job in jobs:
        job.blend_campaign = copy.deepcopy(first)
    jobs[-1].rate_tph = jobs[-1].tonnage_t = first_reclaim_t
    for index, row in enumerate(second["composition"]):
        bucket = 3 + index
        scenario.belt_profile.append(BeltProfileEntry(
            "BELT", bucket, row["tonnage_t"], row["grade_id"]))
        job = Job(f"LOT2-{index}", "ST", "stack", "PILE", grade=row["grade_id"],
                  rate_tph=row["tonnage_t"], tonnage_t=row["tonnage_t"],
                  start_bucket=bucket, end_bucket=bucket, path=["BELT"],
                  method="chevron", blend_mode="blend matrix",
                  blend_recipe=copy.deepcopy(second["composition"]))
        job.blend_campaign = copy.deepcopy(second)
        jobs.append(job)
    reclaim = Job("LOT2-RECLAIM", "RC", "reclaim", "PILE", dest_id="D",
                  grade=second["result_grade"], rate_tph=200,
                  tonnage_t=second["batch_t"],
                  start_bucket=5, end_bucket=5, path=["OUT"])
    reclaim.blend_campaign = copy.deepcopy(second)
    jobs.append(reclaim)
    plan = PlanResult("FEASIBLE", jobs=jobs, blend_campaigns={"PILE": second})
    events = ([SimEvent("FIRST-OUTAGE", "machine_failure", 1, "ST",
                        duration_buckets=1)] if first_shortfall else [])
    return Simulator(scenario, plan, events=events, auto_replan=False)


def test_sequential_lots_reopen_empty_pile_and_keep_separate_actual_compositions():
    replay = _sequential_replay()
    replay.run_to_end()
    assert sum(replay.delivered["D"]) == pytest.approx(350)
    assert replay.inv["PILE"] == pytest.approx(0)
    current = chevron_campaign_state(replay.sc, "PILE")
    assert current["status"] == "DEPLETED"
    assert current["stacked_t_by_grade"] == {"A": 100, "B": 50}
    assert current["campaign_id"] == "LOT-2"
    assert current["result_grade"] == "SECOND-ORDER"
    assert current["batch_t"] == 150
    # Snapshotting revisits every job; first-lot tonnes must stay accounted.
    snapshot = replay.snapshot_scenario()
    assert chevron_campaign_state(snapshot, "PILE")["stacked_t_by_grade"] == {"A": 100, "B": 50}


@pytest.mark.parametrize("identical_recipe", [False, True])
def test_next_lot_cannot_replace_an_incomplete_previous_lot(identical_recipe):
    replay = _sequential_replay(first_shortfall=True, identical_recipe=identical_recipe)
    replay.run_to_end()
    assert sum(replay.delivered["D"]) == 0
    assert replay.inv["PILE"] == pytest.approx(100)
    assert replay.moved_by_job.get("LOT2-0", 0) == 0
    assert replay.moved_by_job.get("LOT2-1", 0) == 0
    assert chevron_campaign_state(replay.sc, "PILE")["campaign_id"] == "LOT-1"


def test_completed_but_nonempty_pile_holds_next_lot():
    replay = _sequential_replay(first_reclaim_t=100)
    replay.run_to_end()
    assert replay.moved_by_job.get("LOT2-0", 0) == 0
    assert replay.moved_by_job.get("LOT2-1", 0) == 0
    assert replay.moved_by_job.get("LOT2-RECLAIM", 0) == 0
    assert sum(replay.delivered["D"]) == pytest.approx(100)
