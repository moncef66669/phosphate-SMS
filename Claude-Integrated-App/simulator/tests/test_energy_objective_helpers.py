"""Focused contracts for fixed-point work and route energy helpers."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets  # noqa: E402
from mph_sim.energy import (  # noqa: E402
    ENERGY_OBJECTIVE_SCALE,
    DEFAULT_LINK_KWH_PER_T,
    DEFAULT_WORK_KWH_PER_T,
    energy_model_parameters,
    energy_objective_parameter_errors,
    work_route_energy_coefficient,
)


def test_default_coefficients_use_centi_kwh_units_for_each_work_kind():
    scenario = datasets.sulphur_minimal()

    assert ENERGY_OBJECTIVE_SCALE == 100
    assert work_route_energy_coefficient(scenario, "stack") == 12
    assert work_route_energy_coefficient(scenario, "reclaim") == 15
    assert work_route_energy_coefficient(scenario, "dispatch") == 10
    assert work_route_energy_coefficient(scenario, "unknown") == 12


def test_route_coefficient_counts_distinct_links_once():
    scenario = datasets.sulphur_minimal()

    # Stack work 0.12 + two distinct links * 0.04 = 0.20 kWh/t.
    assert work_route_energy_coefficient(
        scenario, "STACK", ["L1", "L1", "L2"]) == 20


def test_scenario_overrides_drive_the_integer_candidate_coefficient():
    scenario = datasets.sulphur_minimal()
    scenario.config["ENERGY_LINK_KWH_PER_T"] = 0.03
    scenario.config["ENERGY_WORK_KWH_PER_T"] = {
        "reclaim": 0.21,
        "default": 0.11,
    }

    assert work_route_energy_coefficient(
        scenario, "reclaim", ["L1", "L2"]) == 27
    assert work_route_energy_coefficient(
        scenario, "other", ["L1"]) == 14
    assert energy_objective_parameter_errors(scenario) == []


def test_invalid_coefficients_fall_back_for_reporting_but_fail_validation():
    scenario = datasets.sulphur_minimal()
    scenario.config["ENERGY_LINK_KWH_PER_T"] = float("nan")
    scenario.config["ENERGY_WORK_KWH_PER_T"] = {
        "stack": -1,
        "reclaim": "not-a-number",
    }

    params = energy_model_parameters(scenario)
    assert params["link_kwh_per_t"] == DEFAULT_LINK_KWH_PER_T
    assert params["work_kwh_per_t"]["stack"] == \
        DEFAULT_WORK_KWH_PER_T["stack"]
    assert params["work_kwh_per_t"]["reclaim"] == \
        DEFAULT_WORK_KWH_PER_T["reclaim"]
    assert params["warnings"]
    assert work_route_energy_coefficient(scenario, "stack", ["L1"]) == 16

    errors = energy_objective_parameter_errors(scenario)
    assert any("Conveyor-link" in error for error in errors)
    assert any("stack work" in error for error in errors)
    assert any("reclaim work" in error for error in errors)
    assert energy_objective_parameter_errors(scenario, enabled=False) == []


@pytest.mark.parametrize("raw", [object(), 1.5])
def test_malformed_work_container_uses_defaults_and_is_an_objective_error(raw):
    scenario = datasets.sulphur_minimal()
    scenario.config["ENERGY_WORK_KWH_PER_T"] = raw

    params = energy_model_parameters(scenario)
    assert params["work_kwh_per_t"] == DEFAULT_WORK_KWH_PER_T
    assert params["warnings"]
    assert energy_objective_parameter_errors(scenario) == [
        "Work energy intensities must be a mapping."
    ]


def test_extreme_finite_intensity_is_rejected_for_integer_objective_safety():
    scenario = datasets.sulphur_minimal()
    scenario.config["ENERGY_LINK_KWH_PER_T"] = 1e20

    errors = energy_objective_parameter_errors(scenario)

    assert any("must not exceed" in error for error in errors)
