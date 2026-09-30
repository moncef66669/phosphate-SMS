# -*- coding: utf-8 -*-
"""S3 default, machine disclosure and always-visible Gantt tonnage labels."""
import datetime as dt
import copy

import pandas as pd

from mph_sim import datasets
from mph_sim.domain import Job, JobExecutionWindow, PlanResult
from mph_sim.validation import validate_scenario
from ui.pages_planning import schedule_display_frame
from ui.theme import (GREEN, gantt_figure, jobs_to_gantt_df,
                      timeline_machine_register)


def test_s3_timeline_defaults_to_solver_controlled_equipment():
    sc = datasets.end_to_end()
    reg = timeline_machine_register(sc)
    expected = {
        machine.id for machine in sc.machines
        if getattr(machine, "planning_enabled", True)
    }
    assert set(reg.machine) == expected
    assert not reg.display_reference.any()
    assert "BG-RAW-ST-1" not in set(reg.machine)


def test_reference_and_future_equipment_is_explicit_opt_in():
    sc = datasets.end_to_end()
    current = timeline_machine_register(sc)
    disclosed = timeline_machine_register(sc, include_reference=True)
    assert set(current.machine) <= set(disclosed.machine)
    for machine_id in ("BG-RAW-ST-1", "BG-RAW-ST-2"):
        row = disclosed[disclosed.machine == machine_id].iloc[0]
        assert bool(row.display_reference)
        assert "reference equipment" in row.row_status


def test_empty_timeline_rows_use_export_safe_native_datetimes():
    sc = datasets.end_to_end()
    reg = timeline_machine_register(sc)
    job = Job("J1", "ST01-A", "stack", "HP-01", grade="TBT",
              rate_tph=2600.0, tonnage_t=2600.0,
              start_bucket=0, end_bucket=0)
    fig = gantt_figure(jobs_to_gantt_df(sc, [job]), color_by="kind",
                       group_by="machine", row_register=reg)
    blank_markers = [trace for trace in fig.data
                     if trace.type == "scatter" and trace.hoverinfo == "skip"]
    assert blank_markers
    assert all(isinstance(trace.x[0], dt.datetime)
               for trace in blank_markers)
    assert list(fig.layout.yaxis.categoryarray)[:3] == [
        "MPH · ST01-A", "MPH · ST01-B", "MPH · ST01-C"]


def test_job_dataframe_and_gantt_carry_visible_compact_tonnage():
    sc = datasets.end_to_end()
    job = Job("J-TON", "RC2", "reclaim", "BG-P1", dest_id="C2",
              grade="TBT", rate_tph=2600, tonnage_t=2600,
              start_bucket=0, end_bucket=0)
    df = jobs_to_gantt_df(sc, [job])
    assert df.iloc[0].tonnage_label == "2.6 kt"
    assert df.iloc[0].mass_basis == "physical/as-handled"
    assert df.iloc[0].material_class == "washed"
    assert df.iloc[0].tsm_equivalent_t == 2145.0
    assert df.iloc[0].tsm_tonnage_label == "2.1 kt TSM equivalent"
    assert df.iloc[0].source == "BG-P1"
    assert df.iloc[0].target == "C2"
    assert df.iloc[0].process_stage == "reclaim"
    assert df.iloc[0].depends_on == "—"
    assert df.iloc[0].start_time.endswith("00:00")
    assert df.iloc[0].end_time.endswith("01:00")
    visible = schedule_display_frame(df)
    assert {
        "start_time", "end_time", "source", "target", "path",
        "process_stage", "depends_on",
    } <= set(visible.columns)

    reg = timeline_machine_register(sc)
    fig = gantt_figure(df, color_by="kind", group_by="machine",
                       row_register=reg, show_tonnage=True)
    visible_text = [str(t) for trace in fig.data
                    for t in (list(trace.text)
                              if getattr(trace, "text", None) is not None
                              else [])]
    assert "2.6 kt" in visible_text


def test_material_job_hover_contains_only_four_operator_essentials():
    sc = datasets.end_to_end()
    job = Job(
        "J-HOVER", "RC2", "reclaim", "BG-P1", dest_id="C2",
        grade="TBT", rate_tph=2600, tonnage_t=2600,
        start_bucket=0, end_bucket=0)
    df = jobs_to_gantt_df(sc, [job])
    # Extra engineering metadata may remain available to exports, but must
    # not leak into the operator-facing chart hover.
    df["evidence"] = "SECRET-EVIDENCE"
    df["internal_note"] = "SECRET-INTERNAL"

    fig = gantt_figure(df, color_by="kind", group_by="machine")
    [trace] = [item for item in fig.data if item.type == "bar"]
    [hover] = list(trace.hovertext)

    assert trace.hovertemplate == "%{hovertext}<extra></extra>"
    assert hover.split("<br>") == [
        "<b>Reclaim</b> · J-HOVER",
        "BG-P1 → C2",
        "TBT · 2.6 kt physical · 2.1 kt TSM equivalent "
        "@ 2,600 physical t/h",
        "2026-07-13 00:00 → 2026-07-13 01:00",
    ]
    assert "SECRET-EVIDENCE" not in hover
    assert "SECRET-INTERNAL" not in hover
    assert "solver_" not in hover
    assert "timing_" not in hover


