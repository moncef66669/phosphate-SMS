# -*- coding: utf-8 -*-
"""Focused contracts for Machine Coverage planning usage.

The module deliberately uses synthetic plans: page-level usage reporting
must not require a costly optimisation solve to test.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from conftest import nav_to  # noqa: E402
from mph_sim import datasets  # noqa: E402
from mph_sim.domain import Job, JobExecutionWindow, PlanResult  # noqa: E402
from ui import common  # noqa: E402
from ui.theme import (fleet_usage_kpis, machine_usage_figure,  # noqa: E402
                      machine_usage_summary)


APP = str(ROOT / "app.py")
MACHINE_COVERAGE_PAGE = next(
    label for label in common.ALL_PAGE_LABELS
    if label.endswith("Machine Coverage")
)


def _positioned_jobs() -> list[Job]:
    """Two validated work windows with a valid 20-minute move between them."""
    return [
        Job(
            "J-A", "RC01-A", "reclaim", "HP-01", dest_id="PAP-1",
            grade="TBT", rate_tph=1500, tonnage_t=250,
            start_bucket=0, end_bucket=0, start_x=0, stop_x=20,
            execution_windows=[JobExecutionWindow(
                0, 0, 0, 10, 250, 1500, status="VALIDATED")],
        ),
        Job(
            "J-B", "RC01-A", "reclaim", "HP-05", dest_id="PAP-1",
            grade="TBT", rate_tph=1500, tonnage_t=250,
            start_bucket=2, end_bucket=2, start_x=280, stop_x=300,
            execution_windows=[JobExecutionWindow(
                2, 2, 120, 10, 250, 1500, status="VALIDATED")],
        ),
    ]


def _conflicting_position_jobs() -> list[Job]:
    """Two valid work windows whose intervening move does not fit."""
    jobs = _positioned_jobs()
    jobs[1].id = "J-CONFLICT"
    jobs[1].tonnage_t = 125
    jobs[1].start_bucket = 0
    jobs[1].end_bucket = 0
    jobs[1].execution_windows = [JobExecutionWindow(
        0, 0, 15, 5, 125, 1500, status="VALIDATED")]
    return jobs


def _machine_row(frame: pd.DataFrame, machine_id: str) -> pd.Series:
    rows = frame[frame["machine_id"] == machine_id]
    assert len(rows) == 1, machine_id
    return rows.iloc[0]


def _fresh_on_coverage(plan: PlanResult | None = None) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    assert not at.exception, at.exception
    if plan is not None:
        at.session_state["plan"] = plan
        at.session_state["sim"] = None
    nav_to(at, MACHINE_COVERAGE_PAGE)
    assert not at.exception, at.exception
    return at


def _frame_with_columns(at: AppTest, required: set[str]) -> pd.DataFrame:
    """Find a displayed frame by case-insensitive public column names."""
    for element in at.main.dataframe:
        frame = element.value
        names = {str(column).strip().lower() for column in frame.columns}
        if required <= names:
            return frame
    available = [
        [str(column) for column in element.value.columns]
        for element in at.main.dataframe
    ]
    raise AssertionError(
        f"no dataframe has columns {sorted(required)}; found {available}")


def _display_number(value) -> float:
    """Parse the deliberately formatted values in the compact public table."""
    return float(
        str(value).replace(",", "").replace("%", "").split()[0])


def test_usage_uses_validated_work_and_reserved_positioning_minutes():
    sc = datasets.mph_phosphate()
    plan = PlanResult(status="FEASIBLE", jobs=_positioned_jobs())

    usage = machine_usage_summary(sc, plan)
    row = _machine_row(usage, "RC01-A")

    assert bool(row["usage_applicable"])
    assert row["timing_basis"] == "validated minutes"
    assert row["jobs"] == 2
    assert row["tonnes_t"] == pytest.approx(500.0)
    assert row["reclaim_h"] == pytest.approx(20 / 60, abs=0.001)
    assert row["work_h"] == pytest.approx(20 / 60, abs=0.001)
    assert row["positioning_h"] == pytest.approx(20 / 60, abs=0.001)
    assert row["occupied_h"] == pytest.approx(40 / 60, abs=0.001)
    assert row["available_h"] == pytest.approx(24.0)
    assert row["idle_h"] == pytest.approx(24 - 40 / 60, abs=0.001)
    assert row["usage_pct"] == pytest.approx(2.8)
    assert row["conflict_count"] == 0

    figure = machine_usage_figure(usage)
    traces = {trace.name: trace for trace in figure.data if trace.name}
    assert {"Reclaim work", "Positioning", "Idle available"} <= set(traces)
    reclaim = dict(zip(traces["Reclaim work"].y, traces["Reclaim work"].x))
    positioning = dict(zip(traces["Positioning"].y,
                           traces["Positioning"].x))
    idle = dict(zip(traces["Idle available"].y,
                    traces["Idle available"].x))
    label = row["machine"]
    assert reclaim[label] == pytest.approx(20 / 60, abs=0.001)
    assert positioning[label] == pytest.approx(20 / 60, abs=0.001)
    assert idle[label] == pytest.approx(24 - 40 / 60, abs=0.001)


def test_positioning_conflict_is_reported_but_not_counted_as_usage():
    sc = datasets.mph_phosphate()
    plan = PlanResult(status="FEASIBLE", jobs=_conflicting_position_jobs())

    row = _machine_row(machine_usage_summary(sc, plan), "RC01-A")

    assert row["work_h"] == pytest.approx(15 / 60, abs=0.001)
    assert row["positioning_h"] == pytest.approx(0.0)
    assert row["conflict_h"] == pytest.approx(20 / 60, abs=0.001)
    assert row["conflict_count"] == 1
    assert row["occupied_h"] == pytest.approx(15 / 60, abs=0.001)
    assert row["usage_pct"] == pytest.approx(1.0)


def test_fleet_kpis_reconcile_work_positioning_and_idle_without_conflicts():
    sc = datasets.mph_phosphate()
    result = fleet_usage_kpis(
        sc, PlanResult(status="FEASIBLE", jobs=_positioned_jobs()))

    assert result["valid_plan"] is True
    assert result["used_machines"] == 1
    assert result["fleet_participation_pct"] == pytest.approx(
        100.0 / result["eligible_machines"])
    assert result["available_h"] == pytest.approx(
        result["work_h"] + result["positioning_h"] + result["idle_h"])
    assert result["reconciliation_error_h"] == pytest.approx(0.0)
    assert result["positioning_h"] == pytest.approx(20 / 60, abs=0.001)
    assert result["productive_utilisation_pct"] < \
        result["scheduled_occupation_pct"]
    assert (
        result["productive_utilisation_pct"]
        + result["positioning_loss_pct"]
        + result["idle_pct"]
    ) == pytest.approx(100.0)
    assert result["conflict_h"] == pytest.approx(0.0)


def test_fleet_conflict_exposure_is_separate_from_available_time_identity():
    sc = datasets.mph_phosphate()
    result = fleet_usage_kpis(
        sc, PlanResult(status="FEASIBLE", jobs=_conflicting_position_jobs()))

    assert result["conflict_count"] == 1
    assert result["conflict_h"] == pytest.approx(20 / 60, abs=0.001)
    assert result["available_h"] == pytest.approx(
        result["work_h"] + result["positioning_h"] + result["idle_h"])
    assert result["reconciliation_error_h"] == pytest.approx(0.0)


def test_idle_active_benguerir_is_zero_usage_but_reference_is_not_applicable():
    sc = datasets.end_to_end()
    horizon_h = sc.sim.horizon_buckets * sc.sim.dt_h
    usage = machine_usage_summary(
        sc, PlanResult(status="FEASIBLE", jobs=[]))

    benguerir_machines = {
        "BG-MINE-ST-1", "BG-MINE-ST-2", "BG-MINE-BWR",
        "BG-RAW-ST-1", "BG-RAW-ST-2", "BG-RAW-BWR",
        "ST3", "RC2", "RC3",
    }
    for machine_id in benguerir_machines:
        row = _machine_row(usage, machine_id)
        assert bool(row["usage_applicable"]), machine_id
        assert row["jobs"] == 0
        assert row["work_h"] == pytest.approx(0.0)
        assert row["positioning_h"] == pytest.approx(0.0)
        assert row["usage_pct"] == pytest.approx(0.0)
        assert row["idle_h"] == pytest.approx(horizon_h)

    for machine_id in ("102MG-ST-01", "110MG-RC-01", "102MG-ST-02"):
        reference = _machine_row(usage, machine_id)
        assert not bool(reference["usage_applicable"])
        assert pd.isna(reference["usage_pct"])
        assert pd.isna(reference["work_h"])
        assert pd.isna(reference["positioning_h"])
        assert pd.isna(reference["idle_h"])
        assert reference["status"] in {
            "Reference only", "Outside operating scope"}

    figure = machine_usage_figure(usage)
    public_text = " ".join(
        str(item)
        for trace in figure.data
        for item in (list(trace.text)
                     if getattr(trace, "text", None) is not None else [])
    )
    assert "N/A" in public_text


def test_no_plan_page_still_discloses_machine_coverage():
    at = _fresh_on_coverage()

    assert any(
        str(item.value) == "Machine Coverage & Usage"
        for item in at.main.subheader)
    coverage = _frame_with_columns(
        at, {"machine", "site", "status", "reason"})
    assert any(
        coverage["machine"].astype(str).str.contains("RC01-A",
                                                     regex=False))
    assert any(
        "plan" in str(item.value).lower()
        for item in [*at.main.info, *at.main.caption])


def test_page_shows_usage_kpis_chart_and_compact_operational_table():
    at = _fresh_on_coverage(
        PlanResult(status="FEASIBLE", jobs=_positioned_jobs()))

    assert any(
        str(item.value) == "Machine Coverage & Usage"
        for item in at.main.subheader)
    metric_labels = {str(metric.label).strip().lower()
                     for metric in at.main.metric}
    assert {
        "machines used", "fleet scheduled use", "material work",
        "reserved positioning", "machine-handled tonnes", "conflicts",
    } <= metric_labels

    specs = [
        json.loads(element.proto.spec)
        for element in at.get("plotly_chart")
    ]
    usage_spec = next(
        spec for spec in specs
        if "machine usage" in str(
            spec.get("layout", {}).get("title", {}).get("text", "")
        ).lower()
    )
    trace_names = {
        str(trace.get("name", "")) for trace in usage_spec.get("data", [])
    }
    assert {"Reclaim work", "Positioning", "Idle available"} <= trace_names

    table = _frame_with_columns(
        at, {"machine", "status", "jobs", "usage [%]",
             "work [h]", "positioning [h]", "tonnes [t]"})
    assert len(table.columns) <= 12
    row = table[
        table["machine"].astype(str).str.contains("RC01-A", regex=False)
    ].iloc[0]
    assert _display_number(row["jobs"]) == pytest.approx(2)
    assert _display_number(row["usage [%]"]) == pytest.approx(0.4)
    # Public table rounds hours to one decimal; the pure-helper test above
    # retains the exact minute-level assertion.
    assert _display_number(row["work [h]"]) == pytest.approx(0.3)
    assert _display_number(row["positioning [h]"]) == pytest.approx(0.3)
    assert _display_number(row["tonnes [t]"]) == pytest.approx(500.0)
