"""Focused operator contract for the Job Schedule page."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from conftest import nav_to  # noqa: E402
from mph_sim.domain import (Job, JobBucketFlow, JobExecutionWindow,  # noqa: E402
                            PlanResult)
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
    } <= set(schedule.columns)
    row = schedule.iloc[0]
    assert row["start_time"].endswith("00:00")
    assert row["end_time"].endswith("00:12")
    assert row["tonnage_t"] == 300.0
    assert row["rate_tph"] == 1500.0
    assert row["source"] == "LT-P1"
    assert row["target"] == "HP-01"
    assert row["process_stage"] == "M16 reception"
    assert row["depends_on"] == "LOU-WASH-L1"
