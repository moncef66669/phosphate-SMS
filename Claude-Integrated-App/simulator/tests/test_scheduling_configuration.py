"""Model and UI contracts for protected objectives and optional run volume."""
from copy import deepcopy
import math

import pytest
from ortools.sat.python import cp_model

from mph_sim import datasets
from mph_sim.domain import (CalendarEntry, DemandEntry, Destination, FixedJob,
                            Grade, Link, Machine, Node, ObjectiveWeights,
                            ProcessTransfer, Scenario, SimParams, Site,
                            SpecRange, StockSpace)
from mph_sim.objective_config import operational_objective_errors, scheduling_values
from mph_sim.optimizer import SolveOptions, solve_plan
from mph_sim.scheduling_objective import refine_objectives
from ui.scheduling_configuration import stage_scheduling_configuration


def scenario(demands=(50, 50), **config):
    return Scenario(
        "scheduling-test", sites=[Site("S", "Test", "hub")],
        grades=[Grade("G", "G", quality={"BPL": 70})],
        spaces=[StockSpace("P", "S", "yard", "open", 1000, 500, "G", node_id="P")],
        machines=[Machine("R", "S", "reclaimer", 0, 50, ["P"], setup_min=0)],
        nodes=[Node("P", "S", "space"), Node("D", "S", "dest")],
        links=[Link("P-D", "P", "D", 50)],
        destinations=[Destination("D", "S", "D")],
        demand=[DemandEntry("D", t, mass) for t, mass in enumerate(demands)],
        sim=SimParams(horizon_buckets=len(demands), max_solve_s=1),
        weights=ObjectiveWeights(w4_giveaway=0, w6_safety=0),
        config={"OBJECTIVE_MODE": "service_first", **config})


def solve(sc):
    return solve_plan(sc, SolveOptions(max_time_s=1, max_wall_time_s=5))


def test_default_zero_has_no_hidden_minimum():
    sc = scenario((1,))
    assert scheduling_values(sc)["MIN_JOB_VOLUME_T"] == 0
    plan = solve(sc)
    assert plan.status == "OPTIMAL", plan.reasons
    assert sum(j.tonnage_t for j in plan.jobs) == 1
    assert plan.breakdown["minimum_job_volume_constraints"] == 0


def test_minimum_applies_to_complete_job_not_bucket_capacity():
    plan = solve(scenario(MIN_JOB_VOLUME_T=100))
    assert plan.status == "OPTIMAL", plan.reasons
    assert [j.tonnage_t for j in plan.jobs] == [100]
    assert len(plan.jobs[0].bucket_flows) == 2


def test_impossible_optional_minimum_reports_unmet_instead_of_clipping():
    plan = solve(scenario((50,), MIN_JOB_VOLUME_T=100))
    assert plan.status == "OPTIMAL", plan.reasons
    assert not plan.jobs
    assert plan.unmet["D"] == [50]


def test_minimum_cannot_span_an_outage():
    sc = scenario((50, 0, 50), MIN_JOB_VOLUME_T=100)
    sc.calendars = [CalendarEntry("R", 1, 1, "maintenance")]
    plan = solve(sc)
    assert plan.status == "OPTIMAL", plan.reasons
    assert not plan.jobs
    assert sum(plan.unmet["D"]) == 100


def test_fractional_floor_rounds_up_in_whole_tonne_contract():
    plan = solve(scenario((1,), MIN_JOB_VOLUME_T=1.1))
    assert not plan.jobs
    assert plan.unmet["D"] == [1]


def test_operator_fixed_one_tonne_job_conflicts_with_positive_minimum():
    sc = scenario((1,), MIN_JOB_VOLUME_T=10)
    sc.fixed_jobs = [FixedJob(id="FIXED", kind="reclaim", machine="R", space="P",
                              start_bucket=0, end_bucket=0, rate_tph=1, dest_id="D")]
    plan = solve(sc)
    assert plan.status == "INFEASIBLE", plan.reasons
    assert any("MIN_JOB_VOLUME_T" in reason for reason in plan.reasons)


