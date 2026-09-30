"""Focused contract for the S0/domain navigation redesign.

The operator UI exposes one planning scenario and one commissioned domain,
while the model keeps its existing S3 identity and diagnostic deep links.
"""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from conftest import nav_to  # noqa: E402
from mph_sim import datasets  # noqa: E402
from mph_sim.domain import PlanResult  # noqa: E402
from mph_sim.validation import validate_scenario  # noqa: E402
from ui import common  # noqa: E402


APP = str(ROOT / "app.py")


def _fresh() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    return at


def _generate_buttons(root):
    return [
        button for button in root.button
        if "Generate Plan" in str(button.label)
    ]


def _visible_text(root) -> str:
    values: list[str] = []
    for element_type in (
        "subheader", "markdown", "caption", "info", "warning", "success",
    ):
        for element in getattr(root, element_type):
            values.append(str(element.value))
    return "\n".join(values)


def test_s0_is_a_ui_alias_for_s3_and_plan_has_one_visible_page():
    assert common.PLANNING_SCENARIOS == {
        "S0": datasets.S3_SCENARIO_NAME,
    }
    assert common.planning_scenario_dataset("S0") == \
        datasets.S3_SCENARIO_NAME
    assert common.planning_scenario_code(datasets.S3_SCENARIO_NAME) == "S0"
    assert common.NAV_GROUPS["Plan"] == ["5 · Demand & Quality"]
    assert common.page_title("5 · Demand & Quality") == \
        "S0 · Three-site planning"
    assert "6 · Planning Workspace" in common.NAV_GROUPS["Advanced"]
    assert common.PLAN_LANDING_PAGE == "5 · Demand & Quality"


def test_default_ui_has_top_domain_nav_plan_scenario_and_sidebar_solve():
    at = _fresh()

    assert at.segmented_control[0].label == "Domain"
    assert at.segmented_control[0].options == [
        common.PRIMARY_DOMAIN, common.FUTURE_DOMAIN,
    ]
    assert at.segmented_control[0].value == common.PRIMARY_DOMAIN

    assert at.sidebar.radio[0].value == "Plan"
    assert at.sidebar.radio[1].options == ["S0 · Three-site planning"]
    assert not at.sidebar.selectbox
    assert at.main.selectbox[0].label == "Planning scenario"
    assert at.main.selectbox[0].options == ["S0 — BG + LO + MPH"]
    assert at.main.selectbox[0].value == "S0"

    assert at.session_state["scenario_name"] == datasets.S3_SCENARIO_NAME
    assert at.session_state["scenario"].name == datasets.S3_SCENARIO_NAME
    assert len(_generate_buttons(at.sidebar)) == 1
    assert not _generate_buttons(at.main)
    assert _generate_buttons(at.sidebar)[0].disabled is False
    editor_keys = {frame.key for frame in at.main.dataframe}
    assert {"unit_demand_editor", "demand_editor"} <= editor_keys


def test_ms_is_placeholder_and_round_trip_preserves_s0_plan():
    at = _fresh()
    at.session_state["plan"] = PlanResult(
        status="FEASIBLE", solver_wall_s=1.0)
    at.run()
    assert not at.exception, at.exception

    at.segmented_control[0].set_value(common.FUTURE_DOMAIN)
    at.run()
    assert not at.exception, at.exception
    assert "coming later" in _visible_text(at.main).lower()
    assert at.session_state["scenario_name"] == datasets.S3_SCENARIO_NAME
    assert at.session_state["plan"].status == "FEASIBLE"
    assert _generate_buttons(at.sidebar)[0].disabled is True
    assert not at.main.dataframe

    at.segmented_control[0].set_value(common.PRIMARY_DOMAIN)
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["scenario_name"] == datasets.S3_SCENARIO_NAME
    assert at.session_state["plan"].status == "FEASIBLE"
    assert _generate_buttons(at.sidebar)[0].disabled is False
    editor_keys = {frame.key for frame in at.main.dataframe}
    assert {"unit_demand_editor", "demand_editor"} <= editor_keys


def test_page_six_keeps_its_deep_link_under_advanced():
    at = _fresh()
    nav_to(at, "6 · Planning Workspace")
    assert not at.exception, at.exception
    assert at.sidebar.radio[0].value == "Advanced"
    assert at.sidebar.radio[1].value == "6 · Planning Workspace"
    assert common.page_title(at.sidebar.radio[1].value) == \
        "Planning settings"
    assert not _generate_buttons(at.main)
    assert len(_generate_buttons(at.sidebar)) == 1


def test_plan_page_can_restore_s0_after_a_diagnostic_deep_link():
    at = _fresh()
    diagnostic = datasets.sulphur_minimal()
    at.session_state["scenario"] = diagnostic
    at.session_state["scenario_name"] = diagnostic.name
    at.session_state["plan"] = None
    at.session_state["sim"] = None
    at.session_state["issues"] = validate_scenario(diagnostic)
    at.run()
    assert not at.exception, at.exception

    assert _generate_buttons(at.sidebar)[0].disabled is True
    restore = [
        button for button in at.main.button if button.label == "Load S0"
    ]
    assert len(restore) == 1
    restore[0].click()
    at.run()

    assert not at.exception, at.exception
    assert at.session_state["scenario_name"] == datasets.S3_SCENARIO_NAME
    assert at.session_state["plan"] is None
    assert _generate_buttons(at.sidebar)[0].disabled is False
