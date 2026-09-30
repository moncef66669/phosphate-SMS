"""Demand-driven blend configuration and physical-campaign carry contracts."""
import pytest

from mph_sim import datasets
from mph_sim.blending import (
    chevron_batch_allocation, chevron_campaign_state, chevron_config_errors,
    chevron_construction_batch_t, chevron_is_demand_driven,
    chevron_job_pass_estimate, chevron_minimum_input_t, chevron_ready_minute,
    chevron_result_grade, chevron_settings, update_chevron_campaign_state,
    reopen_depleted_chevron_campaigns,
)
from mph_sim.domain import Job


def _scenario():
    scenario = datasets.end_to_end()
    scenario.config["CHEVRON_STACKING"]["enabled_spaces"] = ["HP-07"]
    return scenario


def _proposal(scenario):
    config = scenario.config["CHEVRON_STACKING"]
    recipe = [{"grade_id": "TBT", "tonnage_t": 600, "target_fraction": 0.4},
              {"grade_id": "BT-LOW-CD", "tonnage_t": 900, "target_fraction": 0.6}]
    config["proposed_recipes_by_space"] = {"HP-07": recipe}
    config["proposed_campaigns_by_space"] = {
        "HP-07": {"batch_t": 1500, "ready_minute": 120, "result_grade": "BT"}}
    return recipe


def test_new_policy_has_no_fixed_batch_grade_or_ready_time():
    scenario = _scenario()
    assert chevron_is_demand_driven(scenario)
    assert chevron_minimum_input_t(scenario) == 500
    assert chevron_construction_batch_t(scenario, "HP-07") == 0
    assert chevron_ready_minute(scenario, "HP-07") == 0
    assert chevron_settings(scenario)["passes_per_cycle"] == 0
    assert chevron_config_errors(scenario) == []


@pytest.mark.parametrize("invalid", [-1, float("nan"), float("inf")])
def test_invalid_input_minimum_is_reported(invalid):
    scenario = _scenario()
    scenario.config["CHEVRON_STACKING"]["min_input_grade_t"] = invalid
    assert any("Minimum tonnes" in error for error in chevron_config_errors(scenario))


def test_first_actual_stack_freezes_selected_total_recipe_and_grade():
    scenario = _scenario()
    recipe = _proposal(scenario)
    first = Job("first", "ST01-A", "stack", "HP-07", grade="TBT",
                tonnage_t=600, method="chevron", blend_recipe=recipe, status="FINISHED")
    update_chevron_campaign_state(scenario, [first])
    config = scenario.config["CHEVRON_STACKING"]
    config["proposed_campaigns_by_space"]["HP-07"] = {
        "batch_t": 9999, "result_grade": "MT", "ready_minute": 999}
    assert chevron_construction_batch_t(scenario, "HP-07") == 1500
    assert chevron_result_grade(scenario, "HP-07") == "BT"
    assert chevron_batch_allocation(scenario, "HP-07") == recipe
    assert chevron_campaign_state(scenario, "HP-07")["status"] == "BUILDING"
    second = Job("second", "ST01-A", "stack", "HP-07", grade="BT-LOW-CD",
                 tonnage_t=900, method="chevron", blend_recipe=recipe, status="FINISHED")
    update_chevron_campaign_state(scenario, [second])
    assert chevron_campaign_state(scenario, "HP-07")["status"] == "COMPLETE"


def test_passes_follow_mass_and_rate_only_with_explicit_stacking_speed():
    scenario = _scenario()
    machine = next(machine for machine in scenario.machines
                   if machine.kind == "stacker" and "HP-07" in machine.reachable_spaces)
    machine.work_area["HP-07"] = [0, 100]
    job = Job("pass", machine.id, "stack", "HP-07", grade="TBT",
              tonnage_t=500, rate_tph=1000, method="chevron")
    assert chevron_job_pass_estimate(scenario, job)["equivalent_passes"] is None
    assert chevron_job_pass_estimate(scenario, job)["passes"] is None
    scenario.config["CHEVRON_STACKING"]["stacking_traverse_speed_m_min"] = 10
    assert chevron_job_pass_estimate(scenario, job)["equivalent_passes"] == 3
    job.tonnage_t = 1000
    assert chevron_job_pass_estimate(scenario, job)["equivalent_passes"] == 6
    job.rate_tph = 2000
    assert chevron_job_pass_estimate(scenario, job)["equivalent_passes"] == 3


