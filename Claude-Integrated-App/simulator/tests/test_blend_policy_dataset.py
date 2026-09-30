from copy import deepcopy

from mph_sim import datasets


RETIRED_POLICY_FIELDS = {
    "global_result_grade", "global_batch_t", "global_ready_minute",
    "global_minimum_component_fraction", "passes_per_cycle",
    "result_grades", "batch_t_by_space", "ready_minute_by_space",
    "ready_bucket_by_space", "minimum_component_fraction_by_space",
}


def test_builtin_blend_policy_exposes_matrix_and_optimized_share_modes():
    scenario = datasets.end_to_end()
    policy = scenario.config["CHEVRON_STACKING"]

    assert policy["model_version"] == 7
    assert policy["quantity_mode"] == "DEMAND_DRIVEN"
    assert policy["min_input_grade_t"] == 500
    assert policy["global_mode"] == "OPTIMIZED"
    assert policy["enabled_spaces"] == [f"HP-{index:02d}" for index in range(1, 9)]
    assert len(policy["matrix_catalog"]) == 2
    assert len(policy["matrix_catalog"][0]["components"]) == 2
    bounds = policy["optimized_share_bounds"]
    assert {row["grade_id"] for row in bounds} == {"TBT", "BT-LOW-CD"}
    assert all(row["min_fraction"] == 0 and row["max_fraction"] == 1
               for row in bounds)
    assert policy["proposed_recipes_by_space"] == {}
    assert policy["proposed_matrix_by_space"] == {}
    assert policy["proposed_campaigns_by_space"] == {}
    assert not RETIRED_POLICY_FIELDS & policy.keys()


def test_v4_blend_policy_migration_preserves_operator_matrix_in_place():
    scenario = datasets.end_to_end()
    policy = {
        "model_version": 4,
        "enabled": False,
        "enabled_spaces": [],
        "passes_per_cycle": 20,
        "recipes": {"HP-07": []},
        "result_grades": {"HP-07": "BT"},
        "batch_t_by_space": {"HP-07": 12000},
        "ready_minute_by_space": {"HP-07": 2880},
        "point_labels": deepcopy(scenario.config[
            "CHEVRON_STACKING"]["point_labels"]),
    }
    operator_matrix = [
        {"grade_id": "BT", "target_fraction": 0.25},
        {"grade_id": "TBT", "target_fraction": 0.75},
    ]
    policy["model_version"] = 4
    policy["recipes"]["HP-07"] = deepcopy(operator_matrix)
    policy.pop("mode_by_space", None)
    policy.pop("share_bounds_by_space", None)
    policy.pop("proposed_recipes_by_space", None)
    policy.pop("minimum_component_fraction_by_space", None)
    scenario.config["CHEVRON_STACKING"] = policy

    assert datasets.migrate_loaded_builtin(scenario) == "solver"
    upgraded = scenario.config["CHEVRON_STACKING"]
    assert upgraded["model_version"] == 7
    assert upgraded["quantity_mode"] == "DEMAND_DRIVEN"
    assert upgraded["min_input_grade_t"] == 500
    assert upgraded["matrix_catalog"][0]["components"] == operator_matrix
    assert upgraded["global_mode"] == "MATRIX"
    assert {row["grade_id"] for row in
            upgraded["optimized_share_bounds"]} == {"BT", "TBT"}
    assert upgraded["proposed_recipes_by_space"] == {}
    assert upgraded["enabled_spaces"] == []
    assert not RETIRED_POLICY_FIELDS & upgraded.keys()


def test_missing_blend_policy_migration_has_an_independent_fallback(
        monkeypatch):
    scenario = datasets.end_to_end()
    scenario.config.pop("CHEVRON_STACKING")
    stripped_fresh = deepcopy(scenario)
    monkeypatch.setattr(
        datasets, "end_to_end", lambda **_kwargs: deepcopy(stripped_fresh))

    assert datasets.migrate_loaded_builtin(scenario) == "solver"
    policy = scenario.config["CHEVRON_STACKING"]
    assert policy["model_version"] == 7
    assert policy["quantity_mode"] == "DEMAND_DRIVEN"
    assert policy["min_input_grade_t"] == 500
    assert policy["global_mode"] == "OPTIMIZED"
    assert policy["enabled_spaces"] == [f"HP-{index:02d}" for index in range(1, 9)]
    assert not RETIRED_POLICY_FIELDS & policy.keys()


