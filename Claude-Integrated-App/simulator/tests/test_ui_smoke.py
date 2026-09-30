"""Headless UI smoke test: every page renders without exceptions,
with and without a generated plan (uses the fast S1 scenario)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")

PAGES = [
    "1 · Executive Overview", "2 · Input Data", "3 · Stockyards & Inventory",
    "4 · Routes & Equipment", "5 · Demand & Quality", "6 · Planning Workspace",
    "7 · Simulation Control", "8 · Results & KPIs", "9 · Job Schedule",
    "10 · Exceptions & Replanning", "11 · Three-SMS Integration Console",
    "12 · Assumptions & Data Gaps", "13 · Scenario Comparison",
    "14 · Audit & Export", "15 · Global Stockyard Layout & Flow",
    "16 · Machine Coverage", "17 · Grade Profiles",
]


def _fresh():
    at = AppTest.from_file(APP, default_timeout=240)
    at.run()
    assert not at.exception, at.exception
    return at


def _operator_visible_text(at) -> str:
    """Collect rendered copy and display tables, excluding internal values."""
    values = []
    for root in (at.sidebar, at.main):
        for element_type in (
            "subheader", "markdown", "caption", "info", "warning", "error",
            "success", "header", "text",
        ):
            values.extend(
                str(element.value)
                for element in getattr(root, element_type)
            )
        for element_type in (
            "button", "radio", "selectbox", "multiselect", "toggle",
            "number_input", "text_input",
        ):
            values.extend(
                str(element.label)
                for element in getattr(root, element_type)
            )
        values.extend(
            str(element.value)
            for element in root.dataframe
        )
    return "\n".join(values)


def test_all_pages_render_without_plan():
    from conftest import nav_to
    at = _fresh()
    for p in PAGES:
        nav_to(at, p)
        assert not at.exception, f"page '{p}' raised: {at.exception}"
        assert "tbc" not in _operator_visible_text(at).lower(), (
            f"page '{p}' exposes internal evidence shorthand")


def test_pages_render_with_plan_and_sim():
    at = _fresh()
    # load fast sulphur scenario and solve via engine directly in session state
    from mph_sim import datasets
    from mph_sim.optimizer import SolveOptions, solve_plan
    from mph_sim.simulator import Simulator
    sc = datasets.sulphur_minimal()
    plan = solve_plan(sc, SolveOptions(max_time_s=3))
    sim = Simulator(sc, plan, auto_replan=True)
    for _ in range(6):
        sim.step()
    at.session_state["scenario"] = sc
    at.session_state["scenario_name"] = sc.name
    at.session_state["plan"] = plan
    at.session_state["sim"] = sim
    from mph_sim.validation import validate_scenario
    at.session_state["issues"] = validate_scenario(sc)
    from conftest import nav_to
    for p in PAGES:
        nav_to(at, p)
        assert not at.exception, f"page '{p}' raised: {at.exception}"
        assert "tbc" not in _operator_visible_text(at).lower(), (
            f"page '{p}' exposes internal evidence shorthand with a plan")
