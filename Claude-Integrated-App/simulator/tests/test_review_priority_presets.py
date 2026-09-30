"""Named priorities are optional valid policies, with a visible apply gate."""
from copy import deepcopy
from pathlib import Path

from streamlit.testing.v1 import AppTest

from mph_sim import datasets
from mph_sim.domain import ObjectiveWeights
from ui.pages_planning import (
    OPERATIONAL_PRIORITY_DEFAULTS,
    PLANNER_WEIGHT_PRESETS,
    _commit_operational_priority_values,
    operational_priority_values,
    planner_weight_values,
    validate_operational_priority_values,
    validate_planner_weight_values,
)
from ui import common
from conftest import nav_to


def test_named_presets_leave_builtin_weights_unchanged_and_pass_validation():
    baseline = datasets.end_to_end()
    before = deepcopy(baseline.weights)
    assert PLANNER_WEIGHT_PRESETS["BALANCED"]["values"] == planner_weight_values(ObjectiveWeights())
    for preset in PLANNER_WEIGHT_PRESETS.values():
        assert validate_planner_weight_values(preset["values"]) == ([], "")
        assert preset["values"]["w7_utilisation"] == 0
    assert baseline.weights == before


def test_operational_priority_defaults_are_visible_without_mutating_scenario():
    scenario = datasets.end_to_end()
    before = deepcopy(scenario.config)

    values = operational_priority_values(scenario)

    assert values == OPERATIONAL_PRIORITY_DEFAULTS
    assert values["ENERGY_OBJECTIVE_ENABLED"] is False
    assert validate_operational_priority_values(values) == []
    assert scenario.config == before


def test_operational_priority_commit_is_atomic_and_invalidates_outputs(
        monkeypatch):
    scenario = datasets.end_to_end()
    calls = []
    monkeypatch.setattr(
        common, "invalidate_outputs",
        lambda: calls.append("invalidate") or True)
    monkeypatch.setattr(
        common, "revalidate", lambda: calls.append("revalidate"))
    requested = dict(OPERATIONAL_PRIORITY_DEFAULTS)
    requested.update({
        "ENERGY_OBJECTIVE_ENABLED": True,
        "CONTINUITY_EPISODE_PENALTY": 125,
        "EQUIPMENT_START_PENALTY": 25,
    })

    changed, stale = _commit_operational_priority_values(
        scenario, requested)

    assert (changed, stale) == (True, True)
    assert calls == ["invalidate", "revalidate"]
    assert scenario.config["ENERGY_OBJECTIVE_ENABLED"] is True
    assert scenario.config["CONTINUITY_EPISODE_PENALTY"] == 125
    assert scenario.config["EQUIPMENT_START_PENALTY"] == 25
    audit = scenario.config["OPERATIONAL_PRIORITY_OVERRIDES"][-1]
    assert audit["basis"] == "ASSUMED operator policy; uncalibrated"


def test_operational_priority_validation_rejects_non_integer_or_bad_flag():
    values = dict(OPERATIONAL_PRIORITY_DEFAULTS)
    values["ENERGY_OBJECTIVE_ENABLED"] = "yes"
    values["EQUIPMENT_STOP_PENALTY"] = 1.5

    errors = validate_operational_priority_values(values)

    assert any("true or false" in error for error in errors)
    assert any("Machine stop penalty" in error for error in errors)


def test_selecting_preset_previews_then_apply_records_policy():
    app = str(Path(__file__).resolve().parents[1] / "app.py")
    at = AppTest.from_file(app, default_timeout=300).run()
    demand_page = next(label for label in common.NAV_GROUPS["Plan"] if label.startswith("5 "))
    nav_to(at, demand_page)
    assert not at.exception
    assert any(
        toggle.label == "Enable productive-work and route-energy objective"
        for toggle in at.main.toggle)
    operational_labels = {item.label for item in at.main.number_input}
    assert {
        "Feed-gap minute penalty",
        "Interruption episode penalty",
        "Work/link energy penalty",
        "Machine start penalty",
        "Machine stop penalty",
        "Group changeover penalty",
    } <= operational_labels
    before = deepcopy(at.session_state["scenario"].weights)
    picker = next(item for item in at.main.selectbox if item.label == "Priority preset")
    picker.set_value("QUALITY").run()
    assert at.session_state["scenario"].weights == before
    next(item for item in at.main.button if item.label == "Apply priority preset").click().run()
    assert not at.exception
    scenario = at.session_state["scenario"]
    assert scenario.weights.w2_offspec == 500
    assert scenario.weights.w4_giveaway == 10
    assert scenario.config["ACTIVE_PLANNER_WEIGHT_PRESET"] == "QUALITY"
    assert scenario.config["PLANNER_WEIGHT_OVERRIDES"][-1]["before"]["w2_offspec"] == before.w2_offspec