def test_derate_splits_jobs_and_the_minimum_does_not_bridge_it():
    sc = scenario(MIN_JOB_VOLUME_T=100)
    # A ceiling change is an extraction/job boundary even if both hourly
    # allocations remain positive.
    sc.calendars = [CalendarEntry("R", 1, 1, "derate", action="RATE_CAP", rate_tph=40)]
    plan = solve(sc)
    assert not plan.jobs


def test_service_cannot_be_bought_by_large_machine_penalties():
    sc = scenario((1,), EQUIPMENT_START_PENALTY=9999)
    plan = solve(sc)
    assert sum(j.tonnage_t for j in plan.jobs) == 1
    assert plan.breakdown["priority_weighted_qualified_unmet_t"] == 0
    assert plan.solver_optimality_scope == "primary_emergency_and_qualified_service"


def test_explicit_service_allowance_can_avoid_standalone_one_tonne_start():
    sc = scenario((1,), SERVICE_DEGRADATION_T=1, EQUIPMENT_START_PENALTY=9999)
    plan = solve(sc)
    assert not plan.jobs
    assert plan.unmet["D"] == [1]
    assert any(r["name"] == "operations" and r["accepted"] for r in plan.objective_pass_records)


def test_offspec_physical_delivery_does_not_earn_qualified_service():
    sc = scenario((50,))
    sc.destinations[0].spec = [SpecRange("BPL", pmin=71, hard=False)]
    plan = solve(sc)
    assert plan.breakdown["priority_weighted_qualified_unmet_t"] == 50
    assert not plan.jobs, plan.reasons


def test_soft_offspec_does_not_remove_an_explicit_operator_commitment():
    sc = scenario((1,))
    sc.destinations[0].spec = [SpecRange("BPL", pmin=71, hard=False)]
    sc.fixed_jobs = [FixedJob(id="FIXED", kind="reclaim", machine="R", space="P",
                              start_bucket=0, end_bucket=0, rate_tph=1, dest_id="D")]
    plan = solve(sc)
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    assert sum(job.tonnage_t for job in plan.jobs) == 1
    assert plan.breakdown["priority_weighted_qualified_unmet_t"] == 1
    assert plan.breakdown["unqualified_dispatch_t"] == 1


@pytest.mark.parametrize("minimum", [-1, math.nan, math.inf, True, "bad"])
def test_bad_minimum_is_rejected_atomically(minimum):
    sc = scenario()
    before = deepcopy(sc)
    with pytest.raises(ValueError, match="MIN_JOB_VOLUME_T"):
        stage_scheduling_configuration(sc, {"MIN_JOB_VOLUME_T": minimum})
    assert sc == before
    sc.config["MIN_JOB_VOLUME_T"] = minimum
    assert any("MIN_JOB_VOLUME_T" in e for e in operational_objective_errors(sc))
    assert solve(sc).status == "ERROR"


def test_configuration_survives_preset_switch_and_source_is_unchanged():
    sc = datasets.end_to_end()
    assert scheduling_values(sc)["OBJECTIVE_MODE"] == "service_first"
    assert sc.config["PARCEL_CONSOLIDATION_ENABLED"] is False
    staged = stage_scheduling_configuration(sc, {"MIN_JOB_VOLUME_T": 100})
    datasets.apply_planning_demand_profile(staged, "BG_TRAIN")
    assert staged.config["MIN_JOB_VOLUME_T"] == 100
    assert sc.config["MIN_JOB_VOLUME_T"] == 0


def test_small_protected_solve_replays_under_a_fixed_seed():
    sc = scenario((50, 0, 50), MIN_JOB_VOLUME_T=50)
    first, second = solve(sc), solve(deepcopy(sc))
    assert first.status == second.status == "OPTIMAL"
    assert first.jobs == second.jobs
    assert first.unmet == second.unmet
    assert first.inventory == second.inventory
    assert first.objective == second.objective
    assert first.objective_pass_records == second.objective_pass_records