def test_pass_estimate_uses_flow_time_without_counting_idle_reservation_time():
    from mph_sim.domain import JobExecutionWindow
    scenario = _scenario()
    scenario.config["CHEVRON_STACKING"]["stacking_traverse_speed_m_min"] = 10
    machine = next(machine for machine in scenario.machines
                   if machine.kind == "stacker" and "HP-07" in machine.reachable_spaces)
    machine.work_area["HP-07"] = [0, 100]
    job = Job("pass", machine.id, "stack", "HP-07", grade="TBT",
              tonnage_t=500, rate_tph=500, method="chevron")
    job.execution_windows = [JobExecutionWindow(
        0, 0, 0, 60, 500, 1000, status="VALIDATED")]
    assert chevron_job_pass_estimate(scenario, job)["flow_duration_min"] == 30
    assert chevron_job_pass_estimate(scenario, job)["equivalent_passes"] == 3


def test_v7_migration_preserves_physical_legacy_quantity_only():
    scenario = _scenario()
    config = scenario.config["CHEVRON_STACKING"]
    config.update({"model_version": 6, "quantity_mode": "FIXED",
                   "global_batch_t": 12000, "global_ready_minute": 2880,
                   "global_result_grade": "BT", "passes_per_cycle": 20,
                   "campaign_states_by_space": {"HP-07": {
                       "status": "BUILDING", "stacked_t_by_grade": {"TBT": 500}}}})
    assert datasets._migrate_chevron_policy_v7(scenario)
    assert "global_batch_t" not in scenario.config["CHEVRON_STACKING"]
    assert chevron_construction_batch_t(scenario, "HP-07") == 12000
    assert chevron_result_grade(scenario, "HP-07") == "BT"
    assert chevron_construction_batch_t(scenario, "HP-08") == 0


def test_derived_pass_result_survives_later_edit_of_speed_and_gantt_has_no_cycle():
    from mph_sim.blending import update_chevron_job_passes
    from ui.theme import _gantt_hover_summary, jobs_to_gantt_df
    scenario = _scenario()
    scenario.config["CHEVRON_STACKING"]["stacking_traverse_speed_m_min"] = 10
    machine = next(machine for machine in scenario.machines
                   if machine.kind == "stacker" and "HP-07" in machine.reachable_spaces)
    machine.work_area["HP-07"] = [0, 100]
    job = Job("pass", machine.id, "stack", "HP-07", grade="TBT",
              tonnage_t=500, rate_tph=1000, method="chevron")
    update_chevron_job_passes(scenario, [job])
    scenario.config["CHEVRON_STACKING"]["stacking_traverse_speed_m_min"] = 30
    hover = _gantt_hover_summary(jobs_to_gantt_df(scenario, [job]).iloc[0])
    assert "estimated traversals for job 3.0" in hover
    assert "passes per cycle" not in hover


def test_missing_speed_is_unavailable_in_gantt_and_mimic_not_zero_passes():
    from mph_sim.blending import update_chevron_job_passes
    from mph_sim.domain import PlanResult
    from ui.theme import _gantt_hover_summary, jobs_to_gantt_df
    from ui.process_mimic import build_process_mimic_state, MimicOpts
    from ui.layout_geometry import geometry_for
    from ui.flow_svg.svg_mimic import build
    scenario = _scenario()
    job = Job("pass", "ST01-B", "stack", "HP-07", grade="TBT",
              tonnage_t=500, rate_tph=1000, method="chevron")
    update_chevron_job_passes(scenario, [job])
    hover = _gantt_hover_summary(jobs_to_gantt_df(scenario, [job]).iloc[0])
    assert "stacking speed not configured" in hover
    assert "0/0" not in hover
    plan = PlanResult(status="FEASIBLE", jobs=[job])
    state = build_process_mimic_state(scenario, plan)
    assert state.chevron_traversal_labels["HP-07"] == "traversal estimate unavailable"
    svg, _ratio = build(scenario, state, geometry_for(scenario, "MPH"), MimicOpts())
    assert "traversal estimate unavailable" in svg
    assert "0 passes/cycle" not in svg
    assert "20 passes/cycle" not in svg


def test_migration_widens_only_inherited_default_share_bounds():
    scenario = _scenario()
    config = scenario.config["CHEVRON_STACKING"]
    config["model_version"] = 6
    config["optimized_share_bounds"] = [
        {"grade_id": "TBT", "min_fraction": 0.05, "max_fraction": 0.95},
        {"grade_id": "BT-LOW-CD", "min_fraction": 0.05, "max_fraction": 0.95},
    ]
    datasets._migrate_chevron_policy_v7(scenario)
    assert all(row["min_fraction"] == 0 and row["max_fraction"] == 1
               for row in scenario.config["CHEVRON_STACKING"]["optimized_share_bounds"])
    config = scenario.config["CHEVRON_STACKING"]
    config["model_version"] = 6
    config["optimized_share_bounds"][0]["min_fraction"] = 0.1
    datasets._migrate_chevron_policy_v7(scenario)
    assert scenario.config["CHEVRON_STACKING"]["optimized_share_bounds"][0]["min_fraction"] == 0.1


