"""Physical and assay regressions from the September project review."""
import copy

import pytest

from mph_sim.blending import (CHEVRON_CONFIG_KEY, blended_quality_for_components,
                              chevron_blended_quality)
from mph_sim.domain import (DemandEntry, Destination, ExogenousFlow, Job, JobBucketFlow, JobExecutionWindow,
                            Link, Machine, Node, PlanResult, SimEvent, SpecRange)
from mph_sim.optimizer import SolveOptions, solve_plan
from mph_sim.simulator import Simulator
from mph_sim.validation import validate_scenario
from test_process_feed_demand import _tiny_wash_scenario
from test_simulator_chevron_quality import _stacking_scenario, _stack_jobs


def _recipe():
    return [{"grade_id": "A", "target_fraction": 0.5},
            {"grade_id": "B", "target_fraction": 0.5}]


def test_unassayed_component_does_not_certify_fixed_blend_contaminant():
    scenario = _stacking_scenario()
    del scenario.grade("B").quality["Cd"]
    quality = blended_quality_for_components(scenario, _recipe())
    assert "Cd" not in quality
    assert quality["BPL"] == pytest.approx(68.75)
    replay = Simulator(scenario, PlanResult("FEASIBLE", jobs=_stack_jobs("chevron")),
                       auto_replan=False)
    replay.run_to_end()
    assert "Cd" not in replay.space_quality["PILE"]


def test_fixed_matrix_quality_uses_exact_scheduled_integer_tonnes():
    replay = _constructed_pile_replay(outage=False)
    replay.sc.config[CHEVRON_CONFIG_KEY]["batch_t_by_space"]["PILE"] = 3
    # A 50/50 request is scheduled as 2 t A + 1 t B, not 1.5 t of each.
    quality = chevron_blended_quality(replay.sc, "PILE")
    assert quality["BPL"] == pytest.approx((2 * 0.9 * 60 + 0.7 * 80) / 2.5)


def test_actual_parcel_moisture_controls_executed_dry_solids_basis():
    scenario = _stacking_scenario()
    scenario.belt_profile[1].quality = dict(scenario.grade("B").quality,
                                          moisture=40.0)
    replay = Simulator(scenario, PlanResult("FEASIBLE", jobs=_stack_jobs("chevron")),
                       auto_replan=False)
    replay.run_to_end()
    assert replay.space_quality["PILE"]["BPL"] == pytest.approx(68.0)
    assert replay.space_quality["PILE"]["moisture"] == pytest.approx(25.0)


def test_carried_pile_live_assay_survives_simulator_initialization():
    scenario = _stacking_scenario(carried=True)
    scenario.space("PILE").quality = {"BPL": 55.0, "Cd": 50.0, "moisture": 20.0}
    replay = Simulator(scenario, PlanResult("FEASIBLE"), auto_replan=False)
    assert replay.space_quality["PILE"] == scenario.space("PILE").quality
    assert chevron_blended_quality(scenario, "PILE") == scenario.space("PILE").quality


def _constructed_pile_replay(*, outage: bool, reclaim_bucket: int = 2):
    scenario = _stacking_scenario()
    scenario.sim.horizon_buckets = 3
    scenario.sim.horizon_minutes = 180
    scenario.config[CHEVRON_CONFIG_KEY] = {
        "model_version": 5, "enabled": True, "enabled_spaces": ["PILE"],
        "recipes": {"PILE": _recipe()}, "result_grades": {"PILE": "A"},
        "batch_t_by_space": {"PILE": 200},
        "ready_minute_by_space": {"PILE": 120},
    }
    scenario.machines.append(Machine("RC", "MPH", "reclaimer", 0, 100, ["PILE"]))
    scenario.nodes.append(Node("DEST", "MPH", "dest"))
    scenario.links.append(Link("OUT", "PILE-NODE", "DEST", 100))
    scenario.destinations.append(Destination("D", "MPH", "DEST", demand_default_tph=100))
    jobs = _stack_jobs("chevron", "blend matrix")
    jobs.append(Job("ZZ-RECLAIM", "RC", "reclaim", "PILE", dest_id="D", grade="A",
                    rate_tph=100, tonnage_t=100, start_bucket=reclaim_bucket,
                    end_bucket=reclaim_bucket, path=["OUT"]))
    events = [SimEvent("OUTAGE", "machine_failure", 1, "ST")] if outage else []
    return Simulator(scenario, PlanResult("FEASIBLE", jobs=jobs),
                     events=events, auto_replan=False)