def test_v6_migration_preserves_started_campaigns_and_shared_operator_policy():
    scenario = datasets.end_to_end()
    policy = deepcopy(scenario.config["CHEVRON_STACKING"])
    matrix_catalog = [{
        "id": "CUSTOM-10-90", "name": "Operator matrix",
        "components": [
            {"grade_id": "TBT", "target_fraction": .1},
            {"grade_id": "BT-LOW-CD", "target_fraction": .9},
        ],
    }]
    locked_recipe = [
        {"grade_id": "BT", "tonnage_t": 5000},
        {"grade_id": "MT", "tonnage_t": 5000},
    ]
    policy.update({
        "model_version": 6,
        "enabled": True,
        "enabled_spaces": ["HP-07", "HP-08"],
        "global_mode": "MATRIX",
        "matrix_catalog": deepcopy(matrix_catalog),
        "optimized_share_bounds": [
            {"grade_id": "TBT", "min_fraction": .2, "max_fraction": .8},
            {"grade_id": "BT-LOW-CD", "min_fraction": .2, "max_fraction": .8},
        ],
        "global_result_grade": "BT",
        "global_batch_t": 12000,
        "global_ready_minute": 2880,
        "passes_per_cycle": 20,
        "global_minimum_component_fraction": .05,
        "min_input_grade_t": 750,
        "proposed_campaigns_by_space": {"HP-08": {"batch_t": 20000}},
        "proposed_recipes_by_space": {"HP-08": [{
            "grade_id": "TBT", "tonnage_t": 20000}]},
        "proposed_matrix_by_space": {"HP-08": "STALE-PROPOSAL"},
        "campaign_states_by_space": {
            "HP-07": {
                "status": "BUILDING",
                "stacked_t_by_grade": {"TBT": 500, "BT-LOW-CD": 1000},
                "accounted_job_t_by_id": {"actual-stack-job": 1500},
                "remaining_inventory_t": 1500,
            },
            "HP-08": {
                "status": "COMPLETE",
                "stacked_t_by_grade": {"BT": 5000, "MT": 5000},
                "remaining_inventory_t": 8000,
                "completed_minute": 1500,
                "resolved_recipe": deepcopy(locked_recipe),
                "resolution_source": "MATRIX",
                "selected_matrix": "PHYSICALLY-LOCKED",
                "batch_t": 10000,
                "result_grade": "MT",
                "ready_minute": 1440,
            },
        },
    })
    policy.pop("quantity_mode", None)
    scenario.config["CHEVRON_STACKING"] = policy

    assert datasets.migrate_loaded_builtin(scenario) == "solver"
    upgraded = scenario.config["CHEVRON_STACKING"]
    assert upgraded["model_version"] == 7
    assert upgraded["quantity_mode"] == "DEMAND_DRIVEN"
    assert upgraded["min_input_grade_t"] == 750
    assert upgraded["global_mode"] == "MATRIX"
    assert upgraded["enabled_spaces"] == ["HP-07", "HP-08"]
    assert upgraded["matrix_catalog"] == matrix_catalog
    assert upgraded["optimized_share_bounds"] == policy["optimized_share_bounds"]
    assert not RETIRED_POLICY_FIELDS & upgraded.keys()
    assert upgraded["proposed_campaigns_by_space"] == {}
    assert upgraded["proposed_recipes_by_space"] == {}
    assert upgraded["proposed_matrix_by_space"] == {}

    building = upgraded["campaign_states_by_space"]["HP-07"]
    assert building["status"] == "BUILDING"
    assert building["stacked_t_by_grade"] == {"TBT": 500, "BT-LOW-CD": 1000}
    assert building["accounted_job_t_by_id"] == {"actual-stack-job": 1500}
    assert building["remaining_inventory_t"] == 1500
    assert building["batch_t"] == 12000
    assert building["result_grade"] == "BT"
    assert building["ready_minute"] == 2880
    assert {row["grade_id"]: row["tonnage_t"]
            for row in building["resolved_recipe"]} == {
                "TBT": 1200, "BT-LOW-CD": 10800}

    complete = upgraded["campaign_states_by_space"]["HP-08"]
    assert complete["status"] == "COMPLETE"
    assert complete["stacked_t_by_grade"] == {"BT": 5000, "MT": 5000}
    assert complete["remaining_inventory_t"] == 8000
    assert complete["completed_minute"] == 1500
    assert {row["grade_id"]: row["tonnage_t"]
            for row in complete["resolved_recipe"]} == {
                row["grade_id"]: row["tonnage_t"] for row in locked_recipe}
    assert all(row["target_fraction"] == .5
               for row in complete["resolved_recipe"])
    assert complete["selected_matrix"] == "PHYSICALLY-LOCKED"
    assert complete["batch_t"] == 10000
    assert complete["result_grade"] == "MT"
    assert complete["ready_minute"] == 1440


def test_v7_migration_replaces_inherited_percentage_floor_with_tonnage_floor():
    scenario = datasets.end_to_end()
    policy = scenario.config["CHEVRON_STACKING"]
    policy["model_version"] = 6
    policy.pop("quantity_mode", None)
    policy.pop("min_input_grade_t", None)
    policy["global_minimum_component_fraction"] = .05
    policy["optimized_share_bounds"] = [
        {"grade_id": "TBT", "min_fraction": .05, "max_fraction": .95},
        {"grade_id": "BT-LOW-CD", "min_fraction": .05, "max_fraction": .95},
    ]

    assert datasets.migrate_loaded_builtin(scenario) == "solver"
    upgraded = scenario.config["CHEVRON_STACKING"]
    assert upgraded["min_input_grade_t"] == 500
    assert upgraded["quantity_mode"] == "DEMAND_DRIVEN"
    assert not RETIRED_POLICY_FIELDS & upgraded.keys()
    assert all(row["min_fraction"] == 0 and row["max_fraction"] == 1
               for row in upgraded["optimized_share_bounds"])
