"""Additional example installation preserves operator and physical state."""

from copy import deepcopy

import pytest

from mph_sim import datasets


def _legacy_catalog_scenario():
    scenario = datasets.end_to_end()
    policy = scenario.config["CHEVRON_STACKING"]
    policy.pop("example_catalog_revision", None)
    policy["matrix_catalog"] = [
        entry for entry in policy["matrix_catalog"]
        if entry["id"] != "MPH-HUMID-02"]
    return scenario


def test_alternative_matrix_install_is_idempotent_and_uses_other_inputs():
    scenario = _legacy_catalog_scenario()
    before = deepcopy(scenario.config["CHEVRON_STACKING"]["matrix_catalog"])

    assert datasets._install_additional_mph_matrix(scenario) is True
    policy = scenario.config["CHEVRON_STACKING"]
    assert policy["matrix_catalog"][:-1] == before
    assert policy["matrix_catalog"][-1]["id"] == "MPH-HUMID-02"
    assert policy["matrix_catalog"][-1]["components"] == [
        {"grade_id": "BT", "target_fraction": 0.4},
        {"grade_id": "MT", "target_fraction": 0.6},
    ]
    assert policy["example_catalog_revision"] == 2
    installed = deepcopy(scenario)

    assert datasets._install_additional_mph_matrix(scenario) is False
    assert scenario == installed


def test_operator_deletion_is_not_undone_by_later_install_calls():
    scenario = _legacy_catalog_scenario()
    assert datasets._install_additional_mph_matrix(scenario) is True
    policy = scenario.config["CHEVRON_STACKING"]
    policy["matrix_catalog"] = [
        entry for entry in policy["matrix_catalog"]
        if entry["id"] != "MPH-HUMID-02"]
    before = deepcopy(scenario)

    assert datasets._install_additional_mph_matrix(scenario) is False
    assert scenario == before


def test_existing_operator_matrix_with_example_id_is_not_replaced():
    scenario = _legacy_catalog_scenario()
    operator_entry = {
        "id": "MPH-HUMID-02", "name": "Operator's existing recipe",
        "components": [
            {"grade_id": "TBT", "target_fraction": 0.25},
            {"grade_id": "BT-LOW-CD", "target_fraction": 0.75},
        ],
    }
    scenario.config["CHEVRON_STACKING"]["matrix_catalog"].append(operator_entry)
    before = deepcopy(scenario)
    expected = deepcopy(before)
    expected.config["CHEVRON_STACKING"]["example_catalog_revision"] = 2

    assert datasets._install_additional_mph_matrix(scenario) is False
    assert scenario == expected


def test_install_preserves_other_recipes_and_physical_campaign_state():
    scenario = _legacy_catalog_scenario()
    policy = scenario.config["CHEVRON_STACKING"]
    operator_entry = {
        "id": "OPERATOR-03", "name": "Physical campaign recipe",
        "components": [
            {"grade_id": "TBT", "target_fraction": 0.3},
            {"grade_id": "BT-LOW-CD", "target_fraction": 0.7},
        ],
    }
    policy["matrix_catalog"].append(operator_entry)
    policy["enabled"] = True
    policy["enabled_spaces"] = ["HP-07"]
    policy["min_input_grade_t"] = 750.0
    policy["campaign_states_by_space"] = {
        "HP-07": {
            "status": "BUILDING", "batch_t": 10_000.0,
            "resolved_recipe": deepcopy(operator_entry["components"]),
            "stacked_t_by_grade": {"TBT": 1_500.0},
            "actual_tonnage_by_job": {"EXISTING-JOB": 1_500.0},
        },
    }
    policy["proposed_matrix_by_space"] = {"HP-08": "OPERATOR-03"}
    scenario.space("HP-07").mass_t = 1_500.0
    before = deepcopy(scenario)

    assert datasets._install_additional_mph_matrix(scenario) is True
    installed = deepcopy(scenario)
    installed.config["CHEVRON_STACKING"].pop("example_catalog_revision")
    installed.config["CHEVRON_STACKING"]["matrix_catalog"] = [
        entry for entry in installed.config["CHEVRON_STACKING"]["matrix_catalog"]
        if entry["id"] != "MPH-HUMID-02"]
    assert installed == before


@pytest.mark.parametrize("scenario_name", ["Imported operator scenario", "Custom S0"])
def test_unrelated_imported_scenarios_are_unchanged(scenario_name):
    scenario = _legacy_catalog_scenario()
    scenario.name = scenario_name
    before = deepcopy(scenario)

    assert datasets._install_additional_mph_matrix(scenario) is False
    assert scenario == before