def test_interrupted_construction_holds_reclaim_after_planned_ready_time():
    replay = _constructed_pile_replay(outage=True)
    replay.run_to_end()
    assert replay.inv["PILE"] == pytest.approx(100)
    assert sum(replay.delivered["D"]) == 0
    assert any("construction incomplete" in str(row) for row in replay.activity_log)
    state = replay.sc.config[CHEVRON_CONFIG_KEY]["campaign_states_by_space"]["PILE"]
    assert state["status"] == "BUILDING"
    assert state["resolved_recipe"] == _recipe()


def test_completed_construction_releases_reclaim_but_never_same_interval_prefix():
    complete = _constructed_pile_replay(outage=False)
    complete.run_to_end()
    assert sum(complete.delivered["D"]) == pytest.approx(100)
    early = _constructed_pile_replay(outage=False, reclaim_bucket=1)
    early.run_to_end()
    assert sum(early.delivered["D"]) == 0


def test_measured_offspec_completed_blend_is_held_at_hard_receiving_gate():
    replay = _constructed_pile_replay(outage=False)
    replay.sc.destination("D").spec = [SpecRange("Cd", pmax=25, hard=True)]
    replay.events.append(SimEvent("ASSAY", "offspec_assay", 2, "PILE",
                                  params={"Cd": 50}))
    replay.run_to_end()
    assert sum(replay.delivered["D"]) == 0
    assert any("hard receiving interval" in str(row) for row in replay.activity_log)


def test_discarded_arrivals_are_counted_including_small_residuals():
    scenario = _stacking_scenario()
    scenario.belt_profile[1].mass_t = 0.5
    replay = Simulator(scenario, PlanResult("FEASIBLE"), auto_replan=False)
    replay.run_to_end()
    assert replay.unstacked_t == pytest.approx(100.5)


def test_process_feed_actuals_credit_input_mass_and_recovered_inventory():
    scenario = _tiny_wash_scenario()
    plan = solve_plan(scenario, SolveOptions(max_time_s=0.2))
    assert plan.status in {"FEASIBLE", "OPTIMAL"}
    replay = Simulator(scenario, plan, auto_replan=False)
    replay.run_to_end()
    assert sum(replay.delivered["WASH-1-FEED"]) == pytest.approx(100)
    assert replay.inv["RAW-PILE"] == pytest.approx(100)
    assert replay.inv["WASHED-WIP"] == pytest.approx(80)


def test_explicitly_rejected_minute_timing_is_not_executed_as_bucket_average():
    scenario = _tiny_wash_scenario()
    plan = solve_plan(scenario, SolveOptions(max_time_s=0.2))
    for job in plan.jobs:
        job.execution_windows = [JobExecutionWindow(
            0, 0, 0, 60, job.tonnage_t, job.rate_tph, status="BUCKET_ONLY")]
    replay = Simulator(scenario, plan, auto_replan=False)
    replay.run_to_end()
    assert sum(replay.delivered["WASH-1-FEED"]) == 0
    assert replay.inv["RAW-PILE"] == pytest.approx(200)
    assert any("dispatch held" in str(row) for row in replay.activity_log)


