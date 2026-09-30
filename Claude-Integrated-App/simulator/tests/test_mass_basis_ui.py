"""Focused stage-aware mass labels for schedule and stockyard UI."""
from __future__ import annotations

import copy

import pytest

from mph_sim import datasets
from mph_sim.domain import Job
from ui.theme import gantt_figure, jobs_to_gantt_df


def _job(*, job_id: str, space: str, machine: str, grade: str,
         output_grade: str = "") -> Job:
    return Job(
        job_id, machine, "reclaim", space,
        grade=grade, output_grade=output_grade,
        rate_tph=1_000, tonnage_t=1_000,
        start_bucket=0, end_bucket=0,
    )


def test_raw_job_needs_explicit_output_campaign_for_expected_tsm():
    scenario = datasets.end_to_end()
    explicit = _job(
        job_id="RAW-EXPLICIT", space="BG-RAW-1",
        machine="BG-RAW-BWR", grade="RAW-BT",
        output_grade="TBT",
    )
    unknown = _job(
        job_id="RAW-UNKNOWN", space="BG-RAW-1",
        machine="BG-RAW-BWR", grade="RAW-BT",
    )

    explicit_row = jobs_to_gantt_df(scenario, [explicit]).iloc[0]
    unknown_row = jobs_to_gantt_df(scenario, [unknown]).iloc[0]

    assert explicit_row.mass_basis == "physical/as-handled"
    assert explicit_row.material_class == "raw_screened"
    assert explicit_row.tsm_equivalent_t == pytest.approx(551.232)
    assert explicit_row.tsm_label == "expected TSM"
    assert unknown_row.tsm_equivalent_t is None
    assert unknown_row.tsm_label == ""

    figure = gantt_figure(
        jobs_to_gantt_df(scenario, [explicit]),
        color_by="kind", group_by="machine")
    hover = next(trace for trace in figure.data if trace.type == "bar").hovertext[0]
    assert len(hover.split("<br>")) == 4
    assert "RAW-BT → TBT" in hover
    assert "1.0 kt physical" in hover
    assert "551 t expected TSM" in hover
    assert "1,000 physical t/h" in hover


def test_dry_stage_displays_physical_mass_as_tsm_without_a_second_loss():
    scenario = copy.deepcopy(datasets.end_to_end())
    scenario.space("BG-P1").material_class = "dry"
    job = _job(
        job_id="DRY", space="BG-P1", machine="RC2", grade="TBT")

    row = jobs_to_gantt_df(scenario, [job]).iloc[0]

    assert row.tsm_equivalent_t == 1_000
    assert row.tsm_label == "TSM"
    assert row.tsm_tonnage_label == "1.0 kt TSM"
