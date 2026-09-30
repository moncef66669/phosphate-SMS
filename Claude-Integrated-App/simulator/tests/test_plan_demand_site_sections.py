"""Focused contract tests for the site-organized Plan demand editor."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets  # noqa: E402
from ui.pages_data import (  # noqa: E402
    _daily_demand_rows_by_site,
    _demand_rows_by_site,
    _demand_site_summaries,
    _grade_display_maps,
    page_demand,
)


def test_demand_sections_cover_each_unit_once_in_operator_site_order():
    scenario = datasets.end_to_end()

    sections = _demand_rows_by_site(scenario)

    assert [site_id for site_id, _, _, _ in sections] == [
        "BENGUERIR", "LOUTA", "MPH",
    ]
    units = [
        row["Receiving unit"]
        for _, _, _, rows in sections
        for row in rows
    ]
    assert len(units) == len(set(units)) == len(scenario.destinations)
    destination_site = {
        destination.id: destination.site
        for destination in scenario.destinations
    }
    assert all(
        destination_site[row["Receiving unit"]] == site_id
        for site_id, _, _, rows in sections
        for row in rows
    )


def test_demand_page_does_not_own_the_global_scenario_selector():
    source = inspect.getsource(page_demand)

    assert "render_planning_scenario_selector" not in source
    assert source.count('st.button("Apply weekly demand"') == 1


def test_site_cards_total_weekly_demand_and_group_it_by_grade():
    scenario = datasets.end_to_end()
    sections = _daily_demand_rows_by_site(scenario)

    summaries = _demand_site_summaries(sections)

    assert [summary["site_id"] for summary in summaries] == [
        "BENGUERIR", "LOUTA", "MPH",
    ]
    assert sum(float(summary["total_t"]) for summary in summaries) == \
        pytest.approx(sum(
            row["Campaign quantity [t]"]
            for _, _, _, rows in sections for row in rows
        ))
    assert all(float(summary["total_t"]) > 0 for summary in summaries)


def test_washed_grade_labels_receive_display_only_w_suffix():
    scenario = datasets.end_to_end()

    display_to_grade, grade_to_display = _grade_display_maps(scenario)

    assert grade_to_display["TBT"] == "TBT-w"
    assert display_to_grade["TBT-w"] == "TBT"
    assert grade_to_display["TBT-HIGH-CD"] == "TBT-HIGH-CD-w"
    assert grade_to_display["RAW-TBT"] == "RAW-TBT"
