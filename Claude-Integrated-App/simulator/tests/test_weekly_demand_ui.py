"""Focused UI contracts for the seven-day, hourly S0 demand plan."""
from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from conftest import nav_to  # noqa: E402
from mph_sim import datasets  # noqa: E402
from ui import common  # noqa: E402
from ui.pages_data import (  # noqa: E402
    _daily_bucket_ranges,
    _daily_demand_rows_by_site,
    _demand_quantity_t,
    _profile_horizon_total_t,
    _weekly_campaign_entries,
)


APP = str(ROOT / "app.py")


def _demand_page() -> str:
    return next(
        label for label in common.NAV_GROUPS["Plan"]
        if label.split(" ", 1)[0] == "5"
    )


def test_profile_cards_expand_daily_values_to_weekly_totals():
    full_flow = datasets.PLANNING_DEMAND_PROFILES["FULL_FLOW"]
    assert _profile_horizon_total_t(
        full_flow, "bg_train_product_t", "BG-TRAIN-PROD", 7.0
    ) == 168_000.0
    assert _profile_horizon_total_t(
        full_flow, "c2_dispatch_t", "C2", 7.0
    ) == 74_200.0

    varied = dict(full_flow)
    varied["daily_quantity_factors"] = {
        "BG-TRAIN-PROD": (1.0, 0.5, 0.0, 1.5, 1.0, 1.0, 2.0),
    }
    assert _profile_horizon_total_t(
        varied, "bg_train_product_t", "BG-TRAIN-PROD", 7.0
    ) == 168_000.0


def test_weekly_rows_cover_every_unit_on_each_calendar_day():
    scenario = datasets.end_to_end()

    days = _daily_bucket_ranges(scenario)
    assert scenario.sim.horizon_buckets == 168
    assert scenario.sim.dt_h == 1.0
    assert len(days) == 7
    assert all(len(day) == 24 for day in days)

    sections = _daily_demand_rows_by_site(scenario)
    assert [site_id for site_id, _, _, _ in sections] == [
        "BENGUERIR", "LOUTA", "MPH",
    ]
    rows = [row for _, _, _, site_rows in sections for row in site_rows]
    assert len(rows) == len(scenario.destinations) * 7
    for destination in scenario.destinations:
        unit_rows = [
            row for row in rows
            if row["Receiving unit"] == destination.id
        ]
        assert [row["Day"].split(" · ", 1)[0] for row in unit_rows] == [
            f"Day {day}" for day in range(1, 8)
        ]
        assert len({row["Date"] for row in unit_rows}) == 7
        assert sum(row["Campaign quantity [t]"] for row in unit_rows) == \
            _demand_quantity_t(scenario, destination.id)


def test_pap_accepts_split_g10_windows_and_rejects_other_grades():
    scenario = datasets.end_to_end()
    rows = [
        deepcopy(row)
        for _, _, _, site_rows in _daily_demand_rows_by_site(scenario)
        for row in site_rows
    ]
    first_date = scenario.sim.bucket_dt(0).strftime("%Y-%m-%d")
    first = next(
        row for row in rows
        if row["Receiving unit"] == "PAP-1" and row["Date"] == first_date
    )
    first["Campaign quantity [t]"] = 18_000.0
    first["Start hour"] = 0.0
    first["End hour"] = 12.0
    second = deepcopy(first)
    second["Requested grade"] = "G10"
    second["Start hour"] = 12.0
    second["End hour"] = 24.0
    rows.append(second)
    grade_map = {grade.id: grade.id for grade in scenario.grades}
    grade_map["—"] = ""

    scenario.demand = _weekly_campaign_entries(
        scenario, rows, grade_map)

    assert scenario.demand_grade("PAP-1", 0) == "G10"
    assert scenario.demand_grade("PAP-1", 11) == "G10"
    assert scenario.demand_grade("PAP-1", 12) == "G10"
    assert scenario.demand_grade("PAP-1", 23) == "G10"
    assert sum(scenario.demand_tph("PAP-1", bucket)
               for bucket in range(24)) == pytest.approx(36_000.0)

    second["Start hour"] = 11.0
    with pytest.raises(ValueError, match="overlap"):
        _weekly_campaign_entries(scenario, rows, grade_map)

    second["Start hour"] = 12.0
    second["Requested grade"] = "BT"
    with pytest.raises(ValueError, match="only accepts the G10"):
        _weekly_campaign_entries(scenario, rows, grade_map)


def test_demand_page_is_one_date_first_weekly_table_and_commits_it():
    """The normal planning surface is a weekly order book, not site tabs."""
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    nav_to(at, _demand_page())
    assert not at.exception, at.exception

    # Presets are a small toolbar action.  Large scenario-summary cards no
    # longer push the actual dated demand below the fold.
    preset_selectors = [
        item for item in at.main.selectbox if item.label == "Demand preset"
    ]
    assert len(preset_selectors) == 1
    assert len([
        button for button in at.main.button if button.label == "Load preset"
    ]) == 1
    assert not {
        "7-day BG train order",
        "7-day Benguerir C2 allocation",
        "Weekly BG washing campaign",
        "Weekly Louta stocked route",
    } & {metric.label for metric in at.main.metric}

    weekly_editors = [
        frame for frame in at.main.dataframe
        if frame.key == "weekly_demand_editor"
    ]
    assert len(weekly_editors) == 1
    weekly_table = weekly_editors[0].value
    expected_columns = [
        "Date", "Site", "Receiving unit", "Requested grade",
        "Campaign quantity [t]",
    ]
    assert list(weekly_table.columns[:len(expected_columns)]) == \
        expected_columns
    scenario_before_apply = at.session_state["scenario"]
    assert len(weekly_table) == len(scenario_before_apply.destinations) * 7 == 70
    assert weekly_table["Date"].nunique() == 7
    assert set(weekly_table["Site"]) == {"Benguerir", "Louta", "MPH"}
    assert weekly_table.groupby("Receiving unit").size().eq(7).all()
    assert weekly_table[[
        "Date", "Site", "Receiving unit", "Requested grade",
        "Campaign quantity [t]",
    ]].notna().all().all()

    apply_demand = next(
        button for button in at.main.button
        if button.label == "Apply weekly demand")
    apply_demand.click()
    at.run()
    assert not at.exception, at.exception
    scenario = at.session_state["scenario"]
    assert scenario.config["ACTIVE_PLANNING_DEMAND_PROFILE"] == \
        "CUSTOM_WEEKLY"
    assert len(scenario.demand) == (
        len(scenario.destinations) * scenario.sim.horizon_buckets)
    assert all(
        entry.requested_grade or entry.rate_tph == 0
        for entry in scenario.demand
    )
    selector = next(
        item for item in at.main.selectbox
        if item.label == "Demand preset")
    assert selector.value == "CUSTOM_WEEKLY"
