"""Focused operator contracts for mixed-resolution planning controls."""
from __future__ import annotations

import inspect
from pathlib import Path

from streamlit.testing.v1 import AppTest

from conftest import nav_to
from mph_sim.domain import PlanResult, SimParams
from ui import common
from ui.pages_planning import (
    build_ui_sim_event,
    page_schedule,
    planning_resolution_summary,
    stage_planning_settings,
)


ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")


def _settings_page() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    nav_to(at, next(
        label for label in common.NAV_GROUPS["Advanced"]
        if label.split(" ", 1)[0] == "6"
    ))
    assert not at.exception, at.exception
    return at


def test_advanced_page_exposes_minute_windows_not_legacy_bucket_controls():
    at = _settings_page()
    labels = {widget.label for widget in at.main.number_input}
    assert {
        "Total horizon [min]",
        "Near step [min]",
        "Near horizon [min]",
        "Far step [min]",
        "Replan cadence [min]",
        "Commit window [min]",
    } <= labels
    assert not {"Horizon [buckets]", "Δt [h]", "Frozen horizon [buckets]"} \
        & labels
    assert any(toggle.label == "Mixed resolution" for toggle in at.main.toggle)


def test_operator_plan_path_keeps_weekly_s0_on_uniform_hourly_grid():
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    nav_to(at, common.PLAN_LANDING_PAGE)
    assert not at.exception, at.exception
    assert at.session_state["scenario"].sim.mixed_resolution_enabled is False
    text = "\n".join(str(item.value) for item in at.main.caption)
    assert "168 h / 10,080 min horizon" in text


def test_summary_reports_minutes_hours_steps_and_control_windows():
    sim = SimParams()
    sim.mixed_resolution_enabled = True
    sim.horizon_minutes = 1440
    sim.near_term_step_min = 15
    sim.near_term_horizon_min = 240
    sim.far_term_step_min = 60
    sim.replan_interval_min = 15
    sim.commit_window_min = 30

    summary = planning_resolution_summary(sim)
    assert "1 days / 24 h / 1,440 min" in summary
    assert "first 240 min (4 h) at 15-min steps" in summary
    assert "remaining horizon at 60-min steps" in summary
    assert "replan every 15 min" in summary
    assert "first 30 min committed" in summary


def test_staging_preserves_native_compatibility_grid():
    sim = SimParams(horizon_buckets=24, dt_h=1.0, frozen_buckets=2)
    staged, errors = stage_planning_settings(
        sim,
        mixed_resolution_enabled=True,
        horizon_minutes=1440,
        near_term_step_min=15,
        near_term_horizon_min=240,
        far_term_step_min=60,
        replan_interval_min=15,
        commit_window_min=30,
        max_solve_s=21.0,
        seed=7,
        deviation_tolerance=0.05,
    )

    assert errors == []
    assert (staged.horizon_buckets, staged.dt_h, staged.frozen_buckets) == \
        (24, 1.0, 2)
    assert staged.horizon_minutes == 1440
    assert staged.near_term_step_min == 15
    assert staged.near_term_horizon_min == 240
    assert staged.far_term_step_min == 60
    assert staged.replan_interval_min == 15
    assert staged.commit_window_min == 30
    assert staged.max_solve_s == 21.0
    assert staged.seed == 7
    assert staged.deviation_tolerance == 0.05


def test_event_builder_uses_exact_minutes_and_keeps_legacy_bucket_fallback():
    sim = SimParams(dt_h=1.0)
    event = build_ui_sim_event(
        event_id="UI1",
        event_type="machine_failure",
        at_value=75,
        target="RC01-A",
        duration_value=30,
        sim_params=sim,
    )

    assert event.minute == 75
    assert event.duration_minutes == 30
    assert event.bucket == 1
    assert event.duration_buckets == 1


def test_simulation_event_form_uses_minute_time_and_duration():
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    at.session_state["plan"] = PlanResult(status="FEASIBLE")
    at.session_state["sim"] = None
    nav_to(at, next(
        label for label in common.NAV_GROUPS["Advanced"]
        if label.split(" ", 1)[0] == "7"
    ))
    assert not at.exception, at.exception
    labels = {widget.label for widget in at.main.number_input}
    assert "At minute" in labels
    assert "Duration [min]" in labels
    assert "At bucket" not in labels
    assert "Duration [buckets]" not in labels


def test_schedule_copy_describes_both_hourly_and_optional_mixed_slices():
    source = inspect.getsource(page_schedule)
    assert "mixed-resolution solver slices" in source
    assert "near_term_step_min" in source
    assert "far_term_step_min" in source
    assert "hourly solver slices" in source
