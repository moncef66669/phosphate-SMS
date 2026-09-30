"""Focused operator contract for the Job Schedule page."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from conftest import nav_to  # noqa: E402
from mph_sim.domain import (Job, JobBucketFlow, JobExecutionWindow,  # noqa: E402
                            PlanResult)
from ui.pages_planning import (clip_schedule_to_window,  # noqa: E402
                               schedule_day_windows)
APP = str(ROOT / "app.py")
SCHEDULE_PAGE = "9 · Job Schedule"


def _fresh_with_plan(plan: PlanResult) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    at.session_state["plan"] = plan
    at.session_state["sim"] = None
    nav_to(at, SCHEDULE_PAGE)
    assert not at.exception, at.exception
    return at


def _schedule_spec(at: AppTest) -> dict:
    specs = [json.loads(element.proto.spec)
             for element in at.get("plotly_chart")]
    return next(
        spec for spec in specs
        if spec.get("layout", {}).get("yaxis", {}).get("categoryarray")
        and (
            any("base" in trace for trace in spec.get("data", []))
            or all(
                trace.get("type") == "scatter" and not trace.get("name")
                for trace in spec.get("data", [])
            )
        )
    )


def test_schedule_defaults_to_current_equipment_and_reference_is_opt_in():
    plan = PlanResult(
        status="FEASIBLE",
        machine_dispositions={
            "ST01-A": {
                "code": "NO_DEMAND",
                "reason": "no demand",
                "classification": "legitimate idle",
                "solver_controlled": True,
            },
        })
    at = _fresh_with_plan(plan)
    toggle = next(item for item in at.main.toggle
                  if item.label == "Show reference/future equipment")
    assert toggle.value is False
    primary = _schedule_spec(at)
    # Benguerir raw-yard machines are now active planning resources. Use the
    # documented Louta 102MG assets for the reference-only disclosure check.
    assert "LOU · 102MG-ST-01" not in \
        primary["layout"]["yaxis"]["categoryarray"]

    toggle.set_value(True)
    at.run()
    assert not at.exception, at.exception
    disclosed = _schedule_spec(at)
    assert "LOU · 102MG-ST-01" in \
        disclosed["layout"]["yaxis"]["categoryarray"]


def test_schedule_offers_full_run_and_each_exact_calendar_day(s3):
    windows = schedule_day_windows(s3)
    assert len(windows) == 7
    assert windows[0][0] == "Day 1 · Mon 13 Jul 2026"
    assert windows[-1][0] == "Day 7 · Sun 19 Jul 2026"
    assert windows[0][1] == pd.Timestamp("2026-07-13 00:00:00")
    assert windows[-1][2] == pd.Timestamp("2026-07-20 00:00:00")

    at = _fresh_with_plan(PlanResult(status="FEASIBLE"))
    selector = next(
        item for item in at.main.selectbox
        if item.label == "Displayed period")
    assert selector.value == "Full run · 7 days"
    assert selector.options[1:] == [label for label, _start, _end in windows]


def test_daily_schedule_view_clips_and_prorates_a_cross_midnight_job():
    frame = pd.DataFrame([{
        "start": pd.Timestamp("2026-07-13 18:00:00"),
        "end": pd.Timestamp("2026-07-14 06:00:00"),
        "start_time": "2026-07-13 18:00",
        "end_time": "2026-07-14 06:00",
        "kind": "reclaim",
        "tonnage_t": 1_200.0,
        "output_tonnage_t": 960.0,
        "tsm_equivalent_t": 900.0,
        "tsm_label": "TSM equivalent",
        "tonnage_label": "1.2 kt",
        "tsm_tonnage_label": "900 t TSM equivalent",
        "execution_duration_min": 720,
    }])

    clipped = clip_schedule_to_window(
        frame,
        pd.Timestamp("2026-07-14 00:00:00"),
        pd.Timestamp("2026-07-15 00:00:00"),
    )

    assert len(clipped) == 1
    row = clipped.iloc[0]
    assert row["start"] == pd.Timestamp("2026-07-14 00:00:00")
    assert row["end"] == pd.Timestamp("2026-07-14 06:00:00")
    assert row["execution_duration_min"] == 360
    assert row["tonnage_t"] == 600.0
    assert row["output_tonnage_t"] == 480.0
    assert row["tsm_equivalent_t"] == 450.0
    assert row["tonnage_label"] == "600 t"


def test_schedule_table_shows_minute_timing_and_process_context():
    path = ["C1", "L-H-A"]
    job = Job(
        "UI-J1", "ST01-A", "stack", "HP-01", grade="TBT",
        rate_tph=300.0, tonnage_t=300.0, start_bucket=0, end_bucket=0,
        source_space="LT-P1", target_space="HP-01",
        process_stage="M16 reception", depends_on_stage="LOU-WASH-L1",
        dependency_delay_min=30, purpose="configured replenishment",
        path=path,
        bucket_flows=[JobBucketFlow(0, 300.0, path)],
        execution_windows=[JobExecutionWindow(
            0, 0, 0, 12, 300.0, 1500.0, status="VALIDATED")],
    )
    at = _fresh_with_plan(PlanResult(status="FEASIBLE", jobs=[job]))
    schedule = next(
        frame.value for frame in at.main.dataframe
        if "job" in frame.value.columns)
    assert {
        "start_time", "end_time", "source", "target", "path",
        "process_stage", "depends_on", "dependency_delay_min",
        "material_class", "mass_basis", "tsm_equivalent_t", "tsm_label",
    } <= set(schedule.columns)
    row = schedule.iloc[0]
    assert row["start_time"].endswith("00:00")
    assert row["end_time"].endswith("00:12")
    assert row["tonnage_t"] == 300.0
    assert row["mass_basis"] == "physical/as-handled"
    assert row["material_class"] == "washed"
    assert row["tsm_equivalent_t"] == 247.5
    assert row["tsm_label"] == "TSM equivalent"
    assert row["rate_tph"] == 1500.0
    assert row["source"] == "LT-P1"
    assert row["target"] == "HP-01"
    assert row["process_stage"] == "M16 reception"
    assert row["depends_on"] == "LOU-WASH-L1"


def test_schedule_warns_that_a_token_allocation_is_real_not_chart_noise():
    job = Job(
        "UI-TOKEN", "ST01-A", "stack", "HP-01", grade="TBT",
        rate_tph=1.0, tonnage_t=1.0, start_bucket=0, end_bucket=0,
        bucket_flows=[JobBucketFlow(0, 1.0, [])],
    )
    at = _fresh_with_plan(PlanResult(status="FEASIBLE", jobs=[job]))

    assert any(
        "below the configured minimum parcel" in warning.value
        and "not a chart artifact" in warning.value
        for warning in at.main.warning
    )
