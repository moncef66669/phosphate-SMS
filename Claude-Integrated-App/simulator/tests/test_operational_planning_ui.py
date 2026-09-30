"""Focused UI contracts for weekly operations and reactive replanning."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from conftest import nav_to
from mph_sim import datasets
from mph_sim.domain import Job, LogRecord, PlanResult
from ui import common
from ui.pages_planning import (
    HOURLY_WEEK_MODE,
    MIDWEEK_TEST_EVENT_ID,
    ROLLING_REFINEMENT_MODE,
    latest_replan_latency,
    planning_clock_label,
    planning_grid_facts,
    replan_impact_summary,
    stage_weekly_resolution_preset,
    unexpected_event_status,
)


APP = str(Path(__file__).resolve().parents[1] / "app.py")


def test_default_week_is_168_hourly_decisions_and_rolling_is_near_term_only():
    scenario = datasets.end_to_end()
    facts = planning_grid_facts(scenario.sim)
    assert facts["horizon_min"] == 10_080
    assert facts["grid"] == "168 × 60 min"
    assert facts["decisions"] == 168

    staged, errors = stage_weekly_resolution_preset(
        scenario.sim, ROLLING_REFINEMENT_MODE)
    assert errors == []
    assert staged.horizon_buckets == 168
    assert staged.horizon_minutes == 10_080
    assert staged.near_term_step_min == 15
    assert staged.near_term_horizon_min == 240
    assert staged.far_term_step_min == 60
    assert planning_grid_facts(staged)["grid"] == \
        "15 min near term → 60 min tail"

    restored, errors = stage_weekly_resolution_preset(
        staged, HOURLY_WEEK_MODE)
    assert errors == []
    assert restored.mixed_resolution_enabled is False


def test_calendar_clock_and_replan_impact_are_operator_readable():
    scenario = datasets.end_to_end()
    assert "Day 4" in planning_clock_label(scenario, 5_040)
    assert "Thu 16 Jul 12:00" in planning_clock_label(scenario, 5_040)

    before = [
        Job("J1", "M1", "reclaim", "P1", tonnage_t=100),
        Job("J2", "M2", "stack", "P2", tonnage_t=200),
        Job("J3", "M3", "reclaim", "P3", tonnage_t=50),
    ]
    after = [
        Job("J1", "M1", "reclaim", "P1", tonnage_t=100),
        Job("J2", "M2", "stack", "P2", tonnage_t=150),
        Job("J4", "M4", "reclaim", "P4", tonnage_t=80),
    ]
    impact = replan_impact_summary(before, after)
    assert impact["preserved"] == ["J1"]
    assert impact["changed"] == ["J2"]
    assert impact["removed"] == ["J3"]
    assert impact["added"] == ["J4"]
    assert impact["tonnage_delta_t"] == pytest.approx(-20)
    assert impact["retained_pct"] == pytest.approx(330 / 350 * 100)


def test_replan_status_distinguishes_queued_detected_and_completed():
    event = SimpleNamespace(id="E1")
    simulator = SimpleNamespace(
        events=[event], log=[], replan_solve_s=[], bucket_width_min=60,
        auto_replan=True)
    assert unexpected_event_status(simulator, "E1") == \
        "Queued · awaiting event time"

    simulator.log.append(LogRecord(
        84, "EVENT", "failure", "M1",
        {"minute": 5_040, "event_id": "E1"}))
    assert unexpected_event_status(simulator, "E1") == \
        "Detected · replan pending"

    simulator.log.append(LogRecord(
        84, "REPLAN", "complete", "M1", {"minute": 5_055}))
    simulator.replan_solve_s.append(2.5)
    assert unexpected_event_status(simulator, "E1") == \
        "Detected · replan completed"
    latency = latest_replan_latency(simulator)
    assert latency["latency_min"] == 15
    assert latency["solve_s"] == pytest.approx(2.5)


def test_normal_plan_page_surfaces_hourly_default_and_rolling_option():
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    nav_to(at, common.PLAN_LANDING_PAGE)
    assert not at.exception, at.exception
    resolution = next(
        radio for radio in at.main.radio
        if radio.label == "Resolution mode for the next plan")
    assert resolution.value == HOURLY_WEEK_MODE
    assert list(resolution.options) == [
        HOURLY_WEEK_MODE, ROLLING_REFINEMENT_MODE]
    context = "\n".join(str(item.value) for item in at.main.markdown)
    assert "planning-envelope-strip" in context
    assert "7 days · 168 h" in context
    assert "168 × 60 min" in context
    assert "Active planning envelope" in context


def test_legacy_contingency_route_opens_consolidated_routing_workspace():
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    nav_to(at, common.PLAN_CONTINGENCIES_PAGE)
    assert not at.exception, at.exception
    captions = "\n".join(str(item.value) for item in at.main.caption)
    assert "weekly review and removal" in captions
    assert "no manual scope IDs or minute-offset table" in captions
    assert not at.get("data_editor")
    context = "\n".join(str(item.value) for item in at.main.markdown)
    assert "Active planning envelope" in context
    assert "Controls</small>0 windows" in context


def test_midweek_unexpected_event_template_queues_exactly_once():
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    at.session_state["plan"] = PlanResult(status="FEASIBLE")
    at.session_state["sim"] = None
    simulation_page = next(
        label for label in common.NAV_GROUPS["Advanced"]
        if label.split(" ", 1)[0] == "7")
    nav_to(at, simulation_page)
    assert not at.exception, at.exception
    button = next(item for item in at.main.button
                  if item.label == "Queue mid-week failure test")
    button.click().run()
    assert not at.exception, at.exception
    events = [event for event in at.session_state["sim"].events
              if event.id == MIDWEEK_TEST_EVENT_ID]
    assert len(events) == 1
    assert events[0].minute == 5_040
    assert events[0].duration_minutes == 120