def test_corridor_dispatch_is_presented_only_as_physical_reclaim_operation():
    sc = datasets.end_to_end()
    job = Job(
        "J-C1", "140LG-RC-01", "dispatch", "LT-P1", dest_id="C1",
        grade="TBT", rate_tph=2600, tonnage_t=1400,
        start_bucket=0, end_bucket=0)

    df = jobs_to_gantt_df(sc, [job])
    row = df.iloc[0]

    assert row.kind == "reclaim"
    assert "movement" not in df.columns
    assert row.source == "LT-P1"
    assert row.target == "C1"
    assert row.purpose == "Supply C1 corridor"
    assert row.tonnage_t == 1400
    assert row.tonnage_label == "1.4 kt"


def test_positioning_is_green_non_material_time_for_the_next_job():
    sc = datasets.mph_phosphate()
    first = Job(
        "J-A", "RC01-A", "reclaim", "HP-01", dest_id="PAP-1",
        grade="TBT", rate_tph=1500, tonnage_t=250,
        start_bucket=0, end_bucket=0, start_x=0, stop_x=20,
        execution_windows=[JobExecutionWindow(
            0, 0, 0, 10, 250, 1500, status="VALIDATED")])
    second = Job(
        "J-B", "RC01-A", "reclaim", "HP-05", dest_id="PAP-1",
        grade="TBT", rate_tph=1500, tonnage_t=250,
        start_bucket=2, end_bucket=2, start_x=280, stop_x=300,
        execution_windows=[JobExecutionWindow(
            2, 2, 120, 10, 250, 1500, status="VALIDATED")])

    df = jobs_to_gantt_df(
        sc, [first, second], include_positioning=True)
    [positioning] = df[df.kind == "positioning"].to_dict("records")

    assert positioning["activity_for_job"] == "J-B"
    assert positioning["source"] == "HP-01"
    assert positioning["target"] == "HP-05"
    assert positioning["translation_distance_m"] == 280
    assert positioning["translation_duration_min"] == 10
    assert positioning["setup_duration_min"] == 10
    assert positioning["positioning_duration_min"] == 20
    assert positioning["start_time"].endswith("01:40")
    assert positioning["end_time"].endswith("02:00")
    assert positioning["gap_status"] == "RESERVED_ESTIMATE"
    assert positioning["tonnage_label"] == "↔ 20 min"
    assert pd.isna(positioning["tonnage_t"])
    assert pd.isna(positioning["rate_tph"])

    visible = schedule_display_frame(df)
    assert {
        "activity_for_job", "from_position", "to_position",
        "translation_distance_m", "translation_duration_min",
        "setup_duration_min", "positioning_duration_min",
        "positioning_basis", "gap_status",
    } <= set(visible.columns)

    fig = gantt_figure(df, color_by="kind", group_by="machine")
    colors = {trace.name: trace.marker.color for trace in fig.data
              if getattr(trace, "name", "")}
    assert colors["positioning"] == GREEN
    assert "dispatch" not in colors
    positioning_trace = next(
        trace for trace in fig.data if trace.name == "positioning")
    [positioning_hover] = list(positioning_trace.hovertext)
    assert positioning_trace.hovertemplate == \
        "%{hovertext}<extra></extra>"
    assert positioning_hover.split("<br>") == [
        "<b>Positioning</b> · for J-B",
        "HP-01 → HP-05",
        "20 min · travel 10 + setup 10",
        "2026-07-13 01:40 → 2026-07-13 02:00",
    ]


def test_positioning_inputs_reject_invalid_speed_and_setup_time():
    sc = copy.deepcopy(datasets.mph_phosphate())
    sc.machine("RC01-A").travel_speed_m_min = 0
    sc.machine("RC01-B").setup_min = -1
    sc.machine("ST01-A").travel_speed_m_min = float("nan")
    sc.machine("ST01-B").setup_min = float("inf")

    codes = {(issue.code, issue.entity)
             for issue in validate_scenario(sc)}

    assert ("BAD_TRAVEL_SPEED", "RC01-A") in codes
    assert ("BAD_SETUP_TIME", "RC01-B") in codes
    assert ("BAD_TRAVEL_SPEED", "ST01-A") in codes
    assert ("BAD_SETUP_TIME", "ST01-B") in codes


def test_zero_job_gantt_retains_current_rows_and_structured_reasons():
    sc = datasets.end_to_end()
    plan = PlanResult(
        status="FEASIBLE",
        machine_dispositions={
            "ST01-A": {
                "code": "NO_DEMAND",
                "reason": "no demand; source evidence TBC",
                "classification": "legitimate idle",
                "solver_controlled": True,
            },
        })
    register = timeline_machine_register(sc, plan)
    fig = gantt_figure(
        jobs_to_gantt_df(sc, []), color_by="kind", group_by="machine",
        row_register=register)
    annotations = {str(a.y): str(a.text) for a in fig.layout.annotations
                   if getattr(a, "yref", "") == "y"}
    assert set(register.machine_label) <= set(annotations)
    assert "NO_DEMAND" in annotations["MPH · ST01-A"]
    assert "pending" in annotations["MPH · ST01-A"]
    assert "TBC" not in annotations["MPH · ST01-A"].upper()
    assert "no jobs" not in {
        str(annotation.text).lower()
        for annotation in fig.layout.annotations
        if getattr(annotation, "yref", "") != "y"
    }
    blank_markers = [
        trace for trace in fig.data
        if trace.type == "scatter" and trace.hoverinfo == "skip"]
    assert blank_markers
    assert all(isinstance(trace.x[0], dt.datetime)
               for trace in blank_markers)
