"""App-level acceptance test for the shared process-mimic time control."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamlit.testing.v1 import AppTest  # noqa: E402

from conftest import nav_to  # noqa: E402
from mph_sim.simulator import Simulator  # noqa: E402
from mph_sim.validation import validate_scenario  # noqa: E402
from ui import common  # noqa: E402
from ui.process_mimic import (  # noqa: E402
    PROCESS_MIMIC_BASIS_KEY,
    PROCESS_MIMIC_FINAL_STATE,
    PROCESS_MIMIC_HOUR_KEY,
    PROCESS_MIMIC_INITIAL_STATE,
    PROCESS_MIMIC_PLAN_TIMELINE,
    PROCESS_MIMIC_SIMULATION_STATE,
    process_mimic_bucket_for_hour,
    process_mimic_display_hour_limit,
)


ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")
OVERVIEW = next(p for p in common.ALL_PAGE_LABELS if p.startswith("1 "))
GLOBAL_FLOW = next(p for p in common.ALL_PAGE_LABELS if p.startswith("15 "))


def _caption_text(at: AppTest) -> str:
    return " ".join(str(item.value) for item in at.caption)


def _plan_hour_slider(at: AppTest):
    matches = [slider for slider in at.sidebar.slider
               if slider.label == "Plan hour"]
    assert len(matches) == 1, (
        "a plan-backed mimic page must expose exactly one shared Plan hour "
        f"slider; found {[s.label for s in at.sidebar.slider]}")
    return matches[0]


def _stockyard_state_radio(at: AppTest):
    matches = [radio for radio in at.sidebar.radio
               if radio.label == "Stockyard state"]
    assert len(matches) == 1
    return matches[0]


def test_hour_selector_maps_only_to_stored_native_boundaries(s2):
    assert process_mimic_display_hour_limit(s2) == 24
    assert process_mimic_bucket_for_hour(s2, -5) == 0
    assert process_mimic_bucket_for_hour(s2, 0) == 0
    assert process_mimic_bucket_for_hour(s2, 7) == 7
    assert process_mimic_bucket_for_hour(s2, 24) == 24
    assert process_mimic_bucket_for_hour(s2, 99) == 24

    subhour = copy.deepcopy(s2)
    subhour.sim.dt_h = 0.5
    subhour.sim.horizon_buckets = 48
    assert process_mimic_display_hour_limit(subhour) == 24
    assert process_mimic_bucket_for_hour(subhour, 7) == 14


def test_shared_sidebar_plan_hour_drives_and_persists_across_mimics(
        s2, s2_plan):
    """Overview and Global use one 0..24 hourly plan-replay state."""
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, at.exception

    # Inject the current built-in M16 scenario and its real plan.  Unlike S1,
    # this scenario renders the process mimic on both pages under test.
    sc = copy.deepcopy(s2)
    plan = copy.deepcopy(s2_plan)
    at.session_state["scenario"] = sc
    at.session_state["scenario_name"] = sc.name
    at.session_state["scn_sel"] = sc.name
    at.session_state["_scn_prev"] = sc.name
    at.session_state["plan"] = plan
    at.session_state["sim"] = None
    at.session_state["issues"] = validate_scenario(sc)

    nav_to(at, OVERVIEW)
    assert not at.exception, at.exception

    slider = _plan_hour_slider(at)
    assert slider.key == PROCESS_MIMIC_HOUR_KEY
    assert (slider.min, slider.max, slider.step) == (0, 24, 1)

    slider.set_value(5)
    at.run()
    assert not at.exception, at.exception
    assert at.session_state[PROCESS_MIMIC_HOUR_KEY] == 5
    assert "bucket 5" in _caption_text(at)
    assert not [s for s in at.slider if s.label == "Time bucket"], (
        "the sidebar timeline must not mount a second in-canvas time slider")

    nav_to(at, GLOBAL_FLOW)
    assert not at.exception, at.exception
    assert _plan_hour_slider(at).value == 5
    assert "bucket 5" in _caption_text(at)
    assert not [s for s in at.slider if s.label == "Time bucket"]

    nav_to(at, OVERVIEW)
    assert not at.exception, at.exception
    assert _plan_hour_slider(at).value == 5
    assert "bucket 5" in _caption_text(at)


def test_sidebar_explicitly_switches_initial_plan_final_and_simulation(
        s2, s2_plan):
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, at.exception

    sc = copy.deepcopy(s2)
    plan = copy.deepcopy(s2_plan)
    at.session_state["scenario"] = sc
    at.session_state["scenario_name"] = sc.name
    at.session_state["scn_sel"] = sc.name
    at.session_state["_scn_prev"] = sc.name
    at.session_state["plan"] = plan
    at.session_state["sim"] = None
    at.session_state["issues"] = validate_scenario(sc)

    nav_to(at, GLOBAL_FLOW)
    assert not at.exception, at.exception
    state = _stockyard_state_radio(at)
    assert state.key == PROCESS_MIMIC_BASIS_KEY
    assert state.options == [
        PROCESS_MIMIC_INITIAL_STATE,
        PROCESS_MIMIC_PLAN_TIMELINE,
        PROCESS_MIMIC_FINAL_STATE,
    ]

    state.set_value(PROCESS_MIMIC_INITIAL_STATE)
    at.run()
    assert at.session_state[PROCESS_MIMIC_BASIS_KEY] == \
        PROCESS_MIMIC_INITIAL_STATE
    assert _plan_hour_slider(at).disabled
    assert "Scenario snapshot" in _caption_text(at)

    _stockyard_state_radio(at).set_value(PROCESS_MIMIC_FINAL_STATE)
    at.run()
    assert at.session_state[PROCESS_MIMIC_BASIS_KEY] == \
        PROCESS_MIMIC_FINAL_STATE
    assert _plan_hour_slider(at).disabled
    assert "Terminal boundary" in _caption_text(at)
    assert "T=24" in _caption_text(at)

    _stockyard_state_radio(at).set_value(PROCESS_MIMIC_PLAN_TIMELINE)
    at.run()
    _plan_hour_slider(at).set_value(8)
    at.run()
    assert not _plan_hour_slider(at).disabled
    assert "bucket 8" in _caption_text(at)

    sim = Simulator(copy.deepcopy(sc), copy.deepcopy(plan),
                    auto_replan=False)
    assert sim.step()
    at.session_state["sim"] = sim
    at.run()
    state = _stockyard_state_radio(at)
    assert state.options[-1] == PROCESS_MIMIC_SIMULATION_STATE
    state.set_value(PROCESS_MIMIC_SIMULATION_STATE)
    at.run()
    assert at.session_state[PROCESS_MIMIC_BASIS_KEY] == \
        PROCESS_MIMIC_SIMULATION_STATE
    assert _plan_hour_slider(at).disabled
    assert "Simulation bucket" in _caption_text(at)
    assert "bucket 1" in _caption_text(at)
    assert not [s for s in at.slider if s.label == "Time bucket"]

    nav_to(at, OVERVIEW)
    assert not at.exception, at.exception
    assert at.session_state[PROCESS_MIMIC_BASIS_KEY] == \
        PROCESS_MIMIC_SIMULATION_STATE
    assert _plan_hour_slider(at).value == 8
    assert _plan_hour_slider(at).disabled
    assert "bucket 1" in _caption_text(at)
