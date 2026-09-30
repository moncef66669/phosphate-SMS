# -*- coding: utf-8 -*-
"""Focused contract for the two-page operator Monitor workspace."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from conftest import nav_to  # noqa: E402
from mph_sim.domain import PlanResult  # noqa: E402
from ui import common  # noqa: E402


APP = str(ROOT / "app.py")
EXPECTED_SIDEBAR_PAGES = [
    "KPI flow diagram and Job scheduling",
    "Machine coverage",
]
COMBINED_HEADINGS = [
    "KPIs",
    "Global Stockyard Layout & Flow",
    "Job Schedule",
]
COVERAGE_HEADINGS = ["Machine Coverage & Usage"]
DEEP_LINKS = {
    "1": common.MONITOR_LANDING_PAGE,
    "9": common.MONITOR_LANDING_PAGE,
    "15": common.MONITOR_LANDING_PAGE,
    "16": common.MONITOR_MACHINE_COVERAGE_PAGE,
}


def _fresh() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    assert not at.exception, at.exception
    return at


def _headings(at: AppTest) -> list[str]:
    return [str(item.value) for item in at.main.subheader]


def test_sidebar_exposes_exactly_two_monitor_pages():
    assert common.VISIBLE_NAV_GROUPS["Monitor"] == [
        common.MONITOR_LANDING_PAGE,
        common.MONITOR_MACHINE_COVERAGE_PAGE,
    ]
    assert common.page_title(common.MONITOR_LANDING_PAGE) == \
        EXPECTED_SIDEBAR_PAGES[0]
    assert common.page_title(common.MONITOR_MACHINE_COVERAGE_PAGE) == \
        EXPECTED_SIDEBAR_PAGES[1]

    at = _fresh()
    nav_to(at, common.MONITOR_LANDING_PAGE)

    assert at.sidebar.radio[0].value == "Monitor"
    assert at.sidebar.radio[1].options == EXPECTED_SIDEBAR_PAGES
    assert at.sidebar.radio[1].value == common.MONITOR_LANDING_PAGE
    assert not at.main.tabs
    assert _headings(at) == COMBINED_HEADINGS


def test_machine_coverage_is_the_second_monitor_page():
    at = _fresh()
    nav_to(at, common.MONITOR_MACHINE_COVERAGE_PAGE)

    assert not at.exception, at.exception
    assert at.sidebar.radio[0].value == "Monitor"
    assert at.sidebar.radio[1].options == EXPECTED_SIDEBAR_PAGES
    assert at.sidebar.radio[1].value == \
        common.MONITOR_MACHINE_COVERAGE_PAGE
    assert not at.main.tabs
    assert _headings(at) == COVERAGE_HEADINGS


def test_combined_monitor_temporarily_hides_performance_graphs():
    at = _fresh()
    at.session_state["plan"] = PlanResult(status="FEASIBLE")
    at.session_state["sim"] = None
    nav_to(at, common.MONITOR_LANDING_PAGE)

    visible_markdown = {
        str(element.value)
        for element in at.main.markdown
        if str(element.value).strip()
    }
    visible_text = "\n".join(visible_markdown)
    assert "Planning performance" not in visible_text
    chart_titles = {
        str(json.loads(element.proto.spec).get(
            "layout", {}).get("title", {}).get("text", ""))
        for element in at.get("plotly_chart")
    }
    assert not {
        "Physical stock inventory by site",
        "Demand and planned delivery",
        "Planned soft-quality deviation",
        "Delivered quality against specification",
    } & chart_titles


@pytest.mark.parametrize(
    ("page_id", "expected_visible_page"),
    list(DEEP_LINKS.items()),
)
def test_monitor_deep_links_open_the_intended_visible_page(
        page_id: str, expected_visible_page: str):
    route = common.page_label_from_qp(page_id)
    assert route is not None
    assert common.visible_page_label(route) == expected_visible_page

    at = AppTest.from_file(APP, default_timeout=180)
    at.query_params["page"] = page_id
    at.run()

    assert not at.exception, at.exception
    assert at.sidebar.radio[0].value == "Monitor"
    assert at.sidebar.radio[1].options == EXPECTED_SIDEBAR_PAGES
    assert at.sidebar.radio[1].value == expected_visible_page
    assert not at.main.tabs
    expected_headings = (
        COVERAGE_HEADINGS
        if expected_visible_page == common.MONITOR_MACHINE_COVERAGE_PAGE
        else COMBINED_HEADINGS
    )
    assert _headings(at) == expected_headings
