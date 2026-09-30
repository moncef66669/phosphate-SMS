# -*- coding: utf-8 -*-
"""Focused, solver-free contracts for Monitor chart readability."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets  # noqa: E402
from ui import pages_overview  # noqa: E402


def test_inventory_overview_aggregates_only_physical_stockpiles():
    scenario = datasets.end_to_end()
    inventory = {
        space.id: [float(space.mass_t), float(space.mass_t) + 10.0]
        for space in scenario.spaces
    }
    for space in scenario.spaces:
        if space.area in {"mine-interface", "process-interface"}:
            inventory[space.id] = [9_999_999.0, 9_999_999.0]

    totals = pages_overview._physical_inventory_by_site(
        scenario, inventory)

    assert set(totals) == {"BENGUERIR", "LOUTA", "MPH"}
    for site, values in totals.items():
        expected = sum(
            space.mass_t
            for space in scenario.spaces
            if space.site == site
            and space.area not in {"mine-interface", "process-interface"}
        )
        assert values[0] == pytest.approx(expected)

    figure = pages_overview._inventory_site_figure(
        scenario, inventory)
    assert len(figure.data) == 3
    assert {trace.name for trace in figure.data} == {
        "Benguerir", "Louta", "MPH"}


def test_demand_delivery_chart_has_two_readable_horizontal_traces():
    kpis = {
        "per_destination": {
            "PAP-1": {"demand_t": 36_000.0, "delivered_t": 35_000.0},
            "DRY-FEED": {"demand_t": 19_200.0, "delivered_t": 19_200.0},
            "BG-TRAIN-PROD": {"demand_t": 0.0, "delivered_t": 0.0},
        }
    }

    figure = pages_overview._demand_delivery_figure(kpis)

    assert [trace.name for trace in figure.data] == [
        "Demand", "Delivered"]
    assert all(trace.orientation == "h" for trace in figure.data)
    assert figure.layout.barmode == "group"
    assert "BG-TRAIN-PROD" in list(figure.data[0].y)


def test_quality_deviation_chart_is_single_horizontal_summary():
    figure = pages_overview._offspec_figure({
        "PAP-1": [0.0, 0.0],
        "DRY-FEED": [50_000.0, 25_000.0],
    })

    assert len(figure.data) == 1
    assert figure.data[0].orientation == "h"
    assert figure.data[0].showlegend is False
    assert list(figure.data[0].x) == [75_000.0]
    assert list(figure.data[0].y) == ["DRY-FEED"]


def test_quality_deviation_chart_has_clear_on_spec_empty_state():
    figure = pages_overview._offspec_figure({
        "PAP-1": [0.0, 0.0],
        "DRY-FEED": [0.0],
    })

    assert not figure.data
    assert figure.layout.annotations[0].text == \
        "No planned soft-quality deviation"