def test_exogenous_transfer_clips_before_source_is_drained():
    scenario = _tiny_wash_scenario()
    scenario.space("WASHED-WIP").capacity_t = 10
    scenario.exogenous = [ExogenousFlow("TRANSFER", "WASHED-WIP", 100,
                                       "RAW-A", source_space="RAW-PILE")]
    replay = Simulator(scenario, PlanResult("FEASIBLE"), auto_replan=False)
    replay.run_to_end()
    assert replay.inv["RAW-PILE"] == pytest.approx(190)
    assert replay.inv["WASHED-WIP"] == pytest.approx(10)
    assert sum(replay.inv.values()) == pytest.approx(200)


@pytest.mark.parametrize("hard", [True, False])
def test_unknown_contaminant_cannot_be_treated_as_zero_in_optimizer(hard):
    scenario = _tiny_wash_scenario()
    scenario.grade("RAW-A").quality = {"BPL": 60}
    scenario.destinations[0].spec = [SpecRange("Cd", pmax=25, hard=hard)]
    plan = solve_plan(scenario, SolveOptions(max_time_s=0.2))
    assert plan.status in {"FEASIBLE", "OPTIMAL"}
    assert sum(plan.delivered["WASH-1-FEED"]) == 0
    assert sum(plan.unmet["WASH-1-FEED"]) == pytest.approx(100)
    assert any("UNKNOWN_ASSAY" in reason for reason in plan.reasons)


def test_sparse_exact_flow_has_zero_mass_in_unscheduled_bucket():
    replay = Simulator(_tiny_wash_scenario(), PlanResult("FEASIBLE"), auto_replan=False)
    job = Job("J", "WASH-FEEDER", "reclaim", "RAW-PILE", rate_tph=50,
              start_bucket=0, end_bucket=2,
              bucket_flows=[JobBucketFlow(0, 50), JobBucketFlow(2, 100)])
    assert replay._planned_bucket_mass(job, 1) == 0


def test_margin_that_inverts_receiving_interval_is_rejected():
    scenario = _tiny_wash_scenario()
    scenario.destinations[0].spec = [SpecRange("BPL", pmin=65, pmax=67, margin=2)]
    assert any(issue.code == "BAD_SPEC_MARGIN" for issue in validate_scenario(scenario))


@pytest.mark.parametrize("dated_priority", [False, True, "reversed"])
def test_destination_priority_changes_allocation_of_scarce_input(dated_priority):
    scenario = _tiny_wash_scenario()
    # Two independent washing lines compete for one source inventory.
    scenario.space("RAW-PILE").mass_t = 100
    second_machine = copy.deepcopy(scenario.machines[0])
    second_machine.id = "OTHER-FEEDER"
    scenario.machines.append(second_machine)
    second_transfer = copy.deepcopy(scenario.process_transfers[0])
    second_transfer.id = "OTHER-TRANSFER"
    second_transfer.machine = second_machine.id
    second_transfer.process_stage = "WASH-2"
    scenario.process_transfers.append(second_transfer)
    second_destination = copy.deepcopy(scenario.destinations[0])
    second_destination.id = "PRIORITY-FEED"
    second_destination.process_stage = "WASH-2"
    second_destination.priority = 1 if dated_priority else 5
    scenario.destinations.append(second_destination)
    if dated_priority:
        first_priority, second_priority = ((9, 1) if dated_priority == "reversed"
                                           else (1, 9))
        scenario.demand = [DemandEntry("WASH-1-FEED", 0, 100, priority=first_priority),
                           DemandEntry("PRIORITY-FEED", 0, 100, priority=second_priority)]
    plan = solve_plan(scenario, SolveOptions(max_time_s=0.2))
    assert plan.status in {"FEASIBLE", "OPTIMAL"}
    preferred = "WASH-1-FEED" if dated_priority == "reversed" else "PRIORITY-FEED"
    other = "PRIORITY-FEED" if dated_priority == "reversed" else "WASH-1-FEED"
    assert sum(plan.delivered[preferred]) == pytest.approx(100)
    assert sum(plan.delivered[other]) == 0