@pytest.mark.parametrize("status", ["COMPLETE", "DEPLETED"])
def test_empty_finished_blend_reopens_with_accounting_preserved(status):
    scenario = _scenario()
    recipe = _proposal(scenario)
    policy = scenario.config["CHEVRON_STACKING"]
    policy["proposed_matrix_by_space"] = {"HP-07": "OLD", "HP-08": "KEEP"}
    policy["campaign_states_by_space"] = {"HP-07": {
        "status": status, "batch_t": 1500, "result_grade": "BT", "campaign_id": "old-lot",
        "resolved_recipe": recipe, "remaining_inventory_t": 0,
        "stacked_t_by_grade": {"TBT": 600, "BT-LOW-CD": 900},
        "accounted_job_t_by_id": {"old-stack": 600},
    }}
    scenario.space("HP-07").mass_t = 0
    assert chevron_config_errors(scenario) == []
    assert reopen_depleted_chevron_campaigns(scenario) == {"HP-07"}
    state = chevron_campaign_state(scenario, "HP-07")
    assert state["status"] == "NOT_STARTED"
    assert state["batch_t"] == 0
    assert state["resolved_recipe"] == []
    assert state["campaign_id"] == ""
    assert state["stacked_t_by_grade"] == {}
    assert state["accounted_job_t_by_id"] == {"old-stack": 600}
    policy = scenario.config["CHEVRON_STACKING"]
    assert "HP-07" not in policy["proposed_campaigns_by_space"]
    assert "HP-07" not in policy["proposed_recipes_by_space"]
    assert policy["proposed_matrix_by_space"] == {"HP-08": "KEEP"}
    old_job = Job("old-stack", "ST01-B", "stack", "HP-07", grade="TBT",
                  tonnage_t=600, method="chevron", blend_recipe=recipe, status="FINISHED")
    update_chevron_campaign_state(scenario, [old_job])
    assert chevron_campaign_state(scenario, "HP-07")["status"] == "NOT_STARTED"
    assert reopen_depleted_chevron_campaigns(scenario) == set()


@pytest.mark.parametrize("status,mass_t,remaining", [
    ("BUILDING", 0, 0), ("DEPLETED", 10, 0), ("COMPLETE", 0, 10),
    ("DEPLETED", 0, 10), ("COMPLETE", 10, None),
])
def test_reopening_preserves_incomplete_or_nonempty_material(status, mass_t, remaining):
    from copy import deepcopy
    scenario = _scenario()
    recipe = _proposal(scenario)
    scenario.space("HP-07").mass_t = mass_t
    scenario.config["CHEVRON_STACKING"]["campaign_states_by_space"] = {"HP-07": {
        "status": status, "batch_t": 1500, "resolved_recipe": recipe,
        "remaining_inventory_t": remaining}}
    before = deepcopy(scenario.config["CHEVRON_STACKING"])
    assert reopen_depleted_chevron_campaigns(scenario) == set()
    assert scenario.config["CHEVRON_STACKING"] == before


def test_reopening_uses_final_inventory_boundary_and_keeps_legacy_reset_required():
    scenario = _scenario()
    recipe = _proposal(scenario)
    scenario.config["CHEVRON_STACKING"]["campaign_states_by_space"] = {"HP-07": {
        "status": "DEPLETED", "batch_t": 1500, "resolved_recipe": recipe,
        "remaining_inventory_t": 0}}
    assert reopen_depleted_chevron_campaigns(scenario, {"HP-07": [1500, 10]}) == set()
    assert reopen_depleted_chevron_campaigns(scenario, {"HP-07": [1500, 0]}) == {"HP-07"}
    scenario.config["CHEVRON_STACKING"].update({"model_version": 6, "quantity_mode": "FIXED",
        "campaign_states_by_space": {"HP-07": {"status": "DEPLETED", "remaining_inventory_t": 0}}})
    assert reopen_depleted_chevron_campaigns(scenario) == set()
    assert any("previous blend is depleted" in error for error in chevron_config_errors(scenario))


def test_planned_carry_retains_lot_identity_without_actual_recipe_lock():
    scenario = _scenario()
    recipe = _proposal(scenario)
    scenario.config["CHEVRON_STACKING"]["proposed_campaigns_by_space"]["HP-07"]["campaign_id"] = "HP-07@0"
    job = Job("planned", "ST01-B", "stack", "HP-07", grade="TBT",
              tonnage_t=600, method="chevron", blend_recipe=recipe)
    update_chevron_campaign_state(scenario, [job], include_planned=True)
    state = chevron_campaign_state(scenario, "HP-07")
    assert state["campaign_id"] == "HP-07@0"
    assert state["resolved_recipe"] == []