def test_later_objective_rejection_retains_incumbent_and_primary_model():
    model = cp_model.CpModel()
    x = model.new_int_var(0, 10, "x")
    seeded = model.clone()
    seeded.add(x == 10)
    initial = cp_model.CpSolver()
    assert initial.solve(seeded) == cp_model.OPTIMAL
    proto = str(model.proto)
    result = refine_objectives(
        model, initial, levels=[("service", 0, 0), ("operations", x, 0)],
        wall_s=1, deterministic_s=1, seed=42,
        accept_candidate=lambda candidate, incumbent: "MINUTE_EXECUTION")
    assert result.solver is initial
    assert result.records[0]["status"] == "REJECTED/MINUTE_EXECUTION"
    assert str(model.proto) == proto


def test_short_parcel_cost_is_independent_of_machine_multiplier():
    from mph_sim.campaigns import default_campaign_policy_config
    sc = scenario((1,), EQUIPMENT_START_PENALTY=0, EQUIPMENT_STOP_PENALTY=0,
                  EQUIPMENT_CHANGEOVER_PENALTY=0, EQUIPMENT_HOLD_BUCKET_PENALTY=0)
    sc.config["CAMPAIGN_POLICY"] = default_campaign_policy_config()
    sc.weights.w5_moves = 0
    first = solve(sc)
    sc.weights.w5_moves = 500
    second = solve(sc)
    assert first.breakdown["short_parcel_buckets"] == second.breakdown["short_parcel_buckets"] == 1
    assert first.breakdown["weighted_objective_comparison"] == second.breakdown["weighted_objective_comparison"]


def test_configuration_json_roundtrip_and_hot_reload_preserve_operator_values():
    from mph_sim.persistence import scenario_from_json, scenario_to_json
    sc = stage_scheduling_configuration(datasets.end_to_end(), {"MIN_JOB_VOLUME_T": 123.5})
    sc.config["BUILTIN_BASELINE_REVISION"] = "2026-09-15-s0-soft-parcel-batching-v33"
    profile = sc.config["ACTIVE_PLANNING_DEMAND_PROFILE"]
    demands = deepcopy(sc.demand)
    restored = scenario_from_json(scenario_to_json(sc))
    datasets.migrate_loaded_builtin(restored)
    assert restored.config["MIN_JOB_VOLUME_T"] == 123.5
    assert restored.config["ACTIVE_PLANNING_DEMAND_PROFILE"] == profile
    assert restored.demand == demands


def test_configuration_ui_defaults_zero_and_applies_only_on_submit():
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from conftest import nav_to
    from ui import common
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=300).run()
    nav_to(at, next(page for page in common.NAV_GROUPS["Configure"] if page.split(" ", 1)[0] == "2"))
    assert not at.exception, at.exception
    volume = next(widget for widget in at.main.number_input if widget.label == "Minimum job volume [t]")
    assert volume.value == 0
    volume.set_value(100)
    at.run()
    assert at.session_state["scenario"].config["MIN_JOB_VOLUME_T"] == 0
    next(button for button in at.main.button if button.label == "Apply scheduling configuration").click()
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["scenario"].config["MIN_JOB_VOLUME_T"] == 100


def test_planning_production_shortfall_is_feasible_and_visible():
    sc = scenario((0,), PLANNING_PROCESS_STAGE_TARGETS=["STACK"],
                  PROCESS_STAGE_OUTPUT_TARGET_T={"STACK": 1000})
    sc.spaces.append(StockSpace("Q", "S", "product", "open", 1000, 0, "G", node_id="Q"))
    sc.nodes.append(Node("Q", "S", "space"))
    sc.links.append(Link("P-Q", "P", "Q", 50))
    sc.machines.append(Machine("ST", "S", "stacker", 0, 50, ["Q"]))
    sc.process_transfers = [ProcessTransfer("STACK", "ST", "P", "Q", ["P-Q"],
                                          operation_kind="stack", process_stage="STACK", output_grade="G")]
    plan = solve(sc)
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    assert plan.breakdown["planning_stage_shortfall_t"] >= 950
    assert any("PLANNING_TARGET_SHORTFALL" in r for r in plan.reasons)
    committed = deepcopy(sc)
    committed.config["PLANNING_PROCESS_STAGE_TARGETS"] = []
    assert solve(committed).status == "INFEASIBLE"
