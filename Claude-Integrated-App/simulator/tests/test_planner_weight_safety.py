"""Focused contracts for safe, effective planner-priority configuration."""
import copy
import math

import pytest

from mph_sim import datasets
from mph_sim.domain import (ACTIVE_OBJECTIVE_WEIGHT_FIELDS,
                            OBJECTIVE_WEIGHT_MAX, ObjectiveWeights)
from mph_sim.optimizer import (BIG_PENALTY, WSCALE,
                               _objective_weight_coefficients, solve_plan)
from mph_sim.validation import validate_scenario
from ui.pages_planning import (planner_weight_values,
                               validate_planner_weight_values)


def _active_values(**updates):
    values = planner_weight_values(ObjectiveWeights())
    values.update(updates)
    return values


def test_editable_weight_cap_stays_below_fixed_emergency_coefficient():
    assert OBJECTIVE_WEIGHT_MAX * WSCALE < BIG_PENALTY
    values = _active_values(w1_unmet=OBJECTIVE_WEIGHT_MAX)
    assert validate_planner_weight_values(values)[0] == []


@pytest.mark.parametrize("field,bad_value", [
    ("w1_unmet", -1.0),
    ("w2_offspec", math.nan),
    ("w4_giveaway", math.inf),
    ("w5_moves", OBJECTIVE_WEIGHT_MAX + 1),
    ("w7_utilisation", 0.5),
])
def test_ui_validator_rejects_solver_unsafe_or_ambiguous_values(
        field, bad_value):
    errors, _warning = validate_planner_weight_values(
        _active_values(**{field: bad_value}))
    assert errors


@pytest.mark.parametrize("field,bad_value", [
    ("w1_unmet", -1.0),
    ("w2_offspec", math.nan),
    ("w4_giveaway", math.inf),
    ("w6_safety", OBJECTIVE_WEIGHT_MAX + 1),
    ("w7_utilisation", 0.25),
])
def test_imported_scenario_reports_invalid_active_weights(field, bad_value):
    scenario = copy.deepcopy(datasets.sulphur_minimal())
    setattr(scenario.weights, field, bad_value)
    errors = [issue for issue in validate_scenario(scenario)
              if issue.severity == "ERROR"
              and issue.code == "BAD_OBJECTIVE_WEIGHT"]
    assert errors
    assert field in errors[0].message


def test_zero_disables_quality_term_but_small_positive_stays_active():
    weights = ObjectiveWeights(w2_offspec=0.0)
    assert _objective_weight_coefficients(weights)["w2_offspec"] == 0
    weights.w2_offspec = 1.0
    assert _objective_weight_coefficients(weights)["w2_offspec"] == 1


def test_utilisation_resolution_and_invalid_solve_guard():
    weights = ObjectiveWeights(w7_utilisation=1.0)
    assert _objective_weight_coefficients(weights)["w7_utilisation"] == 1

    scenario = copy.deepcopy(datasets.sulphur_minimal())
    scenario.weights.w1_unmet = OBJECTIVE_WEIGHT_MAX + 1
    result = solve_plan(scenario)
    assert result.status == "ERROR"
    assert result.reasons and "BAD_OBJECTIVE_WEIGHT" in result.reasons[0]


def test_demand_priority_warning_is_described_as_a_heuristic():
    values = _active_values(w1_unmet=1.0, w2_offspec=2.0)
    errors, warning = validate_planner_weight_values(values)
    assert errors == []
    assert "heuristic" in warning
    assert "not a mathematical guarantee" in warning
    assert set(values) == set(ACTIVE_OBJECTIVE_WEIGHT_FIELDS)
